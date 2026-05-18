"""Verify that firmware-callable endpoints bypass the GitHub OAuth gate.

Background
----------
The Plant Grow Unit firmware on the Raspberry Pi cannot authenticate as a
human user — it has no OAuth credentials. Once the MLSS server has GitHub
OAuth wired up (`state.github_oauth` truthy), the global ``check_auth``
middleware (mlss_monitor/app.py) redirects every request whose endpoint
isn't in ``_PUBLIC_ENDPOINTS`` to the login page (HTML) or returns
``{"error": "Unauthorised", "login_required": true}`` (JSON, 401).

That breaks four endpoints that the firmware MUST be able to reach with no
session cookie:

* ``GET  /api/grow/install.sh``        (api_grow_dist.install_sh)
* ``GET  /api/grow/dist/latest``       (api_grow_dist.serve_wheel)
* ``GET  /api/grow/dist/<filename>``   (api_grow_dist.serve_wheel)
* ``POST /api/grow/enroll``            (api_grow_enroll.enroll)

These endpoints have their own auth posture: ``/enroll`` requires the
shared ``enrollment_key`` in the JSON body; the dist endpoints serve
SHA256-verified static bytes (the integrity guard lives in install.sh,
not in HTTP auth).

These tests boot the *real* Flask app with ``state.github_oauth`` mocked
truthy (the same posture as a production deployment) and assert the
firmware-callable endpoints reach their handler logic without being
intercepted by the auth middleware. The discriminator is the response body:

* Auth middleware kicks in -> ``{"login_required": true}``
* Endpoint reached       -> domain-specific response (200/201/400/401-from-key)

The tests also keep an exhaustive map (endpoint -> expected status, body
predicate) so future contributors who add new firmware-callable routes can
register them in one place.
"""
import hashlib
import sqlite3
import tempfile
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixture: real app with OAuth-on posture and a temp DB + dist dir.
# ---------------------------------------------------------------------------

@pytest.fixture
def oauth_on_client(monkeypatch, tmp_path):
    """Boot the real app with state.github_oauth mocked truthy.

    Yields ``(client, raw_enrollment_key, dist_dir)`` so individual tests can
    POST a valid enrollment, request wheels by name, etc.
    """
    # 1. Temp DB so the seed-on-first-run gives us an enrollment key.
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    db_file.close()
    import database.init_db as init_db
    init_db.DB_FILE = db_file.name
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_file.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_enroll.DB_FILE", db_file.name)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", db_file.name)
    init_db.create_db()

    raw_key_row = sqlite3.connect(db_file.name).execute(
        "SELECT value FROM app_settings "
        "WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()
    raw_key = raw_key_row[0] if raw_key_row else None

    # 2. Temp dist dir with one fake wheel — covers /latest and /<filename>.
    dist_dir = tmp_path / "grow_dist"
    dist_dir.mkdir()
    wheel_bytes = b"FAKE_WHEEL_BYTES"
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(wheel_bytes)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir
    )

    # 3. Real app with OAuth posture flipped on.
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state

    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "github_oauth", MagicMock())  # auth ON

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        # Crucially: do NOT set session — we want to prove the endpoint is
        # reachable without ever logging in.
        yield client, raw_key, dist_dir


def _is_login_required_envelope(body) -> bool:
    """Discriminator for the global auth-middleware 401 envelope."""
    return isinstance(body, dict) and body.get("login_required") is True


# ---------------------------------------------------------------------------
# C1 — endpoint-by-endpoint reachability under OAuth.
# ---------------------------------------------------------------------------

class TestInstallShPublic:
    def test_install_sh_returns_200_without_session(self, oauth_on_client):
        client, _, _ = oauth_on_client
        r = client.get("/api/grow/install.sh")
        assert r.status_code == 200, (
            f"install.sh blocked by auth middleware (status={r.status_code}, "
            f"body={r.get_data(as_text=True)[:200]!r})"
        )

    def test_install_sh_body_is_shellscript_not_json_envelope(self, oauth_on_client):
        client, _, _ = oauth_on_client
        r = client.get("/api/grow/install.sh")
        # Bash, not the {"error":"Unauthorised","login_required":true} envelope.
        assert r.mimetype in (
            "application/x-sh", "text/plain", "text/x-shellscript"
        ), f"unexpected mimetype: {r.mimetype}"
        assert b"#!/bin/bash" in r.data or b"#!/usr/bin/env bash" in r.data
        assert b"login_required" not in r.data


class TestDistLatestPublic:
    def test_dist_latest_returns_200_with_manifest(self, oauth_on_client):
        client, _, _ = oauth_on_client
        r = client.get("/api/grow/dist/latest")
        assert r.status_code == 200, (
            f"dist/latest blocked by auth middleware "
            f"(status={r.status_code}, body={r.get_data(as_text=True)[:200]!r})"
        )
        body = r.get_json()
        assert not _is_login_required_envelope(body), (
            "dist/latest hit the global auth-middleware envelope instead of the "
            "manifest handler"
        )
        # Sanity — the fixture put one wheel in the dist dir.
        assert "mlss_grow" in body
        assert body["mlss_grow"]["filename"] == "mlss_grow-0.1.0-py3-none-any.whl"
        assert body["mlss_grow"]["sha256"] == hashlib.sha256(
            b"FAKE_WHEEL_BYTES").hexdigest()


class TestServeWheelPublic:
    def test_wheel_download_returns_200_without_session(self, oauth_on_client):
        client, _, _ = oauth_on_client
        r = client.get("/api/grow/dist/mlss_grow-0.1.0-py3-none-any.whl")
        assert r.status_code == 200, (
            f"wheel download blocked by auth middleware "
            f"(status={r.status_code}, body={r.get_data(as_text=True)[:200]!r})"
        )
        assert r.data == b"FAKE_WHEEL_BYTES"

    def test_missing_wheel_still_reaches_handler_404_not_auth_401(
        self, oauth_on_client
    ):
        """Even a 404 must come from the *handler*, not the auth gate."""
        client, _, _ = oauth_on_client
        r = client.get("/api/grow/dist/does-not-exist.whl")
        assert r.status_code == 404
        # Body should not be the auth envelope.
        body = None
        try:
            body = r.get_json()
        except Exception:  # 404 from send_from_directory is HTML
            pass
        assert not _is_login_required_envelope(body)


class TestEnrollPublic:
    def test_enroll_with_invalid_key_returns_handler_401_not_auth_401(
        self, oauth_on_client
    ):
        """The discriminator: handler 401 has body {"error":"invalid_enrollment_key"};
        auth-middleware 401 has body {"error":"Unauthorised","login_required":true}.
        """
        client, _, _ = oauth_on_client
        r = client.post("/api/grow/enroll", json={
            "enrollment_key": "wrong-key",
            "hardware_serial": "hw-deadbeef",
            "plant": {"name": "X"},
        })
        assert r.status_code == 401
        body = r.get_json()
        assert body == {"error": "invalid_enrollment_key"}, (
            f"enroll hit the auth middleware instead of the handler: {body!r}"
        )

    def test_enroll_missing_fields_returns_handler_400_not_auth_401(
        self, oauth_on_client
    ):
        """A POST with no body must reach the handler -> 400 missing_fields."""
        client, _, _ = oauth_on_client
        r = client.post("/api/grow/enroll", json={})
        assert r.status_code == 400
        body = r.get_json()
        assert body.get("error") == "missing_fields"

    def test_enroll_with_valid_key_returns_201(self, oauth_on_client):
        """Stack-level happy path: a Pi can enroll without an OAuth cookie."""
        client, raw_key, _ = oauth_on_client
        assert raw_key, "fixture failed to seed the enrollment key"
        r = client.post("/api/grow/enroll", json={
            "enrollment_key": raw_key,
            "hardware_serial": "100000000c0a8014b",
            "plant": {"name": "Test Tomato", "type": "tomato"},
        })
        assert r.status_code == 201, (
            f"enroll happy path failed (status={r.status_code}, "
            f"body={r.get_data(as_text=True)[:200]!r})"
        )
        body = r.get_json()
        assert "unit_id" in body
        assert "token" in body


# ---------------------------------------------------------------------------
# Negative test — proves the auth middleware IS still active for non-firmware
# routes. Catches the regression where someone accidentally widens
# _PUBLIC_ENDPOINTS to include a sensitive route.
# ---------------------------------------------------------------------------

class TestAuthGateStillActiveForOtherRoutes:
    def test_non_firmware_api_still_returns_login_required(self, oauth_on_client):
        """A non-firmware /api/ route must still 401 with login_required."""
        client, _, _ = oauth_on_client
        # /api/grow/units is admin/controller territory — no firmware should
        # be reading the fleet list. Must still hit the gate.
        r = client.get("/api/grow/units")
        assert r.status_code == 401
        body = r.get_json()
        assert _is_login_required_envelope(body), (
            "/api/grow/units must remain behind the OAuth gate"
        )

    def test_dashboard_page_still_redirects_to_login(self, oauth_on_client):
        """A non-/api/ page request must still 302 to login."""
        client, _, _ = oauth_on_client
        r = client.get("/")
        # Either 302 redirect to /login or whatever, but NOT a normal page render.
        assert r.status_code in (301, 302, 308)


# ---------------------------------------------------------------------------
# Sanity check: the _PUBLIC_ENDPOINTS set lists the firmware endpoint names
# explicitly. This ties the test to the actual constant so future renames
# of the blueprint endpoints flag here too.
# ---------------------------------------------------------------------------

class TestPublicEndpointsConstant:
    def test_firmware_endpoints_listed_in_public_set(self):
        from mlss_monitor.app import _PUBLIC_ENDPOINTS
        for name in (
            "api_grow_enroll.enroll",
            "api_grow_dist.install_sh",
            "api_grow_dist.serve_wheel",
        ):
            assert name in _PUBLIC_ENDPOINTS, (
                f"{name} must be in _PUBLIC_ENDPOINTS so the firmware can "
                f"reach it without an OAuth session"
            )

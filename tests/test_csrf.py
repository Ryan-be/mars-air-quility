"""CSRF protection: SameSite cookies + Origin/Referer same-origin enforcement.

Two layers of defence are exercised here:

  1. ``SESSION_COOKIE_SAMESITE`` — a single config-flag check.
  2. ``check_csrf`` ``before_request`` middleware — the bulk of the suite.

The conftest's ``app_client`` fixture sets a default same-origin
``HTTP_ORIGIN`` env var on the test client. Tests that want to exercise the
rejection path must override per-request via ``headers={"Origin": ...}``.
"""
from __future__ import annotations

import pytest


# ── Pure helper tests ────────────────────────────────────────────────────────

class TestOriginAllowedHelper:
    """`_origin_allowed` is a pure function, tested in isolation."""

    def test_returns_true_on_exact_scheme_host_match(self):
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed("http://localhost", "http://localhost/") is True

    def test_returns_true_when_referer_carries_path(self):
        from mlss_monitor.app import _origin_allowed
        # Referer typically includes a path; we should ignore it.
        assert _origin_allowed(
            "http://localhost/some/page?q=1",
            "http://localhost/",
        ) is True

    def test_returns_false_for_path_only_url(self):
        from mlss_monitor.app import _origin_allowed
        # No scheme, no netloc — can't possibly match.
        assert _origin_allowed("/admin", "http://localhost/") is False

    def test_returns_false_for_protocol_mismatch(self):
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed(
            "http://mlss.local:5000",
            "https://mlss.local:5000/",
        ) is False

    def test_returns_false_for_host_mismatch(self):
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed(
            "http://evil.com",
            "http://localhost/",
        ) is False

    def test_returns_false_for_port_mismatch(self):
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed(
            "https://mlss.local:5001",
            "https://mlss.local:5000/",
        ) is False

    def test_returns_false_for_empty_input(self):
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed("", "http://localhost/") is False
        assert _origin_allowed(None, "http://localhost/") is False

    def test_https_with_port_matches(self):
        """Regression: scheme+netloc compare keeps the port in netloc."""
        from mlss_monitor.app import _origin_allowed
        assert _origin_allowed(
            "https://mlss.local:5000",
            "https://mlss.local:5000/",
        ) is True


# ── Cookie-config check ──────────────────────────────────────────────────────

def test_session_cookie_samesite_is_lax():
    """Layer 1: SameSite=Lax must be configured unconditionally."""
    import mlss_monitor.app as app_module
    assert app_module.app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"


# ── Middleware integration tests ─────────────────────────────────────────────

class TestCheckCsrfMiddleware:
    """`check_csrf` `before_request` handler — integration via test client."""

    # GET requests — read-only, no CSRF check.
    def test_get_request_skipped_no_origin_required(self, app_client, db):
        client, _ = app_client
        # Strip the default Origin so we prove GET is genuinely skipped.
        del client.environ_base["HTTP_ORIGIN"]
        res = client.get("/api/data?range=24h")
        # Whatever the route returns, it must NOT be a CSRF 403.
        assert res.status_code != 403 or (
            res.is_json
            and not (res.get_json() or {}).get("error", "").startswith("csrf_")
        )

    # POST with same-origin Origin → reaches handler.
    def test_post_with_same_origin_allowed(self, app_client, db):
        client, _ = app_client
        # The default conftest Origin (http://localhost) is same-origin.
        res = client.post(
            "/api/effector",
            json={"key": "fan1", "state": "on"},
        )
        # We don't care whether the effector call succeeds; we only care that
        # the CSRF layer didn't reject before reaching the handler.
        assert res.status_code != 403 or (
            (res.get_json() or {}).get("error", "") != "csrf_origin_mismatch"
        )

    # POST with cross-origin Origin → 403 csrf_origin_mismatch.
    def test_post_with_different_origin_rejected_403(self, app_client, db):
        client, _ = app_client
        res = client.post(
            "/api/effector",
            json={"key": "fan1", "state": "on"},
            headers={"Origin": "http://evil.com"},
        )
        assert res.status_code == 403
        assert res.get_json() == {"error": "csrf_origin_mismatch"}

    # POST with no Origin but same-origin Referer → reaches handler.
    def test_post_no_origin_falls_back_to_same_origin_referer(self, app_client, db):
        import mlss_monitor.app as app_module
        client, _ = app_client
        # Drop default Origin; supply same-origin Referer instead. We must
        # match the test-client's scheme (driven by PREFERRED_URL_SCHEME).
        del client.environ_base["HTTP_ORIGIN"]
        scheme = app_module.app.config.get("PREFERRED_URL_SCHEME", "http")
        res = client.post(
            "/api/effector",
            json={"key": "fan1", "state": "on"},
            headers={"Referer": f"{scheme}://localhost/some/page"},
        )
        assert res.status_code != 403 or (
            (res.get_json() or {}).get("error", "") not in (
                "csrf_origin_mismatch", "csrf_referer_mismatch",
                "csrf_origin_missing",
            )
        )

    # POST with no Origin but cross-origin Referer → 403 csrf_referer_mismatch.
    def test_post_with_referer_different_origin_rejected_403(self, app_client, db):
        client, _ = app_client
        del client.environ_base["HTTP_ORIGIN"]
        res = client.post(
            "/api/effector",
            json={"key": "fan1", "state": "on"},
            headers={"Referer": "http://evil.com/page"},
        )
        assert res.status_code == 403
        assert res.get_json() == {"error": "csrf_referer_mismatch"}

    # POST with NEITHER header → 403 csrf_origin_missing.
    def test_post_with_neither_header_rejected_403(self, app_client, db):
        client, _ = app_client
        del client.environ_base["HTTP_ORIGIN"]
        res = client.post(
            "/api/effector",
            json={"key": "fan1", "state": "on"},
        )
        assert res.status_code == 403
        assert res.get_json() == {"error": "csrf_origin_missing"}

    # Public endpoints (firmware-callable, bearer auth) bypass CSRF.
    def test_firmware_endpoint_bypasses_csrf(self, app_client, db):
        """`/api/grow/enroll` is in _PUBLIC_ENDPOINTS and uses bearer auth.

        The expected response is 400 (missing_fields) for an empty body,
        proving the request reached the handler — never 403 from CSRF.
        """
        client, _ = app_client
        del client.environ_base["HTTP_ORIGIN"]
        res = client.post("/api/grow/enroll", json={})
        # Reaches the handler — body validation kicks in, not a 403 CSRF block.
        assert res.status_code != 403
        # Sanity: it's the expected 400 from the route's own validation.
        assert res.status_code == 400

    # PUT / PATCH / DELETE are also state-changing.
    @pytest.mark.parametrize("method_name", ["put", "patch", "delete"])
    def test_other_state_changing_methods_also_checked(
        self, app_client, db, method_name,
    ):
        """PUT/PATCH/DELETE with a bad Origin must 403 like POST does."""
        client, _ = app_client
        method = getattr(client, method_name)
        # Use a path that exists in *some* form so we don't measure 404
        # routing instead of CSRF. The middleware fires before routing's
        # method-not-allowed check, so any path will produce csrf_origin_mismatch.
        res = method(
            "/api/effector",
            headers={"Origin": "http://evil.com"},
        )
        assert res.status_code == 403
        assert res.get_json() == {"error": "csrf_origin_mismatch"}

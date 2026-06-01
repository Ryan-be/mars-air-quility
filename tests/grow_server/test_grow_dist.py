"""Tests for /api/grow/dist/* — install.sh, wheel files, and the manifest.

The /latest manifest now returns {pkg: {version, filename, sha256}} instead of
{pkg: version_string} so the Pi installer can SHA256-verify each wheel after
download (Vuln 4 fix — defends against LAN MITM tampering with served wheels).
"""
import hashlib
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Set up a temp grow_dist with a fake wheel file + install script."""
    dist_dir = tmp_path / "grow_dist"
    dist_dir.mkdir()
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(b"FAKEWHEELBYTES")
    (dist_dir / "mlss_contracts-0.1.0-py3-none-any.whl").write_bytes(b"FAKE2")

    # Patch BOTH the legacy string constant and the Path-typed one used by
    # the new sha256-aware code path.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist.GROW_DIST_DIR", str(dist_dir))
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client()


def test_install_sh_served(client):
    r = client.get("/api/grow/install.sh")
    assert r.status_code == 200
    assert r.mimetype in ("application/x-sh", "text/plain", "text/x-shellscript")
    assert b"#!/bin/bash" in r.data
    assert b"mlss-grow" in r.data


def test_dist_file_served(client):
    r = client.get("/api/grow/dist/mlss_grow-0.1.0-py3-none-any.whl")
    assert r.status_code == 200
    assert r.data == b"FAKEWHEELBYTES"


def test_dist_path_traversal_rejected(client):
    r = client.get("/api/grow/dist/../../../etc/passwd")
    assert r.status_code in (400, 404)


def test_dist_404_for_missing_file(client):
    r = client.get("/api/grow/dist/nonexistent.whl")
    assert r.status_code == 404


def test_dist_latest_returns_manifest_dict_for_each_wheel(client):
    """The /latest manifest now returns {pkg: {version, filename, sha256}}.

    Each entry must include the version, the actual filename to GET, and the
    sha256 hex string the installer will verify after download.
    """
    r = client.get("/api/grow/dist/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert "mlss_grow" in body
    assert "mlss_contracts" in body

    grow = body["mlss_grow"]
    assert grow["version"] == "0.1.0"
    assert grow["filename"] == "mlss_grow-0.1.0-py3-none-any.whl"
    assert grow["sha256"] == hashlib.sha256(b"FAKEWHEELBYTES").hexdigest()

    contracts = body["mlss_contracts"]
    assert contracts["version"] == "0.1.0"
    assert contracts["filename"] == "mlss_contracts-0.1.0-py3-none-any.whl"
    assert contracts["sha256"] == hashlib.sha256(b"FAKE2").hexdigest()


# ---------------------------------------------------------------------------
# SHA256 manifest tests (Vuln 4 — LAN MITM defence for the Pi installer)
# ---------------------------------------------------------------------------

def test_latest_includes_sha256_for_each_wheel(tmp_path, monkeypatch):
    """The /latest manifest must expose a sha256 hex string for each wheel
    so the Pi installer can verify integrity after download."""
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    fake_wheel = dist_dir / "mlss_grow-0.1.0-py3-none-any.whl"
    fake_wheel.write_bytes(b"FAKE_WHEEL_CONTENT")
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert "mlss_grow" in body
    entry = body["mlss_grow"]
    assert entry["version"] == "0.1.0"
    assert entry["filename"] == "mlss_grow-0.1.0-py3-none-any.whl"
    expected = hashlib.sha256(b"FAKE_WHEEL_CONTENT").hexdigest()
    assert entry["sha256"] == expected


def test_latest_sha256_matches_actual_wheel_bytes(tmp_path, monkeypatch):
    """Compute the sha256 in the test manually and assert the manifest matches —
    catches any future regression where the manifest hash drifts from the
    actual served bytes."""
    contents = b"\x50\x4b\x03\x04" + b"X" * 1000  # zip magic + filler
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    fake_wheel = dist_dir / "mlss_grow-9.9.9-py3-none-any.whl"
    fake_wheel.write_bytes(contents)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/latest")
    body = r.get_json()
    assert body["mlss_grow"]["sha256"] == hashlib.sha256(contents).hexdigest()


def test_latest_handles_missing_wheel_dir_gracefully(monkeypatch):
    """If wheels haven't been built yet, /latest must return an empty dict (not 500)."""
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR",
        Path("/path/that/does/not/exist"))

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/latest")
    assert r.status_code == 200
    assert r.get_json() == {}


# ---------------------------------------------------------------------------
# I5 — systemd .service shipped via /api/grow/dist/.
#
# install.sh used to look for the systemd unit in three brittle places (a
# repo-relative cp, a venv site-packages cp, and a curl as last resort).
# The fix is to ship the .service file through the same dist endpoint the
# wheels use, exposed via the manifest with its sha256 so install.sh can
# verify integrity exactly like it already does for wheels.
# ---------------------------------------------------------------------------

def test_latest_includes_systemd_service_when_present(tmp_path, monkeypatch):
    """/latest must include the mlss-grow.service entry when the file is in
    the dist dir, alongside the wheel entries."""
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(b"WHEEL")
    service_bytes = b"[Unit]\nDescription=MLSS Plant Grow Unit\n"
    (dist_dir / "mlss-grow.service").write_bytes(service_bytes)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert "mlss-grow.service" in body, (
        f"manifest missing systemd unit entry; got keys={list(body)!r}"
    )
    entry = body["mlss-grow.service"]
    assert entry["filename"] == "mlss-grow.service"
    assert entry["sha256"] == hashlib.sha256(service_bytes).hexdigest()
    # Wheels carry a real semver version; the .service file isn't versioned
    # the same way. Either null or a sentinel string is acceptable, but the
    # KEY must exist so installers don't have to special-case its absence.
    assert "version" in entry


def test_latest_omits_service_when_not_in_dist(tmp_path, monkeypatch):
    """If the .service file isn't in the dist dir, the manifest must not
    fabricate an entry for it."""
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(b"WHEEL")
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/latest")
    body = r.get_json()
    assert "mlss-grow.service" not in body
    # And wheel entries still work.
    assert "mlss_grow" in body


def test_serve_wheel_route_serves_systemd_unit(tmp_path, monkeypatch):
    """GET /api/grow/dist/mlss-grow.service must return the actual file bytes."""
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    service_bytes = b"[Unit]\nDescription=MLSS Plant Grow Unit firmware\n"
    (dist_dir / "mlss-grow.service").write_bytes(service_bytes)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist.GROW_DIST_DIR", str(dist_dir))

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)

    r = app.test_client().get("/api/grow/dist/mlss-grow.service")
    assert r.status_code == 200
    assert r.data == service_bytes


def test_service_sha256_matches_actual_bytes_after_install_flow(
    tmp_path, monkeypatch
):
    """Stack-level: the manifest hash for mlss-grow.service must match the
    bytes returned by GET /api/grow/dist/mlss-grow.service. This is the
    exact contract install.sh relies on."""
    dist_dir = tmp_path / "wheels"
    dist_dir.mkdir()
    service_bytes = b"[Unit]\nDescription=MLSS\n[Service]\nExecStart=/x\n"
    (dist_dir / "mlss-grow.service").write_bytes(service_bytes)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist.GROW_DIST_DIR", str(dist_dir))

    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)
    client = app.test_client()

    manifest = client.get("/api/grow/dist/latest").get_json()
    served = client.get("/api/grow/dist/mlss-grow.service").data
    assert manifest["mlss-grow.service"]["sha256"] == hashlib.sha256(served).hexdigest()


# ── CA cert endpoint (rotation-safe install.sh pin) ──────────────────────────

def test_ca_crt_served_when_present(tmp_path, monkeypatch):
    """install.sh pins /api/grow/ca.crt before enrollment so future leaf
    rotations don't break the grow unit's TLS pin. Endpoint is public
    by design (CA is a trust anchor, not a secret)."""
    repo_root = tmp_path / "fake_repo"
    (repo_root / "certs").mkdir(parents=True)
    ca = repo_root / "certs" / "ca.crt"
    ca.write_bytes(
        b"-----BEGIN CERTIFICATE-----\nFAKECA\n-----END CERTIFICATE-----\n"
    )

    # The route resolves the CA path relative to its own module file,
    # so monkeypatch Path on the module to redirect at the repo root.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist.Path",
        type("FakePath", (), {
            "__new__": lambda cls, *a, **k: __import__("pathlib").Path(*a, **k),
        }),
        raising=False,
    )
    # Simpler: monkeypatch the __file__ attribute the route uses.
    import mlss_monitor.routes.api_grow_dist as mod
    monkeypatch.setattr(mod, "__file__",
                        str(repo_root / "mlss_monitor" / "routes" / "api_grow_dist.py"))

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(mod.api_grow_dist_bp)
    client = app.test_client()

    r = client.get("/api/grow/ca.crt")
    assert r.status_code == 200
    assert b"BEGIN CERTIFICATE" in r.data
    assert "x509" in r.headers["Content-Type"] or \
           "ca-cert" in r.headers["Content-Type"]


def test_ca_crt_404_when_missing(tmp_path, monkeypatch):
    """Older hubs (pre-CA) don't have certs/ca.crt — install.sh treats
    a 404 here as the signal to fall back to legacy TOFU leaf pinning."""
    repo_root = tmp_path / "fake_repo"
    (repo_root / "certs").mkdir(parents=True)
    # No ca.crt written.
    import mlss_monitor.routes.api_grow_dist as mod
    monkeypatch.setattr(mod, "__file__",
                        str(repo_root / "mlss_monitor" / "routes" / "api_grow_dist.py"))

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(mod.api_grow_dist_bp)
    client = app.test_client()
    r = client.get("/api/grow/ca.crt")
    assert r.status_code == 404

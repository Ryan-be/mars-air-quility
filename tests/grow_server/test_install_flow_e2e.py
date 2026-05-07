"""Stack-level test: the manifest-then-download flow that install.sh runs.

Simulates the bash flow in Python:
  1. GET /api/grow/dist/latest -> parse manifest
  2. GET /api/grow/dist/<filename> for each wheel
  3. sha256(downloaded_bytes) == manifest['sha256']

If this test fails, the install.sh integrity guard would also fail,
meaning a wheel served to a Pi wouldn't validate.
"""
import hashlib
import tempfile
import pytest


@pytest.fixture
def app_with_wheels(monkeypatch, tmp_path):
    """Real app with fake wheel files in a tmp dist dir."""
    # DB
    # pylint: disable=R1732  # delete=False + close() pattern: we only want the path
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", tmp.name)
    init_db.create_db()

    # Fake wheels
    dist_dir = tmp_path / "grow_dist"
    dist_dir.mkdir()
    grow_bytes = b"PK\x03\x04" + b"GROW_WHEEL_CONTENTS" * 100
    contracts_bytes = b"PK\x03\x04" + b"CONTRACTS_WHEEL_CONTENTS" * 100
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(grow_bytes)
    (dist_dir / "mlss_contracts-0.1.0-py3-none-any.whl").write_bytes(contracts_bytes)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_dist._WHEEL_DIR", dist_dir)
    # serve_wheel uses _wheel_dir() too, which reads _WHEEL_DIR — so a single
    # patch covers both the manifest hash computation AND the download path.

    # Build mini app
    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client(), grow_bytes, contracts_bytes


def test_full_install_flow_manifest_then_download_then_verify(app_with_wheels):
    """End-to-end of what install.sh does: manifest, download, verify."""
    c, expected_grow, expected_contracts = app_with_wheels

    # 1. Manifest
    r = c.get("/api/grow/dist/latest")
    assert r.status_code == 200
    manifest = r.get_json()
    assert "mlss_grow" in manifest and "mlss_contracts" in manifest

    # 2. Download each wheel
    grow_resp = c.get(f"/api/grow/dist/{manifest['mlss_grow']['filename']}")
    contracts_resp = c.get(f"/api/grow/dist/{manifest['mlss_contracts']['filename']}")
    assert grow_resp.status_code == 200
    assert contracts_resp.status_code == 200

    # 3. Verify hashes — this is exactly what install.sh's sha256sum check does
    grow_actual = hashlib.sha256(grow_resp.data).hexdigest()
    contracts_actual = hashlib.sha256(contracts_resp.data).hexdigest()
    assert grow_actual == manifest["mlss_grow"]["sha256"]
    assert contracts_actual == manifest["mlss_contracts"]["sha256"]

    # 4. Cross-check: served bytes ARE the bytes we wrote
    assert grow_resp.data == expected_grow
    assert contracts_resp.data == expected_contracts


def test_tampered_wheel_would_fail_verification(app_with_wheels):
    """If an attacker substitutes a wheel between manifest and download,
    the hash check would fail. Simulate by hashing different bytes."""
    c, _, _ = app_with_wheels
    r = c.get("/api/grow/dist/latest")
    manifest = r.get_json()

    tampered = b"MALICIOUS_PAYLOAD"
    tampered_hash = hashlib.sha256(tampered).hexdigest()
    assert tampered_hash != manifest["mlss_grow"]["sha256"]

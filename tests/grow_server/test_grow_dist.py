import os
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Set up a temp grow_dist with a fake wheel file + install script."""
    dist_dir = tmp_path / "grow_dist"
    dist_dir.mkdir()
    (dist_dir / "mlss_grow-0.1.0-py3-none-any.whl").write_bytes(b"FAKEWHEELBYTES")
    (dist_dir / "mlss_contracts-0.1.0-py3-none-any.whl").write_bytes(b"FAKE2")

    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.GROW_DIST_DIR", str(dist_dir))

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


def test_dist_latest_returns_version(client):
    r = client.get("/api/grow/dist/latest")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mlss_grow"] == "0.1.0"
    assert body["mlss_contracts"] == "0.1.0"

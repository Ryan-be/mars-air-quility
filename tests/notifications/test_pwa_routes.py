"""Tests for PWA-related routes: /sw.js, /static/manifest.json, icons."""

import json
from pathlib import Path

import pytest
from flask import Flask

from mlss_monitor.routes.pages import pages_bp


@pytest.fixture
def app():
    a = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parents[2] / "templates"),
        static_folder=str(Path(__file__).resolve().parents[2] / "static"),
    )
    a.config["TESTING"] = True
    a.secret_key = "test"
    a.register_blueprint(pages_bp)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def test_service_worker_served_at_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["Content-Type"]
    assert r.headers.get("Service-Worker-Allowed") == "/"
    body = r.get_data(as_text=True)
    assert "push" in body  # has the push event listener
    assert "notificationclick" in body


def test_manifest_served_from_static(client):
    r = client.get("/static/manifest.json")
    assert r.status_code == 200
    data = json.loads(r.get_data(as_text=True))
    assert data["name"].startswith("MLSS")
    assert data["display"] == "standalone"
    assert any(i["sizes"] == "192x192" for i in data["icons"])


def test_icons_present_on_disk():
    icon_dir = Path(__file__).resolve().parents[2] / "static" / "icons"
    for fname in ("icon-180.png", "icon-192.png", "icon-512.png",
                  "icon-512-maskable.png"):
        p = icon_dir / fname
        assert p.is_file(), f"missing {fname}"
        assert p.stat().st_size > 1000, f"{fname} suspiciously small"

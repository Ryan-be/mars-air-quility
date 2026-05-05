"""Reveal the raw enrollment key once (then it's deleted from app_settings)."""
import sqlite3
import tempfile
import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_dist.DB_FILE", tmp.name)
    init_db.create_db()
    from flask import Flask
    from mlss_monitor.routes.api_grow_dist import api_grow_dist_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_dist_bp)
    return app.test_client(), tmp.name


def test_peek_once_returns_raw_key_first_time(client):
    c, db = client
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 200
    assert "key" in r.get_json()


def test_peek_once_deletes_after_reveal(client):
    c, db = client
    c.get("/api/grow/enrollment-key/peek-once")
    r = c.get("/api/grow/enrollment-key/peek-once")
    assert r.status_code == 410  # Gone — already revealed
    body = r.get_json()
    assert "already_revealed" in body.get("error", "")

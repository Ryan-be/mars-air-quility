"""bearer_required decorator: validates per-unit bearer tokens on grow API endpoints."""
import sqlite3
from datetime import datetime

import pytest
from flask import Flask, g, jsonify


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Flask app with a temp DB and one enrolled unit."""
    db_path = str(tmp_path / "test.db")

    import database.init_db as init_db
    init_db.DB_FILE = db_path
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
    init_db.create_db()

    from mlss_monitor.grow.auth import generate_token, hash_secret, bearer_required

    raw_token = generate_token()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?)",
        ("hw-001", "Test Plant", datetime.utcnow(), hash_secret(raw_token),
         datetime.utcnow()),
    )
    unit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    app = Flask(__name__)

    @app.route("/api/grow/units/<int:unit_id>/test")
    @bearer_required
    def protected(unit_id):
        return jsonify({"unit_id": unit_id, "auth_unit_id": g.grow_unit_id})

    return app, raw_token, unit_id


def test_valid_bearer_passes(app):
    flask_app, token, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.get_json()["auth_unit_id"] == unit_id


def test_missing_header_returns_401(app):
    flask_app, _, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test")
    assert r.status_code == 401


def test_wrong_token_returns_401(app):
    flask_app, _, unit_id = app
    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_inactive_unit_returns_403(app):
    flask_app, token, unit_id = app
    # Deactivate the unit
    import sqlite3
    from database.init_db import DB_FILE
    conn = sqlite3.connect(DB_FILE)
    conn.execute("UPDATE grow_units SET is_active=0 WHERE id=?", (unit_id,))
    conn.commit()
    conn.close()

    client = flask_app.test_client()
    r = client.get(f"/api/grow/units/{unit_id}/test",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403

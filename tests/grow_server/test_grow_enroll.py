"""POST /api/grow/enroll — first-boot enrollment endpoint tests."""
import sqlite3

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
    init_db.create_db()

    # Pull the raw enrollment key the seed left us
    conn = sqlite3.connect(db_path)
    raw_key = conn.execute(
        "SELECT value FROM app_settings WHERE key='grow_enrollment_key_raw_pending_reveal'"
    ).fetchone()[0]
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_enroll import api_grow_enroll_bp
    monkeypatch.setattr("mlss_monitor.routes.api_grow_enroll.DB_FILE", db_path)

    app = Flask(__name__)
    app.register_blueprint(api_grow_enroll_bp)
    return app.test_client(), raw_key, db_path


def test_enroll_with_valid_key_creates_unit_and_returns_token(client):
    c, raw_key, db_path = client
    r = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test Tomato", "type": "tomato", "medium": "soil"},
    })
    assert r.status_code == 201
    body = r.get_json()
    assert "unit_id" in body
    assert "token" in body
    assert len(body["token"]) >= 32

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT label, plant_type, medium_type, hardware_serial "
        "FROM grow_units WHERE id=?", (body["unit_id"],)
    ).fetchone()
    conn.close()
    assert row == ("Test Tomato", "tomato", "soil", "100000000c0a8014b")


def test_enroll_idempotent_returns_existing_unit(client):
    c, raw_key, db_path = client
    r1 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test", "type": "tomato"},
    })
    r2 = c.post("/api/grow/enroll", json={
        "enrollment_key": raw_key,
        "hardware_serial": "100000000c0a8014b",
        "plant": {"name": "Test"},
    })
    assert r1.get_json()["unit_id"] == r2.get_json()["unit_id"]
    assert r1.get_json()["token"] != r2.get_json()["token"]


def test_enroll_with_wrong_key_returns_401(client):
    c, _, _ = client
    r = c.post("/api/grow/enroll", json={
        "enrollment_key": "wrong-key",
        "hardware_serial": "hw-002",
        "plant": {"name": "X"},
    })
    assert r.status_code == 401


def test_enroll_missing_fields_returns_400(client):
    c, raw_key, _ = client
    r = c.post("/api/grow/enroll", json={"enrollment_key": raw_key})
    assert r.status_code == 400

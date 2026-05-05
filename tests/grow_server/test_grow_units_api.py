"""GET /api/grow/units (list) and /api/grow/units/<id> (detail) endpoint tests."""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    import database.init_db as init_db
    init_db.DB_FILE = db_path
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", db_path)
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at, last_known_state_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("hw-1", "Tomato 1", now, "hash1", now, now,
         json.dumps({"soil_moisture_pct": 58, "light_state": True}))
    )
    conn.execute(
        "INSERT INTO grow_units (hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("hw-2", "Basil 1", now, "hash2", now, now - timedelta(minutes=10)),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    monkeypatch.setattr("mlss_monitor.routes.api_grow_units.DB_FILE", db_path)

    app = Flask(__name__)
    app.register_blueprint(api_grow_units_bp)
    return app.test_client()


def test_list_returns_all_active_units(client):
    r = client.get("/api/grow/units")
    assert r.status_code == 200
    body = r.get_json()
    assert "units" in body
    assert len(body["units"]) == 2
    labels = {u["label"] for u in body["units"]}
    assert labels == {"Tomato 1", "Basil 1"}


def test_list_includes_status_field(client):
    r = client.get("/api/grow/units")
    statuses = {u["label"]: u["status"] for u in r.get_json()["units"]}
    assert statuses["Tomato 1"] == "online"
    assert statuses["Basil 1"] == "offline"


def test_list_includes_last_known_state(client):
    r = client.get("/api/grow/units")
    tomato = next(u for u in r.get_json()["units"] if u["label"] == "Tomato 1")
    assert tomato["last_known_state"]["soil_moisture_pct"] == 58


def test_detail_returns_full_unit(client):
    list_resp = client.get("/api/grow/units").get_json()
    unit_id = next(u["id"] for u in list_resp["units"] if u["label"] == "Tomato 1")
    r = client.get(f"/api/grow/units/{unit_id}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["label"] == "Tomato 1"
    assert body["plant_type"] == "generic"
    assert body["medium_type"] == "soil"
    assert body["status"] == "online"
    assert "capabilities" in body  # empty list for now


def test_detail_404_for_missing(client):
    r = client.get("/api/grow/units/9999")
    assert r.status_code == 404

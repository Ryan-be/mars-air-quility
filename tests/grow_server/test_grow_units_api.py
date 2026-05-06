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


# ---------------------------------------------------------------------------
# Configure-tab Task 5: GET response includes overrides + calibration +
# light_windows blocks. The frontend (Tasks 6-7) reads these to render the
# Configure panels with current values + "(default)" vs "(custom)" indicators.
# ---------------------------------------------------------------------------


def _unit_id(client, label="Tomato 1"):
    body = client.get("/api/grow/units").get_json()
    return next(u["id"] for u in body["units"] if u["label"] == label)


def _set_overrides(db_path, unit_id, **overrides):
    """Raw UPDATE of grow_units override / calibration columns."""
    if not overrides:
        return
    cols = ", ".join(f"{k}=?" for k in overrides)
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"UPDATE grow_units SET {cols} WHERE id=?",
        (*overrides.values(), unit_id),
    )
    conn.commit()
    conn.close()


def _seed_light_window(db_path, unit_id, phase, start, end, sort_order):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_light_windows "
        "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
        "VALUES (?, ?, ?, ?, ?)",
        (unit_id, phase, start, end, sort_order),
    )
    conn.commit()
    conn.close()


def test_get_unit_includes_overrides_block(client, tmp_path):
    db_path = str(tmp_path / "test.db")
    uid = _unit_id(client)
    _set_overrides(
        db_path, uid,
        watering_kp_override=0.5,
        soak_window_min_override=60,
    )
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert "overrides" in body
    assert body["overrides"] == {
        "watering_target": None,
        "kp": 0.5,
        "ki": None,
        "kd": None,
        "soak_window_min": 60,
        "min_pulse_s": None,
        "max_pulse_s": None,
    }


def test_get_unit_overrides_block_all_null_when_no_overrides_set(client):
    uid = _unit_id(client)
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["overrides"] == {
        "watering_target": None,
        "kp": None,
        "ki": None,
        "kd": None,
        "soak_window_min": None,
        "min_pulse_s": None,
        "max_pulse_s": None,
    }


def test_get_unit_includes_calibration_block(client, tmp_path):
    db_path = str(tmp_path / "test.db")
    uid = _unit_id(client)
    _set_overrides(db_path, uid, soil_dry_raw=300, soil_wet_raw=1500)
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["calibration"] == {"dry_raw": 300, "wet_raw": 1500}


def test_get_unit_calibration_block_null_when_uncalibrated(client):
    uid = _unit_id(client)
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["calibration"] == {"dry_raw": None, "wet_raw": None}


def test_get_unit_includes_light_windows_grouped_by_phase(client, tmp_path):
    db_path = str(tmp_path / "test.db")
    uid = _unit_id(client)
    _seed_light_window(db_path, uid, "vegetative", "06:00", "12:00", 0)
    _seed_light_window(db_path, uid, "vegetative", "14:00", "22:00", 1)
    _seed_light_window(db_path, uid, "flowering",  "08:00", "20:00", 0)

    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["light_windows"] == {
        "vegetative": [
            {"start": "06:00", "end": "12:00"},
            {"start": "14:00", "end": "22:00"},
        ],
        "flowering": [
            {"start": "08:00", "end": "20:00"},
        ],
    }


def test_get_unit_light_windows_empty_dict_when_none(client):
    uid = _unit_id(client)
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["light_windows"] == {}


def test_get_unit_existing_keys_unchanged(client):
    """Regression guard: the new blocks are additive; existing keys still
    present and shaped as before."""
    uid = _unit_id(client)
    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["id"] == uid
    assert body["label"] == "Tomato 1"
    assert "capabilities" in body
    assert isinstance(body["capabilities"], list)
    assert "last_known_state" in body
    assert body["last_known_state"]["soil_moisture_pct"] == 58
    assert body["status"] == "online"
    assert body["plant_type"] == "generic"
    assert body["medium_type"] == "soil"
    assert body["current_phase"] == "vegetative"


def test_get_unit_light_windows_preserves_sort_order(client, tmp_path):
    """Insert in non-monotonic sort_order; response should return rows
    ordered by sort_order, not insertion order."""
    db_path = str(tmp_path / "test.db")
    uid = _unit_id(client)
    # Insert with sort_order [2, 0, 1] — the seed values mean the row inserted
    # FIRST has the highest sort_order, so insertion order != sort order.
    _seed_light_window(db_path, uid, "vegetative", "20:00", "22:00", 2)
    _seed_light_window(db_path, uid, "vegetative", "06:00", "08:00", 0)
    _seed_light_window(db_path, uid, "vegetative", "10:00", "12:00", 1)

    body = client.get(f"/api/grow/units/{uid}").get_json()
    assert body["light_windows"]["vegetative"] == [
        {"start": "06:00", "end": "08:00"},
        {"start": "10:00", "end": "12:00"},
        {"start": "20:00", "end": "22:00"},
    ]

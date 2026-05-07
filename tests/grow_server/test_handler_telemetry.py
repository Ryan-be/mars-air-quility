"""handle_telemetry: writes one grow_telemetry row + updates last_known_state."""
import json
import sqlite3
import tempfile
from datetime import datetime
import pytest


@pytest.fixture
def db_with_unit(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.grow.handlers.DB_FILE", tmp.name)
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, soil_dry_raw, soil_wet_raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "hw-1", "Tomato 1", datetime.utcnow(), "hash", datetime.utcnow(),
         200, 1500),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_handle_telemetry_inserts_row(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime(2026, 5, 3, 12, 34, 18), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
        "soil_temp_c": 21.4,
    })
    conn = sqlite3.connect(db_with_unit)
    row = conn.execute(
        "SELECT soil_moisture_raw, soil_moisture_pct, light_state, "
        "pump_state, soil_temp_c FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()
    assert row == (612, 31.7, 1, 0, 21.4)


def test_handle_telemetry_updates_last_known_state(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612,
        "soil_moisture_pct": 31.7,
        "light_state": True,
        "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    state_json, last_seen = conn.execute(
        "SELECT last_known_state_json, last_seen_at FROM grow_units WHERE id=1"
    ).fetchone()
    state = json.loads(state_json)
    assert state["soil_moisture_pct"] == 31.7
    assert state["light_state"] is True
    assert last_seen is not None


def test_handle_telemetry_returns_inserted_id(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    inserted_id = handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    assert isinstance(inserted_id, int)
    assert inserted_id > 0


def _seed_capability(db_path, unit_id, channel, hardware, is_required, health):
    """Helper: insert a grow_unit_capabilities row with details_json={'health': ...}."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, unit_label, "
        " installed_at, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (unit_id, channel, hardware, int(is_required), "bool",
         datetime.utcnow(), json.dumps({"health": health})),
    )
    conn.commit()
    conn.close()


def _read_capability_health(db_path, unit_id, channel):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT details_json FROM grow_unit_capabilities "
        "WHERE unit_id=? AND channel=?",
        (unit_id, channel),
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    return json.loads(row[0]).get("health")


def test_handle_telemetry_with_pump_state_1_promotes_pump_to_connected(db_with_unit):
    """Phase 2 sense-only-mode: when telemetry shows pump_state=1, the server
    promotes the pump capability's health to "connected" — that's strong
    evidence the actuator is wired and working."""
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "pump", "automation_phat", False, "untested")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": True,
    })
    assert _read_capability_health(db_with_unit, 1, "pump") == "connected"


def test_handle_telemetry_with_light_state_1_promotes_light_to_connected(db_with_unit):
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "light", "automation_phat", False, "untested")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": True, "pump_state": False,
    })
    assert _read_capability_health(db_with_unit, 1, "light") == "connected"


def test_handle_telemetry_with_state_0_does_not_demote_connected(db_with_unit):
    """Once a capability is "connected", routine off-state telemetry must NOT
    flip it back. The pump being off most of the time is the normal idle
    state, not evidence of disconnection. Only the watchdog (after a
    command without follow-up evidence) demotes."""
    from mlss_monitor.grow.handlers import handle_telemetry
    _seed_capability(db_with_unit, 1, "pump", "automation_phat", False, "connected")
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 612, "light_state": False, "pump_state": False,
    })
    assert _read_capability_health(db_with_unit, 1, "pump") == "connected"


def test_handle_telemetry_computes_pct_when_unit_calibrated(db_with_unit):
    """If pct is missing but raw + calibration are present, server fills it in."""
    from mlss_monitor.grow.handlers import handle_telemetry
    handle_telemetry(unit_id=1, ts=datetime.utcnow(), payload={
        "soil_moisture_raw": 850,  # midway between dry=200 and wet=1500
        "light_state": False, "pump_state": False,
    })
    conn = sqlite3.connect(db_with_unit)
    pct = conn.execute(
        "SELECT soil_moisture_pct FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    # (850-200)/(1500-200) = 0.5 → 50%
    assert pct == pytest.approx(50.0, abs=0.5)

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
        "bearer_token_hash, phase_set_at) VALUES (1, 'hw1', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()
    return tmp.name


def test_handle_capabilities_inserts_rows(db_with_unit):
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "details": {"i2c_address": "0x36"}},
            {"channel": "soil_temp_c", "hardware": "Seesaw",
             "is_required": False, "unit_label": "°C", "details": None},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    rows = conn.execute(
        "SELECT channel, hardware, is_required FROM grow_unit_capabilities "
        "WHERE unit_id=1 ORDER BY channel"
    ).fetchall()
    assert rows == [
        ("soil_moisture", "Seesaw", 1),
        ("soil_temp_c", "Seesaw", 0),
    ]


def test_handle_capabilities_writes_health_to_details_json(db_with_unit):
    """Phase 2 sense-only-mode: the firmware sends `health` per capability.
    The server must persist it into grow_unit_capabilities.details_json
    so api_grow_units can surface it on GET (no schema migration needed —
    we reuse the existing JSON column).
    """
    import json
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool",
             "health": "no_hardware"},
            {"channel": "soil_moisture", "hardware": "Seesaw",
             "is_required": True, "unit_label": "raw",
             "details": {"i2c_address": "0x36"},
             "health": "connected"},
        ],
        "firmware_version": "0.1.0",
        "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    rows = {r[0]: json.loads(r[1]) if r[1] else {} for r in conn.execute(
        "SELECT channel, details_json FROM grow_unit_capabilities WHERE unit_id=1"
    ).fetchall()}
    assert rows["pump"]["health"] == "no_hardware"
    assert rows["soil_moisture"]["health"] == "connected"
    # Pre-existing details survive — the merge must NOT clobber.
    assert rows["soil_moisture"]["i2c_address"] == "0x36"


def test_handle_capabilities_defaults_missing_health_to_untested(db_with_unit):
    """A firmware too old to send `health` shouldn't crash the handler — fall
    back to "untested" so the UI still has a value to render."""
    import json
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "pump", "hardware": "automation_phat",
             "is_required": False, "unit_label": "bool"},  # no health key
        ],
        "firmware_version": "0.1.0", "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    details_json = conn.execute(
        "SELECT details_json FROM grow_unit_capabilities "
        "WHERE unit_id=1 AND channel='pump'"
    ).fetchone()[0]
    assert json.loads(details_json)["health"] == "untested"


def test_handle_capabilities_replaces_old_set(db_with_unit):
    """A second capabilities push replaces the first (e.g. a sensor was added)."""
    from mlss_monitor.grow.handlers import handle_capabilities
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [{"channel": "soil_moisture", "hardware": "S",
                          "is_required": True, "unit_label": "raw"}],
        "firmware_version": "0.1.0", "hardware_serial": "hw1",
    })
    handle_capabilities(unit_id=1, ts=datetime.utcnow(), payload={
        "capabilities": [
            {"channel": "soil_moisture", "hardware": "S", "is_required": True,
             "unit_label": "raw"},
            {"channel": "ambient_lux", "hardware": "TSL2591",
             "is_required": False, "unit_label": "lux"},
        ],
        "firmware_version": "0.1.0", "hardware_serial": "hw1",
    })
    conn = sqlite3.connect(db_with_unit)
    channels = {r[0] for r in conn.execute(
        "SELECT channel FROM grow_unit_capabilities WHERE unit_id=1"
    ).fetchall()}
    assert channels == {"soil_moisture", "ambient_lux"}

"""grow_unit_capabilities, grow_telemetry, and grow_watering_events tables exist."""
import sqlite3
from database.init_db import create_db
import pytest


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    """Create a fresh DB and return its path."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", path)
    create_db()
    return path


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1] for row in rows}


def test_grow_unit_capabilities_table(db_path):
    cols = _columns(db_path, "grow_unit_capabilities")
    assert {"unit_id", "channel", "hardware", "is_required",
            "unit_label", "installed_at", "details_json"} <= cols


def test_grow_telemetry_table(db_path):
    cols = _columns(db_path, "grow_telemetry")
    required = {"id", "unit_id", "timestamp_utc", "soil_moisture_raw",
                "soil_moisture_pct", "light_state", "pump_state"}
    optional = {"soil_temp_c", "ambient_lux", "air_temp_c",
                "air_humidity_pct", "reservoir_level_pct"}
    assert required <= cols
    assert optional <= cols


def test_grow_watering_events_table(db_path):
    cols = _columns(db_path, "grow_watering_events")
    assert {"id", "unit_id", "timestamp_utc", "trigger", "duration_s",
            "soil_pct_before", "soil_pct_after_5min", "triggered_by",
            "pid_error", "pid_p_term", "pid_i_term", "pid_d_term"} <= cols

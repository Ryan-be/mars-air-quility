"""grow_photos, grow_plant_profiles, grow_light_windows, grow_medium_defaults,
and grow_errors tables exist."""
import sqlite3
from database.init_db import create_db
import pytest


@pytest.fixture
def db_path(monkeypatch, tmp_path):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", path)
    create_db()
    return path


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1] for row in rows}


def test_grow_photos_table_with_telemetry_id_join(db_path):
    cols = _columns(db_path, "grow_photos")
    assert {"id", "unit_id", "taken_at", "file_path", "width_px", "height_px",
            "size_bytes", "telemetry_id", "classified_phase",
            "classifier_confidence"} <= cols


def test_grow_plant_profiles_table(db_path):
    cols = _columns(db_path, "grow_plant_profiles")
    assert {"id", "plant_type", "phase", "target_moisture_pct", "deadband_pct",
            "kp", "ki", "kd", "min_pulse_s", "max_pulse_s", "soak_window_min",
            "default_light_hours", "is_shipped"} <= cols


def test_grow_light_windows_table(db_path):
    cols = _columns(db_path, "grow_light_windows")
    assert {"id", "unit_id", "phase", "start_hh_mm", "end_hh_mm",
            "sort_order"} <= cols


def test_grow_medium_defaults_table(db_path):
    cols = _columns(db_path, "grow_medium_defaults")
    assert {"medium_type", "dry_raw", "wet_raw"} <= cols


def test_grow_errors_table(db_path):
    cols = _columns(db_path, "grow_errors")
    assert {"id", "unit_id", "timestamp_utc", "severity", "kind",
            "message", "details_json", "resolved_at"} <= cols

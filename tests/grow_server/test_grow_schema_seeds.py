"""Shipped plant profiles, medium calibration defaults, and grow_* app_settings
keys are seeded on first DB init."""
import sqlite3
from database.init_db import create_db
import pytest


@pytest.fixture
def conn(monkeypatch, tmp_path):
    """Set up a fresh DB and return an open sqlite3 connection."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", path)
    create_db()
    return sqlite3.connect(path)


def test_shipped_plant_profiles_seeded(conn):
    profiles = conn.execute(
        "SELECT plant_type, phase, target_moisture_pct, kp, ki, kd "
        "FROM grow_plant_profiles WHERE is_shipped=1"
    ).fetchall()
    types_phases = {(p[0], p[1]) for p in profiles}
    expected = {
        ("tomato", "seedling"), ("tomato", "vegetative"),
        ("tomato", "flowering"), ("tomato", "fruiting"),
        ("basil", "vegetative"),
        ("lettuce", "vegetative"),
        ("microgreens", "seedling"),
        ("pepper", "vegetative"),
        ("generic", "seedling"), ("generic", "vegetative"),
        ("generic", "flowering"),
    }
    assert expected <= types_phases
    for p in profiles:
        assert p[4] == 0, f"{p[0]} {p[1]} expected Ki=0, got {p[4]}"
        assert p[5] == 0, f"{p[0]} {p[1]} expected Kd=0, got {p[5]}"


def test_medium_defaults_seeded(conn):
    rows = dict(conn.execute(
        "SELECT medium_type, dry_raw FROM grow_medium_defaults"
    ).fetchall())
    assert rows.get("soil") == 200
    assert rows.get("coco") == 250
    assert rows.get("rockwool") == 300


def test_app_settings_grow_keys_seeded(conn):
    rows = dict(conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'grow_%'"
    ).fetchall())
    assert rows["grow_default_soak_window_min"] == "30"
    assert rows["grow_default_buffer_retention_days"] == "7"
    assert rows["grow_disk_warn_pct"] == "90"
    assert rows["grow_holiday_mode"] == "0"
    assert "grow_enrollment_key_hash" in rows
    assert len(rows["grow_enrollment_key_hash"]) > 30

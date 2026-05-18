"""grow_units table is created with the right columns."""
import sqlite3
from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    conn.close()
    return {row[1]: row[2] for row in rows}  # {name: type}


def test_grow_units_table_exists(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "grow_units")

    assert "id" in cols
    assert "hardware_serial" in cols
    assert "label" in cols
    assert "bearer_token_hash" in cols
    assert "is_active" in cols
    assert "current_phase" in cols
    assert "phase_set_by" in cols
    assert "plant_type" in cols
    assert "medium_type" in cols
    assert "soil_dry_raw" in cols
    assert "soil_wet_raw" in cols
    assert "buffer_retention_days" in cols
    assert "last_seen_at" in cols
    # C1 schema cleanup: last_known_state_json was a denormalised JSON
    # cache rewritten on every telemetry frame; the GET endpoints now
    # SELECT directly from grow_telemetry. Same for
    # light_phase_override_json (dead code superseded by grow_light_windows).
    assert "last_known_state_json" not in cols
    assert "light_phase_override_json" not in cols


def test_grow_units_is_idempotent(monkeypatch, tmp_path):
    """create_grow_schema can run twice without error (e.g. on restart)."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    create_db()  # should not raise
    cols = _columns(db_path, "grow_units")
    assert "id" in cols  # still there


# ── Phase 3 Task 1: firmware-reported metadata columns ─────────────────────
#
# Three nullable columns added to grow_units. Both fresh schema (CREATE
# TABLE in grow_schema.py) AND ALTER-TABLE migration in init_db.py paths
# must produce them, otherwise the firmware metadata writes from Task 2
# will fail on either path. We test create_db() because that exercises
# the migration list — and CREATE TABLE IF NOT EXISTS won't recreate, so
# the migration is the actual path for any DB that existed pre-Phase 3.


def test_grow_units_has_firmware_version_column(monkeypatch, tmp_path):
    """firmware_version is what the unit reports on capabilities — Task 2
    stores it on the units row so the diagnostics endpoint can show it
    without joining the latest capabilities frame."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "grow_units")
    assert "firmware_version" in cols
    assert cols["firmware_version"].upper() == "TEXT"


def test_grow_units_has_last_uptime_s_column(monkeypatch, tmp_path):
    """last_uptime_s lets diagnostics show 'unit has been up for 14 hrs'
    without keeping a separate time-series."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "grow_units")
    assert "last_uptime_s" in cols
    assert cols["last_uptime_s"].upper() == "REAL"


def test_grow_units_has_last_buffer_size_column(monkeypatch, tmp_path):
    """last_buffer_size surfaces when a unit's outbound queue is backing
    up — early warning of a network/disk issue."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "grow_units")
    assert "last_buffer_size" in cols
    assert cols["last_buffer_size"].upper() == "INTEGER"

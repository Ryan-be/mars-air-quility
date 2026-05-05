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
    assert "last_known_state_json" in cols


def test_grow_units_is_idempotent(monkeypatch, tmp_path):
    """create_grow_schema can run twice without error (e.g. on restart)."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    create_db()  # should not raise
    cols = _columns(db_path, "grow_units")
    assert "id" in cols  # still there

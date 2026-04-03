"""Tests for HotTier SQLite persistence: write, reload, prune."""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.hot_tier import HotTier


def _reading(tvoc: float = 100.0, seconds_ago: int = 0) -> NormalisedReading:
    return NormalisedReading(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        source="test",
        tvoc_ppb=tvoc,
        temperature_c=22.0,
        humidity_pct=50.0,
    )


# ── Schema ────────────────────────────────────────────────────────────────────

def test_hot_tier_table_created_by_create_db(tmp_path):
    """create_db() must create the hot_tier table."""
    import database.init_db as dbi
    db_path = str(tmp_path / "test.db")
    original = dbi.DB_FILE
    dbi.DB_FILE = db_path
    try:
        dbi.create_db()
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "hot_tier" in tables
    finally:
        dbi.DB_FILE = original


# ── Write on push ─────────────────────────────────────────────────────────────

def test_push_writes_row_to_db(tmp_path):
    """push() must insert one row into the hot_tier table."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    tier = HotTier(maxlen=3600, db_file=db_path)
    tier.push(_reading(tvoc=150.0))

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT tvoc_ppb FROM hot_tier").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == pytest.approx(150.0)


def test_push_stores_all_sensor_fields(tmp_path):
    """push() must store every NormalisedReading field correctly."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    r = NormalisedReading(
        timestamp=datetime(2026, 4, 3, 12, 0, 0, tzinfo=timezone.utc),
        source="test",
        tvoc_ppb=200.0,
        eco2_ppm=800.0,
        temperature_c=22.5,
        humidity_pct=55.0,
        pm25_ug_m3=5.0,
        co_ppb=None,
        no2_ppb=None,
        nh3_ppb=None,
    )
    tier = HotTier(maxlen=3600, db_file=db_path)
    tier.push(r)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT * FROM hot_tier").fetchone()
    conn.close()

    # Columns: id, timestamp, source, tvoc_ppb, eco2_ppm, temperature_c,
    #          humidity_pct, pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
    assert row[2] == "test"
    assert row[3] == pytest.approx(200.0)  # tvoc_ppb
    assert row[4] == pytest.approx(800.0)  # eco2_ppm
    assert row[5] == pytest.approx(22.5)   # temperature_c
    assert row[6] == pytest.approx(55.0)   # humidity_pct
    assert row[7] == pytest.approx(5.0)    # pm25_ug_m3
    assert row[8] is None                  # co_ppb
    assert row[9] is None                  # no2_ppb
    assert row[10] is None                 # nh3_ppb


def test_push_with_no_db_does_not_raise():
    """HotTier(db_file=None) must work exactly as before — no DB operations."""
    tier = HotTier(maxlen=3600, db_file=None)
    tier.push(_reading(tvoc=100.0))
    assert tier.size() == 1


# ── Reload on init ────────────────────────────────────────────────────────────

def test_init_loads_last_60_min_from_db(tmp_path):
    """HotTier.__init__ must pre-fill the deque from the DB."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    # Write 3 readings into DB via first instance
    tier1 = HotTier(maxlen=3600, db_file=db_path)
    tier1.push(_reading(tvoc=10.0, seconds_ago=120))
    tier1.push(_reading(tvoc=20.0, seconds_ago=60))
    tier1.push(_reading(tvoc=30.0, seconds_ago=0))

    # Reload — should get all 3 back
    tier2 = HotTier(maxlen=3600, db_file=db_path)
    assert tier2.size() == 3
    assert tier2.latest().tvoc_ppb == pytest.approx(30.0)


def test_init_ignores_rows_older_than_60_min(tmp_path):
    """HotTier.__init__ must NOT load rows older than 60 minutes."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    # Insert a row that is 2 hours old directly into the DB
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO hot_tier (timestamp, source, tvoc_ppb) VALUES (?, ?, ?)",
        (old_ts, "test", 999.0),
    )
    conn.commit()
    conn.close()

    tier = HotTier(maxlen=3600, db_file=db_path)
    assert tier.size() == 0  # old row ignored


def test_init_with_no_db_starts_empty():
    """HotTier(db_file=None) must start empty — no DB load attempted."""
    tier = HotTier(maxlen=3600, db_file=None)
    assert tier.size() == 0


# ── Pruning ───────────────────────────────────────────────────────────────────

def test_prune_old_deletes_rows_older_than_60_min(tmp_path):
    """prune_old() must delete rows with timestamp < now - 60 min."""
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    tier = HotTier(maxlen=3600, db_file=db_path)

    # Insert one recent and one old row via push
    tier.push(_reading(tvoc=1.0, seconds_ago=30))      # 30s ago — keep
    tier.push(_reading(tvoc=2.0, seconds_ago=3700))    # >60min ago — delete

    conn = sqlite3.connect(db_path)
    count_before = conn.execute("SELECT COUNT(*) FROM hot_tier").fetchone()[0]
    conn.close()
    assert count_before == 2

    tier.prune_old()

    conn = sqlite3.connect(db_path)
    rows_after = conn.execute("SELECT tvoc_ppb FROM hot_tier").fetchall()
    conn.close()
    assert len(rows_after) == 1
    assert rows_after[0][0] == pytest.approx(1.0)  # recent row survives


def test_prune_old_with_no_db_does_not_raise():
    """prune_old() on a no-DB HotTier must be a no-op."""
    tier = HotTier(maxlen=3600, db_file=None)
    tier.prune_old()  # must not raise


# ── last_minutes after reload ─────────────────────────────────────────────────

def test_last_minutes_returns_reloaded_readings_within_window(tmp_path):
    """Readings reloaded from DB must have UTC-aware timestamps so that
    last_minutes() comparisons work correctly after a restart.

    This is the highest-risk path: isoformat() → SQLite string → fromisoformat()
    must round-trip to a timezone-aware datetime, otherwise the >= comparison
    in last_minutes() raises TypeError.
    """
    import database.init_db as dbi
    import mlss_monitor.hot_tier as ht_mod

    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    ht_mod.DB_FILE = db_path
    dbi.create_db()

    # Push two readings: one 2 minutes ago, one 10 minutes ago
    tier1 = HotTier(maxlen=3600, db_file=db_path)
    tier1.push(_reading(tvoc=5.0, seconds_ago=600))   # 10 min ago
    tier1.push(_reading(tvoc=9.0, seconds_ago=120))   # 2 min ago

    # Reload
    tier2 = HotTier(maxlen=3600, db_file=db_path)
    assert tier2.size() == 2

    # last_minutes(5) should return only the 2-min-ago reading
    recent = tier2.last_minutes(5)
    assert len(recent) == 1
    assert recent[0].tvoc_ppb == pytest.approx(9.0)

    # last_minutes(15) should return both
    all_recent = tier2.last_minutes(15)
    assert len(all_recent) == 2

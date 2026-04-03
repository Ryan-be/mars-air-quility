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

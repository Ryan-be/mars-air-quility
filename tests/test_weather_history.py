"""Tests for get_weather_history db function."""
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

from database.db_logger import get_weather_history


def _setup_db(tmp_path, n=5):
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE weather_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            temp REAL, humidity REAL, feels_like REAL,
            wind_speed REAL, weather_code INTEGER, uv_index REAL
        )
    """)
    now = datetime.utcnow()
    for i in range(n):
        ts = (now - timedelta(hours=n - i)).isoformat()
        conn.execute(
            "INSERT INTO weather_log "
            "(timestamp, temp, humidity, feels_like, wind_speed, weather_code, uv_index) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, 15.0 + i, 60 + i, 14.0 + i, 10.0, 1, 2.0),
        )
    conn.commit()
    conn.close()
    return db


def test_returns_rows_after_since(tmp_path):
    db   = _setup_db(tmp_path)
    since = (datetime.utcnow() - timedelta(hours=3)).isoformat()
    with patch("database.db_logger.DB_FILE", db):
        rows = get_weather_history(since)
    assert len(rows) >= 2
    for row in rows:
        assert row["timestamp"] >= since


def test_returns_all_keys(tmp_path):
    db    = _setup_db(tmp_path)
    since = (datetime.utcnow() - timedelta(hours=10)).isoformat()
    with patch("database.db_logger.DB_FILE", db):
        rows = get_weather_history(since)
    assert len(rows) > 0
    for key in ("timestamp", "temp", "humidity", "feels_like",
                "wind_speed", "weather_code", "uv_index"):
        assert key in rows[0]


def test_empty_when_no_recent_data(tmp_path):
    db    = _setup_db(tmp_path)
    since = datetime.utcnow().isoformat()
    with patch("database.db_logger.DB_FILE", db):
        rows = get_weather_history(since)
    assert rows == []


def test_ordered_ascending(tmp_path):
    db    = _setup_db(tmp_path)
    since = (datetime.utcnow() - timedelta(hours=10)).isoformat()
    with patch("database.db_logger.DB_FILE", db):
        rows = get_weather_history(since)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)

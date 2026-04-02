import sqlite3
import tempfile
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch


def test_get_24h_baselines_returns_medians(tmp_path):
    """Median of known values is returned per sensor field."""
    from database.db_logger import get_24h_baselines

    db_path = str(tmp_path / "test.db")

    # Create minimal sensor_data table
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            temperature REAL, humidity REAL,
            eco2 INTEGER, tvoc INTEGER,
            pm2_5 REAL, gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )
    """)
    # Insert 3 rows within last 24h
    now = datetime.utcnow()
    for minutes_ago, (t, h, e, v, pm, co, no2, nh3) in enumerate([
        (21.0, 55.0, 600, 180, 8.0,  1.0, 0.05, 6.0),
        (22.0, 57.0, 620, 200, 10.0, 1.5, 0.07, 7.0),
        (23.0, 59.0, 640, 220, 12.0, 2.0, 0.09, 8.0),
    ]):
        conn.execute(
            "INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, "
            "pm2_5, gas_co, gas_no2, gas_nh3) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((now - timedelta(minutes=minutes_ago * 10)).isoformat(),
             t, h, e, v, pm, co, no2, nh3),
        )
    conn.commit()
    conn.close()

    with patch("database.db_logger.DB_FILE", db_path):
        result = get_24h_baselines()

    assert result["tvoc_ppb"] == pytest.approx(200.0)       # median of [180, 200, 220]
    assert result["eco2_ppm"] == pytest.approx(620.0)       # median of [600, 620, 640]
    assert result["temperature_c"] == pytest.approx(22.0)
    assert result["humidity_pct"] == pytest.approx(57.0)
    assert result["pm25_ug_m3"] == pytest.approx(10.0)
    assert result["co_ppb"] == pytest.approx(1.5)
    assert result["no2_ppb"] == pytest.approx(0.07)
    assert result["nh3_ppb"] == pytest.approx(7.0)  # median of [6, 7, 8]


def test_get_24h_baselines_returns_none_when_no_data(tmp_path):
    """None returned for channels with no readings."""
    from database.db_logger import get_24h_baselines

    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY, timestamp TEXT,
            temperature REAL, humidity REAL, eco2 INTEGER, tvoc INTEGER,
            pm2_5 REAL, gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )
    """)
    conn.commit()
    conn.close()

    with patch("database.db_logger.DB_FILE", db_path):
        result = get_24h_baselines()

    for key in ("tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
                "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb"):
        assert result[key] is None

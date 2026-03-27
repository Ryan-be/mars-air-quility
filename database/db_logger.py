import sqlite3
from datetime import datetime, timedelta

from config import config

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")


def log_sensor_data(temp, hum, eco2, tvoc, annotation=None, fan_power_w=None, vpd_kpa=None):
    """
    Log sensor data into the SQLite database.

    :param temp: temperature in °C
    :param hum: relative humidity in %
    :param eco2: equivalent CO₂ in ppm
    :param tvoc: total VOC in ppb
    :param annotation: optional text annotation
    :param fan_power_w: current fan power consumption in watts (None if unavailable)
    :param vpd_kpa: vapour pressure deficit in kPa (None falls back to NULL in DB)
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO sensor_data
            (timestamp, temperature, humidity, eco2, tvoc, annotation, fan_power_w, vpd_kpa)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), temp, hum, eco2, tvoc, annotation, fan_power_w, vpd_kpa))

    conn.commit()
    conn.close()


def get_sensor_data():
    """
    Fetch all sensor data from the database, ordered by timestamp in descending order.
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT * FROM sensor_data ORDER BY timestamp DESC")
    rows = cur.fetchall()

    conn.close()
    return rows


def get_sensor_data_by_date(start_date, end_date):
    """
    Fetch sensor data within a specific date range.
    :param start_date:
    :param end_date:
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM sensor_data
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY timestamp DESC
    """, (start_date, end_date))

    rows = cur.fetchall()
    conn.close()
    return rows


def add_annotation(sensor_id, annotation):
    """
    Add an annotation to a sensor data entry.
    :param sensor_id:
    :param annotation:
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (annotation, sensor_id))

    conn.commit()
    conn.close()


def remove_annotation(sensor_id):
    """
    Remove an annotation from a sensor data entry.
    :param sensor_id:
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = NULL
        WHERE id = ?
    """, (sensor_id,))

    conn.commit()
    conn.close()


def edit_annotation(sensor_id, new_annotation):
    """
    Edit an existing annotation for a sensor data entry.
    :param sensor_id:
    :param new_annotation:
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (new_annotation, sensor_id))

    conn.commit()
    conn.close()


def get_fan_settings():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT tvoc_min, tvoc_max, temp_min, temp_max, enabled FROM fan_settings ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {"tvoc_min": 0, "tvoc_max": 500, "temp_min": 0.0, "temp_max": 20.0, "enabled": False}
    return {
        "tvoc_min": row[0],
        "tvoc_max": row[1],
        "temp_min": row[2],
        "temp_max": row[3],
        "enabled": bool(row[4]),
    }


def log_weather(temp, humidity, feels_like, wind_speed, weather_code, uv_index):
    """Store one hourly weather snapshot."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weather_log (timestamp, temp, humidity, feels_like, wind_speed, weather_code, uv_index)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), temp, humidity, feels_like, wind_speed, weather_code, uv_index))
    conn.commit()
    conn.close()


def get_latest_weather(max_age_minutes: int = 90):
    """Return the most recent weather row if it is newer than max_age_minutes, else None."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(minutes=max_age_minutes)).isoformat()
    cur.execute("""
        SELECT temp, humidity, feels_like, wind_speed, weather_code, uv_index, timestamp
        FROM weather_log
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (since,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "temp": row[0], "humidity": row[1], "feels_like": row[2],
        "wind_speed": row[3], "weather_code": row[4], "uv_index": row[5],
        "fetched_at": row[6],
    }


def cleanup_old_weather(days: int = 7):
    """Delete weather rows older than `days` days."""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    cur.execute("DELETE FROM weather_log WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()


def get_location():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM app_settings WHERE key IN ('location_lat','location_lon','location_name')")
    rows = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    lat = rows.get("location_lat")
    lon = rows.get("location_lon")
    return {
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "name": rows.get("location_name", ""),
    }


def save_location(lat, lon, name=""):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    upsert = (
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    for key, val in [("location_lat", str(lat)), ("location_lon", str(lon)), ("location_name", name)]:
        cur.execute(upsert, (key, val))
    conn.commit()
    conn.close()


def update_fan_settings(tvoc_min, tvoc_max, temp_min, temp_max, enabled):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        UPDATE fan_settings
        SET tvoc_min = ?, tvoc_max = ?, temp_min = ?, temp_max = ?, enabled = ?
        WHERE id = (SELECT MAX(id) FROM fan_settings)
    """, (tvoc_min, tvoc_max, temp_min, temp_max, int(enabled)))
    conn.commit()
    conn.close()

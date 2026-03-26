import sqlite3
from datetime import datetime

from config import config

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")


def log_sensor_data(temp, hum, eco2, tvoc, annotation=None, fan_power_w=None):
    """
    Log sensor data into the SQLite database.
    :param temp:
    :param hum:
    :param eco2:
    :param tvoc:
    :param annotation:
    :param fan_power_w: current fan power consumption in watts (None if unavailable)
    :return:
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, annotation, fan_power_w)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), temp, hum, eco2, tvoc, annotation, fan_power_w))

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

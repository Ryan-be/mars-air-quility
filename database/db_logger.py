import sqlite3
from datetime import datetime

DB_FILE = "data/sensor_data.db"


def log_sensor_data(temp, hum, eco2, tvoc, annotation=None):
    """
    Log sensor data into the SQLite database.
    :param temp:
    :param hum:
    :param eco2:
    :param tvoc:
    :param annotation:
    :return:
    """
    conn = sqlite3.connect("data/sensor_data.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, annotation)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datetime.utcnow().isoformat(), temp, hum, eco2, tvoc, annotation))

    conn.commit()
    conn.close()


def get_sensor_data():
    """
    Fetch all sensor data from the database, ordered by timestamp in descending order.
    :return:
    """
    conn = sqlite3.connect("data/sensor_data.db")
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
    conn = sqlite3.connect("data/sensor_data.db")
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
    conn = sqlite3.connect("data/sensor_data.db")
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
    conn = sqlite3.connect("data/sensor_data.db")
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
    conn = sqlite3.connect("%s" % DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (new_annotation, sensor_id))

    conn.commit()
    conn.close()


def auto_fan_control():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT tvoc_min, tvoc_max, temp_min, temp_max, enabled FROM fan_settings ORDER BY id DESC LIMIT 1")
    rows = cur.fetchall()
    conn.close()
    return rows

def update_fan_settings(tvoc_min, tvoc_max, temp_min, temp_max, enabled=True):
    """
    Update the fan settings in the database.
    :param tvoc_min: Minimum TVOC level to enable the fan.
    :param tvoc_max: Maximum TVOC level to enable the fan.
    :param temp_min: Minimum temperature to enable the fan.
    :param temp_max: Maximum temperature to enable the fan.
    :param enabled: Whether auto fan control is enabled.
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE fan_settings
        SET tvoc_min = ?, tvoc_max = ?, temp_min = ?, temp_max = ?, enabled = ?
        WHERE id = (SELECT MAX(id) FROM fan_settings)
    """, (tvoc_min, tvoc_max, temp_min, temp_max, enabled))

    conn.commit()
    conn.close()

def update_fan_state(enabled):
    """
    Update the fan enabled state in the database.
    :param enabled: Whether auto fan control is enabled.
    """
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE fan_settings
        SET enabled = ?
        WHERE id = (SELECT MAX(id) FROM fan_settings)
    """, (enabled,))

    conn.commit()
    conn.close()
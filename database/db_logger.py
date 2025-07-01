import sqlite3
from datetime import datetime

def log_sensor_data(temp, hum, eco2, tvoc, annotation=None):
    conn = sqlite3.connect("data/sensor_data.db")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, annotation)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), temp, hum, eco2, tvoc, annotation))

    conn.commit()
    conn.close()
def get_sensor_data():
    conn = sqlite3.connect("data/sensor_data.db")
    cur = conn.cursor()

    cur.execute("SELECT * FROM sensor_data ORDER BY timestamp DESC")
    rows = cur.fetchall()

    conn.close()
    return rows

def get_sensor_data_by_date(start_date, end_date):
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
    conn = sqlite3.connect("data/sensor_data.db")
    cur = conn.cursor()

    cur.execute("""
        UPDATE sensor_data
        SET annotation = ?
        WHERE id = ?
    """, (annotation, sensor_id))

    conn.commit()
    conn.close()


import sqlite3

from config import config

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")


def create_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sensor_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        temperature REAL,
        humidity REAL,
        eco2 INTEGER,
        tvoc INTEGER,
        annotation TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fan_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tvoc_min INTEGER,
        tvoc_max INTEGER,
        temp_min REAL,
        temp_max REAL,
        enabled INTEGER DEFAULT 0
    );
    """)

    cur.execute("SELECT COUNT(*) FROM fan_settings")
    if cur.fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO fan_settings (tvoc_min, tvoc_max, temp_min, temp_max, enabled)
        VALUES (?, ?, ?, ?, ?)
        """, (0, 500, 0.0, 20.0, 0))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_db()
    print("✅ SQLite database created at data/sensor_data.db")

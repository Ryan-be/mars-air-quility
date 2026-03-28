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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS weather_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        temp REAL,
        humidity REAL,
        feels_like REAL,
        wind_speed REAL,
        weather_code INTEGER,
        uv_index REAL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS inferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at DATETIME NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        title TEXT NOT NULL,
        description TEXT,
        action TEXT,
        evidence TEXT,
        confidence REAL NOT NULL DEFAULT 0.5,
        sensor_data_start_id INTEGER,
        sensor_data_end_id INTEGER,
        annotation TEXT,
        user_notes TEXT,
        dismissed INTEGER DEFAULT 0
    );
    """)

    # Migrations: add columns introduced after initial release
    for migration in [
        "ALTER TABLE sensor_data ADD COLUMN fan_power_w REAL",
        "ALTER TABLE sensor_data ADD COLUMN vpd_kpa REAL",
    ]:
        try:
            cur.execute(migration)
        except Exception:  # pylint: disable=broad-except
            pass  # column already exists

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

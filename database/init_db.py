import sqlite3

DB_FILE = "data/sensor_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # Sensor data table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sensor_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        temperature REAL,
        humidity REAL,
        eco2 INTEGER,
        tvoc INTEGER,
        pm1_0 REAL,
        pm2_5 REAL,
        pm10 REAL,
        annotation TEXT
    );
    """)

    # Fan settings table
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

    # Ensure at least one row exists with defaults
    cur.execute("SELECT COUNT(*) FROM fan_settings")
    if cur.fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO fan_settings (tvoc_min, tvoc_max, temp_min, temp_max, enabled)
        VALUES (?, ?, ?, ?, ?)
        """, (0, 500, 0.0, 20.0, 0))

    conn.commit()
    conn.close()
    print("✅ Database initialized")

if __name__ == "__main__":
    init_db()

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS inference_thresholds (
        key TEXT PRIMARY KEY,
        default_value REAL NOT NULL,
        user_value REAL,
        unit TEXT NOT NULL,
        label TEXT NOT NULL,
        description TEXT
    );
    """)

    # Seed default thresholds if table is empty
    cur.execute("SELECT COUNT(*) FROM inference_thresholds")
    if cur.fetchone()[0] == 0:
        _defaults = [
            ("tvoc_high",     500,  "ppb", "TVOC High",              "WHO 'high' threshold for total volatile organic compounds"),
            ("tvoc_moderate", 250,  "ppb", "TVOC Moderate",          "WHO 'good' ceiling — above this triggers spike detection"),
            ("eco2_cognitive",1000, "ppm", "eCO₂ Cognitive",         "CO₂ level where cognitive impairment begins"),
            ("eco2_danger",   2000, "ppm", "eCO₂ Danger",            "CO₂ level causing headaches and drowsiness"),
            ("temp_high",     28.0, "°C",  "Temperature High",       "Upper comfort zone boundary"),
            ("temp_low",      15.0, "°C",  "Temperature Low",        "Lower comfort zone boundary"),
            ("hum_high",      70.0, "%",   "Humidity High",          "Above this promotes mould and dust mites"),
            ("hum_low",       30.0, "%",   "Humidity Low",           "Below this causes dry skin and irritation"),
            ("vpd_low",       0.4,  "kPa", "VPD Low",               "Below this air is too saturated for plants"),
            ("vpd_high",      1.6,  "kPa", "VPD High",              "Above this plants close stomata (stress)"),
            ("spike_factor",  2.0,  "×",   "Spike Factor",          "Multiplier above rolling mean to detect spikes"),
            ("min_readings",  6,    "count","Minimum Readings",      "Data points required before analysis runs"),
            ("mould_hum",     70.0, "%",   "Mould Risk Humidity",   "Sustained humidity above this promotes mould growth"),
            ("mould_temp",    20.0, "°C",  "Mould Risk Temp",       "Warm temperatures above this accelerate mould growth"),
            ("mould_hours",   4,    "hrs", "Mould Risk Duration",   "Hours of sustained conditions before flagging mould risk"),
        ]
        for key, default, unit, label, desc in _defaults:
            cur.execute(
                "INSERT INTO inference_thresholds (key, default_value, unit, label, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, default, unit, label, desc),
            )

    # Ensure new thresholds are added to existing databases
    _new_thresholds = [
        ("mould_hum",   70.0, "%",   "Mould Risk Humidity",  "Sustained humidity above this promotes mould growth"),
        ("mould_temp",  20.0, "°C",  "Mould Risk Temp",      "Warm temperatures above this accelerate mould growth"),
        ("mould_hours", 4,    "hrs", "Mould Risk Duration",  "Hours of sustained conditions before flagging mould risk"),
    ]
    for key, default, unit, label, desc in _new_thresholds:
        cur.execute(
            "INSERT OR IGNORE INTO inference_thresholds (key, default_value, unit, label, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, default, unit, label, desc),
        )

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

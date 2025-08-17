import sqlite3


def create_db():
    conn = sqlite3.connect("data/sensor_data.db")
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

    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_db()
    print("✅ SQLite database created at data/sensor_data.db")

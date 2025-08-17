import csv
import sqlite3

conn = sqlite3.connect("data/sensor_data.db")
cur = conn.cursor()

with open("logs/default.csv", newline='') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        cur.execute("""
            INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, annotation)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            row["timestamp"],
            float(row["temperature"]),
            float(row["humidity"]),
            int(row["eco2"]),
            int(row["tvoc"]),
            None  # or custom annotation if needed
        ))

conn.commit()
conn.close()
print("✅ CSV data loaded")

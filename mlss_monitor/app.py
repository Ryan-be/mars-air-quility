from flask import Flask, send_file, render_template, jsonify, request
import pandas as pd
from datetime import datetime, timedelta
import csv
import os
import time
import board
import io
import busio
from adafruit_ahtx0 import AHTx0
from adafruit_sgp30 import Adafruit_SGP30
from config import config
from sensors.display import update_display
from sensors.aht20 import read_aht20
from sensors.sgp30 import read_sgp30
from database.db_logger import log_sensor_data, get_sensor_data, get_sensor_data_by_date, add_annotation
from datetime import datetime, timedelta
import psutil
import subprocess

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
)


LOG_INTERVAL = int(config.get("LOG_INTERVAL", "10"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up from mlss_monitor
DATA_FILE = os.path.join(BASE_DIR, "data", "default.csv")

i2c = busio.I2C(board.SCL, board.SDA)

try:
    aht20 = AHTx0(i2c)
except (OSError, ValueError) as e:
    print(f"Failed to initialize AHT20 sensor (I2C error or device not found): {e}")
    sgp30 = None
except Exception as e:
    print(f"Unexpected error initializing AHT30 sensor: {e}")
    sgp30 = None

try:
    sgp30 = Adafruit_SGP30(i2c)
except (OSError, ValueError) as e:
    print(f"Failed to initialize SGP30 sensor (I2C error or device not found): {e}")
    sgp30 = None
except Exception as e:
    print(f"Unexpected error initializing SGP30 sensor: {e}")
    sgp30 = None

def read_sensors():
    ts = int(datetime.now().timestamp() * 1000)  # in ms
    temperature, humidity, eco2, tvoc = 0, 0, 0, 0

    # Read AHT20 sensor data if available
    if aht20:
        try:
            print("reading aht20")
            temperature, humidity = read_aht20()
            print(f" temperature and humidity = {temperature}, {humidity}")
        except Exception as e:
            print(f"Error reading AHT20 sensor: {e}")

    # Read SGP30 sensor data if available
    if sgp30:
        try:
            eco2, tvoc = read_sgp30()
        except Exception as e:
            print(f"Error reading SGP30 sensor: {e}")

    # Return the timestamp and sensor data
    return ts, temperature, humidity, eco2, tvoc

def log_data():
    ts, temp, hum, eco2, tvoc = read_sensors()
    write_header = not os.path.exists(DATA_FILE)
    with open(DATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "temperature", "humidity", "eco2", "tvoc"])
        writer.writerow([ts, temp, hum, eco2, tvoc])
    log_sensor_data(temp, hum, eco2, tvoc)

#    update_display(temp, hum, eco2, tvoc)

@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/download")
def download_data():
    return send_file(
        DATA_FILE,
        mimetype="text/csv",
        as_attachment=True,
        download_name="mlss_data.csv"
    )

@app.route("/api/data")
def get_data():
    range_param = request.args.get("range", "24h")
    now = datetime.utcnow()

    range_map = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
    }

    if range_param in range_map:
        since = now - range_map[range_param]
    else:
        since = datetime.min  # 'all'

    if not os.path.exists(DATA_FILE):
        return jsonify({"error": "Data file not found."}), 404

    try:
        df = pd.read_csv(DATA_FILE)
        # Only convert rows that are numeric
        df = df[pd.to_numeric(df["timestamp"], errors="coerce").notnull()]
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit='ms')
        df = df[df["timestamp"] >= since]
    except Exception as e:
        return jsonify({"error": f"Error reading data: {str(e)}"}), 500

    return jsonify(df.to_dict(orient="records"))

@app.route("/system_health")
def system_health():
    status = {}

    # Check AHT20
    if aht20:
        status["AHT20"] = "OK"
    else:
        status["AHT20"] = "UNAVAILABLE"

    # Check SGP30
    if sgp30:
        status["SGP30"] = "OK"
    else:
        status["SGP30"] = "UNAVAILABLE"

    # System stats
    try:
        uptime_seconds = float(subprocess.check_output(["cat", "/proc/uptime"]).decode().split()[0])
        uptime_str = str(timedelta(seconds=int(uptime_seconds)))
    except Exception:
        uptime_str = "Unknown"

    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()

    status["uptime"] = uptime_str
    status["cpu_usage"] = f"{cpu_percent:.1f}%"
    status["memory_used"] = f"{memory.used // (1024**2)} MB"
    status["memory_total"] = f"{memory.total // (1024**2)} MB"
    status["memory_percent"] = f"{memory.percent:.1f}%"

    return jsonify(status)

def main():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w"): pass

    from threading import Thread

    def background_log():
        while True:
            log_data()
            time.sleep(LOG_INTERVAL)

    Thread(target=background_log, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()

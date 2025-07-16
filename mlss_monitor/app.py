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
from database.db_logger import log_sensor_data, get_sensor_data, get_sensor_data_by_date, add_annotation, remove_annotation
from datetime import datetime, timedelta
import psutil
import subprocess
from external_api_interfaces.kasa_smart_plug import KasaSmartPlug
import asyncio


app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
)


LOG_INTERVAL = int(config.get("LOG_INTERVAL", "10"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up from mlss_monitor
FAN_KASA_SMART_PLUG_IP = config.get("FAN_KASA_SMART_PLUG_IP", "192.168.1.63")

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

    # Update SGP30 humidity compensation if available
    if sgp30 and humidity > 0:
        try:
            sgp30.set_iaq_relative_humidity(celcius=temperature, relative_humidity=humidity)
        except Exception as e:
            print(f"Error setting SGP30 humidity compensation: {e}")

    # Read SGP30 sensor data if available
    if sgp30:
        try:
            eco2, tvoc= read_sgp30()
            print(f"eco2: {eco2}, tvoc: {tvoc}")
        except Exception as e:
            print(f"Error reading SGP30 sensor: {e}")

    # Return the timestamp and sensor data
    return ts, temperature, humidity, eco2, tvoc

# Initialize the KasaSmartPlug instance
fan_smart_plug = KasaSmartPlug(FAN_KASA_SMART_PLUG_IP)

def log_data():
    ts, temp, hum, eco2, tvoc = read_sensors()
    log_sensor_data(temp, hum, eco2, tvoc)

    # Add logic to control the smart plug based on temperature and TVOC
    try:
        if temp > 26 or tvoc > 500:
            asyncio.run(fan_smart_plug.switch(True))  # Turn on the plug
        else:
            asyncio.run(fan_smart_plug.switch(False))  # Turn off the plug
    except Exception as e:
        print(f"Error controlling smart plug fan: {e}")


@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/download")
def download_data():
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

    try:
        # Fetch data from SQLite database
        rows = get_sensor_data_by_date(since.isoformat(), now.isoformat())

        # Write data to a CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "timestamp", "temperature", "humidity", "eco2", "tvoc", "annotation"])  # Header
        writer.writerows(rows)
        output.seek(0)

        # Serve the CSV file
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="sensor_data.csv"
        )
    except Exception as e:
        return jsonify({"error": f"Error generating CSV: {str(e)}"}), 500

@app.route("/api/data")
def get_data():
    range_param = request.args.get("range", "24h")
    now = datetime.utcnow()

    range_map = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "24h": timedelta(hours=24),
    }

    if range_param in range_map:
        since = now - range_map[range_param]
    else:
        since = datetime.min  # 'all'

    try:
        # Fetch data from SQLite database
        rows = get_sensor_data_by_date(since.isoformat(), now.isoformat())
        data = [
            {
                "id": row[0],
                "timestamp": row[1],
                "temperature": row[2],
                "humidity": row[3],
                "eco2": row[4],
                "tvoc": row[5],
                "annotation": row[6],
            }
            for row in rows
        ]
    except Exception as e:
        return jsonify({"error": f"Error reading data: {str(e)}"}), 500

    return jsonify(data)

@app.route("/api/annotate", methods=["POST"])
def annotate_point_query():
    try:
        # Get the 'point' query parameter
        entry_id = request.args.get("point", type=int)
        if not entry_id:
            return jsonify({"error": "'point' query parameter is required and must be an integer."}), 400

        # Parse JSON payload
        data = request.get_json()
        annotation = data.get("annotation")
        if not annotation:
            return jsonify({"error": "'annotation' is required in the request body."}), 400

        # Add annotation to the database
        add_annotation(entry_id, annotation)

        return jsonify({"message": "Annotation added successfully."}), 200
    except Exception as e:
        return jsonify({"error": f"Error adding annotation: {str(e)}"}), 500

@app.route("/api/annotate", methods=["DELETE"])
def remove_annotation_query():
    try:
        # Get the 'point' query parameter
        entry_id = request.args.get("point", type=int)
        if not entry_id:
            return jsonify({"error": "'point' query parameter is required and must be an integer."}), 400

        # Remove annotation from the database
        remove_annotation(entry_id)

        return jsonify({"message": "Annotation removed successfully."}), 200
    except Exception as e:
        return jsonify({"error": f"Error removing annotation: {str(e)}"}), 500


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

    from threading import Thread

    def background_log():
        while True:
            log_data()
            time.sleep(LOG_INTERVAL)

    Thread(target=background_log, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()

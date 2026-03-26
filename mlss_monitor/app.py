from flask import Flask, send_file, render_template, jsonify, request

import csv
import os
import time
import board
import io
import busio
from adafruit_ahtx0 import AHTx0
from adafruit_sgp30 import Adafruit_SGP30
from config import config
from sensors.aht20 import read_aht20
from sensors.sgp30 import read_sgp30
from database.db_logger import (
    log_sensor_data, get_sensor_data_by_date, add_annotation, remove_annotation,
    get_fan_settings, update_fan_settings,
)
from datetime import datetime, timedelta
import psutil
import subprocess
from external_api_interfaces.kasa_smart_plug import KasaSmartPlug
import asyncio
from threading import Thread

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates"),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static"),
)

LOG_INTERVAL = int(config.get("LOG_INTERVAL", "10"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up from mlss_monitor
FAN_KASA_SMART_PLUG_IP = config.get("FAN_KASA_SMART_PLUG_IP", "192.168.1.63")

# Global variables to store fan state and mode
fan_mode = "auto"  # Default mode: auto
fan_state = "off"  # Default state: off
service_start_time = datetime.utcnow()

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
            eco2, tvoc = read_sgp30()
            print(f"eco2: {eco2}, tvoc: {tvoc}")
        except Exception as e:
            print(f"Error reading SGP30 sensor: {e}")

    # Return the timestamp and sensor data
    return ts, temperature, humidity, eco2, tvoc


# Initialize the KasaSmartPlug instance
fan_smart_plug = KasaSmartPlug(FAN_KASA_SMART_PLUG_IP)

# Create an event loop for the background thread
thread_loop = asyncio.new_event_loop()

def start_thread_event_loop():
    asyncio.set_event_loop(thread_loop)
    thread_loop.run_forever()


# Start the thread with the event loop
thread = Thread(target=start_thread_event_loop, daemon=True)
thread.start()

def log_data():
    global fan_state
    _, temp, hum, eco2, tvoc = read_sensors()

    # Read fan power consumption (fire-and-forget friendly; falls back to None)
    fan_power_w = None
    try:
        power_future = asyncio.run_coroutine_threadsafe(fan_smart_plug.get_power(), thread_loop)
        power_data = power_future.result(timeout=5)
        fan_power_w = power_data.get("power_w")
    except Exception:
        pass

    log_sensor_data(temp, hum, eco2, tvoc, fan_power_w=fan_power_w)

    settings = get_fan_settings()
    if settings["enabled"]:
        try:
            if temp > settings["temp_max"] or tvoc > settings["tvoc_max"]:
                fan_state = "on"
                asyncio.run_coroutine_threadsafe(fan_smart_plug.switch(True), thread_loop)
            else:
                fan_state = "off"
                asyncio.run_coroutine_threadsafe(fan_smart_plug.switch(False), thread_loop)
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


@app.route("/api/fan", methods=["POST"])
def control_fan():
    global fan_mode, fan_state
    try:
        state = request.args.get("state")
        if state not in ["on", "off", "auto"]:
            return jsonify({"error": "'state' must be 'on', 'off', or 'auto'."}), 400

        if state == "auto":
            fan_mode = "auto"
            fan_state = "off"
        else:
            fan_mode = "manual"
            fan_state = state
            asyncio.run_coroutine_threadsafe(fan_smart_plug.switch(state == "on"), thread_loop).result()

        return jsonify({"message": f"Fan set to {state} successfully.", "mode": fan_mode}), 200
    except Exception as e:
        app.logger.error(f"Error controlling fan: {str(e)}")
        return jsonify({"error": f"Error controlling fan: {str(e)}"}), 500

@app.route("/api/fan/status", methods=["GET"])
def get_fan_state():
    try:
        # Schedule the update coroutine in the thread's event loop
        update_task = asyncio.run_coroutine_threadsafe(fan_smart_plug.plug.update(), thread_loop)
        update_task.result()  # Wait for the update to complete

        # Schedule the get_state coroutine in the thread's event loop
        state_task = asyncio.run_coroutine_threadsafe(fan_smart_plug.get_state(), thread_loop)
        plug_state = state_task.result()

        # Append power data if available
        try:
            power_task = asyncio.run_coroutine_threadsafe(fan_smart_plug.get_power(), thread_loop)
            plug_state.update(power_task.result(timeout=5))
        except Exception:
            plug_state["power_w"] = None
            plug_state["today_kwh"] = None

        plug_state["mode"] = fan_mode
        return jsonify(plug_state), 200
    except Exception as e:
        return jsonify({"error": f"Error retrieving fan state: {str(e)}"}), 500

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
                "fan_power_w": row[7] if len(row) > 7 else None,
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


@app.route("/admin")
def admin():
    return render_template("admin.html")


@app.route("/api/fan/settings", methods=["GET"])
def get_fan_settings_route():
    return jsonify(get_fan_settings())


@app.route("/api/fan/settings", methods=["POST"])
def update_fan_settings_route():
    data = request.get_json()
    update_fan_settings(
        tvoc_min=data.get("tvoc_min", 0),
        tvoc_max=data.get("tvoc_max", 500),
        temp_min=data.get("temp_min", 0.0),
        temp_max=data.get("temp_max", 20.0),
        enabled=data.get("enabled", False),
    )
    return jsonify({"message": "Fan settings updated"})


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
    disk = psutil.disk_usage("/")

    status["uptime"] = uptime_str
    service_uptime = datetime.utcnow() - service_start_time
    status["service_uptime"] = str(timedelta(seconds=int(service_uptime.total_seconds())))
    status["cpu_usage"] = f"{cpu_percent:.1f}%"
    status["memory_used"] = f"{memory.used // (1024 ** 2)} MB"
    status["memory_total"] = f"{memory.total // (1024 ** 2)} MB"
    status["memory_percent"] = f"{memory.percent:.1f}%"
    status["disk_used"] = f"{disk.used // (1024 ** 3):.1f} GB"
    status["disk_total"] = f"{disk.total // (1024 ** 3):.1f} GB"
    status["disk_percent"] = f"{disk.percent:.1f}%"

    # DB file size
    db_path = config.get("DB_FILE", "data/sensor_data.db")
    try:
        db_bytes = os.path.getsize(db_path)
        if db_bytes >= 1024 ** 2:
            status["db_size"] = f"{db_bytes / (1024 ** 2):.1f} MB"
        else:
            status["db_size"] = f"{db_bytes / 1024:.1f} KB"
    except OSError:
        status["db_size"] = "Unknown"

    # Smart plug connectivity
    try:
        future = asyncio.run_coroutine_threadsafe(fan_smart_plug.plug.update(), thread_loop)
        future.result(timeout=5)
        status["smart_plug"] = "OK"
    except Exception:
        status["smart_plug"] = "UNAVAILABLE"

    return jsonify(status)


def _background_log():
    asyncio.set_event_loop(thread_loop)
    while True:
        try:
            log_data()
        except Exception as e:
            print(f"Error in background log loop: {e}")
        time.sleep(LOG_INTERVAL)


def main():
    Thread(target=_background_log, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()

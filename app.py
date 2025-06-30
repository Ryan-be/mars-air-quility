from flask import Flask, send_file, render_template, jsonify
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
#from sensors.sgp30 import read_sgp30

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
)


LOG_INTERVAL = int(config.get("LOG_INTERVAL", "10"))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up from mlss_monitor
DATA_FILE = os.path.join(BASE_DIR, "logs", "default.csv")

i2c = busio.I2C(board.SCL, board.SDA)
aht20 = AHTx0(i2c)
#sgp30 = Adafruit_SGP30(i2c)

def read_sensors():
    temperature, humidity = read_aht20()
 #   eco2, tvoc = read_sgp30()
    return datetime.now(), temperature, humidity, 0, 0

def log_data():
    ts, temp, hum, eco2, tvoc = read_sensors()
    with open(DATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([ts, temp, hum, eco2, tvoc])
    update_display(temp, hum, eco2, tvoc)

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
def api_data():
    if not os.path.exists(DATA_FILE):
        return jsonify([])
    df = pd.read_csv(DATA_FILE, names=["timestamp", "temperature", "humidity", "eco2", "tvoc"], parse_dates=["timestamp"])
    df = df[df["timestamp"] > datetime.now() - timedelta(hours=24)]
    return jsonify({
        "timestamp": df["timestamp"].astype(str).tolist(),
        "temperature": df["temperature"].tolist(),
        "humidity": df["humidity"].tolist(),
        "eco2": df["eco2"].tolist(),
        "tvoc": df["tvoc"].tolist()
    })

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
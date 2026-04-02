"""System health route."""

import asyncio
import os
import subprocess
from datetime import datetime, timedelta

import psutil
from flask import Blueprint, jsonify

from config import config
from mlss_monitor import state

system_bp = Blueprint("system", __name__)


@system_bp.route("/system_health")
def system_health():
    status = {}

    # Sensor status
    status["AHT20"] = "OK" if state.aht20 else "UNAVAILABLE"
    status["SGP30"] = "OK" if state.sgp30 else "UNAVAILABLE"
    status["PM_sensor"] = "OK" if state.pm_sensor else "UNAVAILABLE"
    status["MICS6814"] = "OK" if state.mics6814 else "UNAVAILABLE"

    # Pi uptime
    try:
        uptime_seconds = float(subprocess.check_output(["cat", "/proc/uptime"]).decode().split()[0])
        status["uptime"] = str(timedelta(seconds=int(uptime_seconds)))
    except Exception:
        status["uptime"] = "Unknown"

    # System stats
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    if state.service_start_time:
        service_uptime = datetime.utcnow() - state.service_start_time
        status["service_uptime"] = str(timedelta(seconds=int(service_uptime.total_seconds())))
    else:
        status["service_uptime"] = "Unknown"

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
        future = asyncio.run_coroutine_threadsafe(
            state.fan_smart_plug.plug.update(), state.thread_loop
        )
        future.result(timeout=5)
        status["smart_plug"] = "OK"
    except Exception:
        status["smart_plug"] = "UNAVAILABLE"

    return jsonify(status)

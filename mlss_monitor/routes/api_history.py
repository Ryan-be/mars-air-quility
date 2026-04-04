"""History API routes — sensor data, baselines, ML context, narratives."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from database.db_logger import _normalise_ts, compute_detection_method, get_inferences
from mlss_monitor import narrative_engine, state
from mlss_monitor.narrative_engine import _DAY_NAMES

api_history_bp = Blueprint("api_history", __name__)

_DB_TO_API = {
    "tvoc": "tvoc_ppb", "eco2": "eco2_ppm", "temperature": "temperature_c",
    "humidity": "humidity_pct", "pm1_0": "pm1_ug_m3", "pm2_5": "pm25_ug_m3",
    "pm10": "pm10_ug_m3", "gas_co": "co_ppb", "gas_no2": "no2_ppb", "gas_nh3": "nh3_ppb",
}
_ALL_CHANNELS = list(_DB_TO_API.values())
_BASELINE_CHANNELS = _ALL_CHANNELS
_ANOMALY_THRESHOLD_FACTOR = 0.25


def _query_sensor_data(db_file: str, start: str, end: str) -> list[dict]:
    start_db = start.rstrip("Z").replace("T", " ")
    end_db = end.rstrip("Z").replace("T", " ")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, tvoc, eco2, temperature, humidity,
                  pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3
           FROM sensor_data WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (start_db, end_db),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@api_history_bp.route("/api/history/sensor")
def sensor_history():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    from mlss_monitor.app import DB_FILE
    rows = _query_sensor_data(DB_FILE, start, end)
    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels: dict = {ch: [] for ch in _ALL_CHANNELS}
    for row in rows:
        for db_col, api_key in _DB_TO_API.items():
            channels[api_key].append(row.get(db_col))
    return jsonify({"timestamps": timestamps, "channels": channels})


@api_history_bp.route("/api/history/baselines")
def baselines():
    engine = state.detection_engine
    result: dict = {}
    if engine and engine._anomaly_detector:
        for ch in _BASELINE_CHANNELS:
            result[ch] = engine._anomaly_detector.baseline(ch)
    else:
        result = {ch: None for ch in _BASELINE_CHANNELS}
    result["anomaly_threshold_factor"] = _ANOMALY_THRESHOLD_FACTOR
    return jsonify(result)

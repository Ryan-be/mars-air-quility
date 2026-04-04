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


def _extract_attribution_source(inf: dict) -> str | None:
    ev = inf.get("evidence") or {}
    if isinstance(ev, str):
        try: ev = json.loads(ev)
        except Exception: return None
    return ev.get("attribution_source")


def _extract_attribution_confidence(inf: dict) -> float:
    ev = inf.get("evidence") or {}
    if isinstance(ev, str):
        try: ev = json.loads(ev)
        except Exception: return 0.0
    return float(ev.get("attribution_confidence") or 0.0)


def _pearson_r(xs: list, ys: list) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    sx = sum(p[0] for p in pairs); sy = sum(p[1] for p in pairs)
    sxy = sum(p[0]*p[1] for p in pairs)
    sx2 = sum(p[0]**2 for p in pairs); sy2 = sum(p[1]**2 for p in pairs)
    num = n*sxy - sx*sy
    den = math.sqrt((n*sx2 - sx**2) * (n*sy2 - sy**2))
    return num/den if den != 0 else None


_COMOVEMENT_PHRASES = {
    ("tvoc_ppb","eco2_ppm"): "TVOC and eCO2 rose together — consistent with indoor air pollutant build-up.",
    ("tvoc_ppb","pm25_ug_m3"): "TVOC and PM2.5 moved together — may indicate combustion or cooking.",
    ("co_ppb","no2_ppb"): "CO and NO2 resistance moved together — typical of a combustion event.",
    ("humidity_pct","temperature_c"): "Temperature and humidity changed together — check ventilation or HVAC.",
    ("pm1_ug_m3","pm25_ug_m3"): "PM1 and PM2.5 tracked closely — consistent with fine particle sources.",
}
_CHANNEL_LABELS = {
    "tvoc_ppb":"TVOC","eco2_ppm":"eCO2","temperature_c":"Temperature","humidity_pct":"Humidity",
    "pm1_ug_m3":"PM1","pm25_ug_m3":"PM2.5","pm10_ug_m3":"PM10",
    "co_ppb":"CO (resistance)","no2_ppb":"NO2 (resistance)","nh3_ppb":"NH3 (resistance)",
}


def _comovement_summary(sensor_rows: list[dict]) -> str:
    if len(sensor_rows) < 3:
        return ""
    ch_data: dict = {ch: [] for ch in _ALL_CHANNELS}
    for row in sensor_rows:
        for db_col, api_key in _DB_TO_API.items():
            ch_data[api_key].append(row.get(db_col))
    sentences = []
    for pair, phrase in _COMOVEMENT_PHRASES.items():
        a, b = pair
        r = _pearson_r(ch_data.get(a,[]), ch_data.get(b,[]))
        if r is not None and abs(r) > 0.7:
            sentences.append(phrase)
        if len(sentences) >= 3:
            break
    return " ".join(sentences)


@api_history_bp.route("/api/history/ml-context")
def ml_context():
    start = request.args.get("start",""); end = request.args.get("end","")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    from mlss_monitor.app import DB_FILE
    all_infs = get_inferences(limit=1000, include_dismissed=False)
    s_db = start.rstrip("Z").replace("T"," "); e_db = end.rstrip("Z").replace("T"," ")
    window = [i for i in all_infs if s_db <= i["created_at"].rstrip("Z").replace("T"," ") <= e_db]
    summary: dict = {}
    for inf in window:
        src = _extract_attribution_source(inf)
        if src: summary[src] = summary.get(src,0) + 1
    dominant = max(summary, key=summary.get) if summary else None
    sensor_rows = _query_sensor_data(DB_FILE, start, end)
    enriched = []
    for inf in window:
        ev = inf.get("evidence") or {}
        if isinstance(ev, str):
            try: ev = json.loads(ev)
            except Exception: ev = {}
        enriched.append({
            "id": inf["id"], "created_at": inf["created_at"],
            "title": inf.get("title",""), "event_type": inf.get("event_type",""),
            "severity": inf.get("severity",""),
            "attribution_source": ev.get("attribution_source"),
            "attribution_confidence": ev.get("attribution_confidence"),
            "runner_up_source": ev.get("runner_up_source"),
            "runner_up_confidence": ev.get("runner_up_confidence"),
            "detection_method": inf.get("detection_method","rule"),
        })
    return jsonify({
        "inferences": enriched, "attribution_summary": summary,
        "dominant_source": dominant,
        "dominant_source_sentence": narrative_engine.generate_period_summary(window,[],dominant) if window else "No events detected.",
        "comovement_summary": _comovement_summary(sensor_rows),
    })

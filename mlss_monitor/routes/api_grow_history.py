"""GET /api/grow/units/<id>/history — moisture series + watering events for charts."""
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, abort
from database.init_db import DB_FILE

api_grow_history_bp = Blueprint("api_grow_history", __name__)

_RANGE_TO_HOURS = {"24h": 24, "7d": 168, "30d": 720}


@api_grow_history_bp.route("/api/grow/units/<int:unit_id>/history", methods=["GET"])
def history(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in _RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = _RANGE_TO_HOURS[range_str]
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        moisture = conn.execute(
            "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
            "FROM grow_telemetry WHERE unit_id=? AND timestamp_utc >= ? "
            "ORDER BY timestamp_utc ASC", (unit_id, cutoff),
        ).fetchall()
        events = conn.execute(
            "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
            "FROM grow_watering_events WHERE unit_id=? AND timestamp_utc >= ? "
            "ORDER BY timestamp_utc ASC", (unit_id, cutoff),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "moisture": [
            {"ts": r["timestamp_utc"], "pct": r["soil_moisture_pct"],
             "raw": r["soil_moisture_raw"]}
            for r in moisture
        ],
        "watering_events": [
            {"ts": r["timestamp_utc"], "trigger": r["trigger"],
             "duration_s": r["duration_s"], "soil_pct_before": r["soil_pct_before"]}
            for r in events
        ],
    })

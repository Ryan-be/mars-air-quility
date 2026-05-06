"""GET /api/grow/units/<id>/history — moisture series + watering events for charts.

Supports ranges 24h / 7d / 30d / 90d / all. When the moisture-row count for a
range exceeds ``_DOWNSAMPLE_THRESHOLD`` (600), the rows are bucketed into at
most 600 buckets each carrying ``{ts, pct_min, pct_avg, pct_max, raw_avg}`` so
the frontend chart can render a min/max band rather than tens of thousands of
SVG points. Short ranges keep the original ``{ts, pct, raw}`` shape so the 24h
chart behaviour is unchanged.

The response always includes a ``phase_changes`` key (currently ``[]`` —
reserved for the Phase 3 phase-audit table the frontend annotation chart will
consume).
"""
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from database.init_db import DB_FILE

api_grow_history_bp = Blueprint("api_grow_history", __name__)

_RANGE_TO_HOURS = {"24h": 24, "7d": 168, "30d": 720, "90d": 2160, "all": None}
_DOWNSAMPLE_THRESHOLD = 600


def _maybe_downsample(rows, target=_DOWNSAMPLE_THRESHOLD):
    """Return rows as-is when len(rows) <= target, else bucket into target buckets.

    For the bucketed case each entry is ``{ts, pct_min, pct_avg, pct_max,
    raw_avg}`` where ``ts`` is the slice midpoint timestamp. Buckets whose
    ``soil_moisture_pct`` values are all NULL are skipped (the chart can
    interpolate the gap).
    """
    if len(rows) <= target:
        return [
            {
                "ts": r["timestamp_utc"],
                "pct": r["soil_moisture_pct"],
                "raw": r["soil_moisture_raw"],
            }
            for r in rows
        ]
    bucket_size = len(rows) / target
    buckets = []
    for i in range(target):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size) if i < target - 1 else len(rows)
        slice_rows = rows[start:end]
        if not slice_rows:
            continue
        pcts = [
            r["soil_moisture_pct"]
            for r in slice_rows
            if r["soil_moisture_pct"] is not None
        ]
        if not pcts:
            # All-NULL bucket — let the chart interpolate; skip it.
            continue
        raws = [r["soil_moisture_raw"] for r in slice_rows]
        buckets.append({
            "ts": slice_rows[len(slice_rows) // 2]["timestamp_utc"],
            "pct_min": min(pcts),
            "pct_avg": sum(pcts) / len(pcts),
            "pct_max": max(pcts),
            "raw_avg": (sum(raws) / len(raws)) if raws else None,
        })
    return buckets


@api_grow_history_bp.route("/api/grow/units/<int:unit_id>/history", methods=["GET"])
def history(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in _RANGE_TO_HOURS:
        return jsonify({"error": "invalid_range"}), 400
    hours = _RANGE_TO_HOURS[range_str]
    cutoff = (datetime.utcnow() - timedelta(hours=hours)) if hours is not None else None

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if cutoff is not None:
            moisture_rows = conn.execute(
                "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
                "FROM grow_telemetry WHERE unit_id=? AND timestamp_utc >= ? "
                "ORDER BY timestamp_utc ASC",
                (unit_id, cutoff),
            ).fetchall()
            event_rows = conn.execute(
                "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
                "FROM grow_watering_events WHERE unit_id=? AND timestamp_utc >= ? "
                "ORDER BY timestamp_utc ASC",
                (unit_id, cutoff),
            ).fetchall()
        else:
            moisture_rows = conn.execute(
                "SELECT timestamp_utc, soil_moisture_pct, soil_moisture_raw "
                "FROM grow_telemetry WHERE unit_id=? "
                "ORDER BY timestamp_utc ASC",
                (unit_id,),
            ).fetchall()
            event_rows = conn.execute(
                "SELECT timestamp_utc, trigger, duration_s, soil_pct_before "
                "FROM grow_watering_events WHERE unit_id=? "
                "ORDER BY timestamp_utc ASC",
                (unit_id,),
            ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "moisture": _maybe_downsample(moisture_rows),
        "watering_events": [
            {
                "ts": r["timestamp_utc"],
                "trigger": r["trigger"],
                "duration_s": r["duration_s"],
                "soil_pct_before": r["soil_pct_before"],
            }
            for r in event_rows
        ],
        # Reserved for the Phase 3 phase-audit table — frontend chart will
        # annotate phase transitions. Always present so the consumer doesn't
        # need to defensively check for the key.
        "phase_changes": [],
    })

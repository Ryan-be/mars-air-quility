"""API routes for environment inferences."""

from flask import Blueprint, jsonify, request

from database.db_logger import (
    dismiss_inference,
    get_inferences,
    update_inference_notes,
)
from mlss_monitor.inference_engine import CATEGORIES, event_category
from mlss_monitor.rbac import require_role

api_inferences_bp = Blueprint("api_inferences", __name__)


@api_inferences_bp.route("/api/inferences")
def list_inferences():
    limit = request.args.get("limit", 50, type=int)
    include_dismissed = request.args.get("dismissed", "0") == "1"
    category = request.args.get("category", "").strip()
    start = request.args.get("start", "").strip() or None
    end = request.args.get("end", "").strip() or None

    rows = get_inferences(limit=limit, include_dismissed=include_dismissed, start=start, end=end)

    for row in rows:
        row["category"] = event_category(row.get("event_type", ""))

    if category and category != "all":
        rows = [r for r in rows if r["category"] == category]

    return jsonify(rows)


@api_inferences_bp.route("/api/inferences/categories")
def list_categories():
    return jsonify(CATEGORIES)


@api_inferences_bp.route("/api/inferences/<int:inference_id>/notes", methods=["POST"])
@require_role("controller", "admin")
def save_notes(inference_id):
    data = request.get_json(force=True)
    notes = data.get("notes", "")
    update_inference_notes(inference_id, notes)
    return jsonify({"ok": True})


@api_inferences_bp.route("/api/inferences/<int:inference_id>/dismiss", methods=["POST"])
@require_role("controller", "admin")
def dismiss(inference_id):
    dismiss_inference(inference_id)
    return jsonify({"ok": True})


@api_inferences_bp.route("/api/inferences/<int:inference_id>/sparkline")
def sparkline(inference_id):
    """Return sensor data for ±15 min around a specific inference."""
    import json as _json
    import database.db_logger as _dbl_mod
    from datetime import datetime, timedelta, timezone
    from database.db_logger import get_inference_by_id
    from mlss_monitor.routes.api_history import _query_sensor_data, _DB_TO_API
    from database.db_logger import _normalise_ts

    # Map rule-based and statistical event_type values to the channels most relevant to them.
    _RULE_CHANNEL_MAP = {
        # Rule-based threshold events
        "high_tvoc":        ["tvoc_ppb"],
        "high_eco2":        ["eco2_ppm"],
        "high_temperature": ["temperature_c"],
        "low_temperature":  ["temperature_c"],
        "high_humidity":    ["humidity_pct"],
        "low_humidity":     ["humidity_pct"],
        "high_pm25":        ["pm25_ug_m3", "pm1_ug_m3", "pm10_ug_m3"],
        "high_pm10":        ["pm10_ug_m3", "pm25_ug_m3"],
        "high_co":          ["co_ppb"],
        "high_no2":         ["no2_ppb"],
        "high_nh3":         ["nh3_ppb"],
        # Statistical anomaly events (single-channel River detectors)
        "anomaly_tvoc":        ["tvoc_ppb"],
        "anomaly_eco2":        ["eco2_ppm"],
        "anomaly_temperature": ["temperature_c"],
        "anomaly_humidity":    ["humidity_pct"],
        "anomaly_pm25":        ["pm25_ug_m3", "pm1_ug_m3", "pm10_ug_m3"],
        "anomaly_pm1":         ["pm1_ug_m3", "pm25_ug_m3"],
        "anomaly_pm10":        ["pm10_ug_m3", "pm25_ug_m3"],
        "anomaly_co":          ["co_ppb"],
        "anomaly_no2":         ["no2_ppb"],
        "anomaly_nh3":         ["nh3_ppb"],
    }

    inf = get_inference_by_id(inference_id)
    if inf is None:
        return jsonify({"error": "not found"}), 404

    created_at = inf["created_at"]
    dt = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    window_start = (dt - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (dt + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _query_sensor_data(_dbl_mod.DB_FILE, window_start, window_end)

    # Also query hot_tier (1-second resolution, ~60 min retention) so that
    # recent inferences that haven't been downsampled to sensor_data yet still
    # have chart data.  hot_tier lacks pm1/pm10, so those are left as None.
    import sqlite3 as _sqlite3
    _hot_to_api = {
        "tvoc_ppb": "tvoc_ppb", "eco2_ppm": "eco2_ppm",
        "temperature_c": "temperature_c", "humidity_pct": "humidity_pct",
        "pm25_ug_m3": "pm25_ug_m3", "co_ppb": "co_ppb",
        "no2_ppb": "no2_ppb", "nh3_ppb": "nh3_ppb",
    }
    # _DB_TO_API maps sensor_data col names → api names; build reverse to get db col from api name
    _api_to_db = {v: k for k, v in _DB_TO_API.items()}
    try:
        # hot_tier stores timestamps as datetime.isoformat() — "YYYY-MM-DDTHH:MM:SS"
        # (T separator, no Z).  Do NOT replace "T" with " " here; sensor_data uses
        # space-formatted strings but hot_tier uses the ISO T format.  Using space-
        # formatted bounds against T-formatted values fails because 'T' > ' ' in
        # SQLite string ordering, making every hot_tier row appear past the end bound.
        start_db = window_start.rstrip("Z")  # keeps T: "YYYY-MM-DDTHH:MM:SS"
        end_db = window_end.rstrip("Z")
        _conn = _sqlite3.connect(_dbl_mod.DB_FILE)
        _conn.row_factory = _sqlite3.Row
        hot_raw = _conn.execute(
            """SELECT timestamp, tvoc_ppb, eco2_ppm, temperature_c, humidity_pct,
                      pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
               FROM hot_tier
               WHERE timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp ASC""",
            (start_db, end_db),
        ).fetchall()
        _conn.close()
        # Convert hot_tier rows to the same dict shape as sensor_data rows
        # (keyed by sensor_data column names so _DB_TO_API still works).
        hot_rows = []
        for hr in hot_raw:
            d = {}
            for hot_col, api_name in _hot_to_api.items():
                db_col = _api_to_db.get(api_name)
                if db_col:
                    d[db_col] = hr[hot_col]
            d["timestamp"] = hr["timestamp"]
            # pm1_0 and pm10 not in hot_tier
            d.setdefault("pm1_0", None)
            d.setdefault("pm10", None)
            hot_rows.append(d)
    except Exception:
        hot_rows = []

    # Merge: prefer sensor_data rows when both share the same timestamp.
    if hot_rows:
        cold_ts = {r["timestamp"] for r in rows}
        extra = [r for r in hot_rows if r["timestamp"] not in cold_ts]
        if extra:
            rows = sorted(rows + extra, key=lambda r: r["timestamp"])

    evidence = inf.get("evidence") or {}
    if isinstance(evidence, str):
        try: evidence = _json.loads(evidence)
        except Exception: evidence = {}

    event_type = inf.get("event_type", "")

    _FV_TO_API = {
        "tvoc_current":"tvoc_ppb","eco2_current":"eco2_ppm","temperature_current":"temperature_c",
        "humidity_current":"humidity_pct","pm1_current":"pm1_ug_m3","pm25_current":"pm25_ug_m3",
        "pm10_current":"pm10_ug_m3","co_current":"co_ppb","no2_current":"no2_ppb","nh3_current":"nh3_ppb",
    }
    snapshot = evidence.get("sensor_snapshot", [])
    triggering = []
    for entry in snapshot:
        api_key = _FV_TO_API.get(entry.get("channel",""), entry.get("channel",""))
        if api_key and api_key not in triggering:
            triggering.append(api_key)
    if not triggering:
        # For rule-based inferences, derive relevant channels from event_type.
        # For ML/statistical inferences with no snapshot, fall back to all channels.
        rule_channels = _RULE_CHANNEL_MAP.get(event_type, [])
        if rule_channels:
            triggering = rule_channels
        else:
            triggering = list(_DB_TO_API.values())

    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels = {api_key: [r.get(db_col) for r in rows] for db_col, api_key in _DB_TO_API.items() if api_key in triggering}

    return jsonify({"timestamps": timestamps, "channels": channels, "inference_at": created_at, "triggering_channels": triggering})

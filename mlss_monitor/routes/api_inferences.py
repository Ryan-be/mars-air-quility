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

    rows = get_inferences(limit=limit, include_dismissed=include_dismissed)

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

    inf = get_inference_by_id(inference_id)
    if inf is None:
        return jsonify({"error": "not found"}), 404

    created_at = inf["created_at"]
    dt = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    window_start = (dt - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (dt + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _query_sensor_data(_dbl_mod.DB_FILE, window_start, window_end)

    evidence = inf.get("evidence") or {}
    if isinstance(evidence, str):
        try: evidence = _json.loads(evidence)
        except Exception: evidence = {}

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
        triggering = list(_DB_TO_API.values())

    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels = {api_key: [r.get(db_col) for r in rows] for db_col, api_key in _DB_TO_API.items() if api_key in triggering}

    return jsonify({"timestamps": timestamps, "channels": channels, "inference_at": created_at, "triggering_channels": triggering})

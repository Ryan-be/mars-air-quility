"""API routes for environment inferences."""

import json as _json
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from database.db_logger import (
    dismiss_inference,
    get_distinct_attribution_sources,
    get_inferences,
    get_inference_by_id,
    update_inference_notes,
    get_inference_tags,
    add_inference_tag,
    get_sensor_data_range,
    get_hot_tier_range,
    _normalise_ts,
)
import database.db_logger as _dbl
from mlss_monitor.inference_engine import CATEGORIES, event_category
from mlss_monitor.rbac import require_role
from mlss_monitor.routes.api_history import _DB_TO_API

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
        row["tags"] = get_inference_tags(row["id"])

    if category and category != "all":
        if category in CATEGORIES:
            rows = [r for r in rows if r["category"] == category]
        else:
            rows = [r for r in rows
                    if (r.get("evidence") or {}).get("attribution_source") == category]

    return jsonify(rows)


_log = logging.getLogger(__name__)


@api_inferences_bp.route("/api/inferences/categories")
def list_categories():
    result = dict(CATEGORIES)
    try:
        sources = get_distinct_attribution_sources()
        _log.debug("get_distinct_attribution_sources returned: %s", sources)
        for src in sorted(sources):
            if src not in result:
                result[src] = src.replace("_", " ").title()
    except Exception as exc:
        _log.warning("list_categories failed to load fingerprint sources: %s", exc)
    _log.debug("list_categories returning %d categories: %s", len(result), list(result.keys()))
    return jsonify(result)


@api_inferences_bp.route("/api/inferences/<int:inference_id>", methods=["PATCH"])
@require_role("controller", "admin")
def patch_inference(inference_id):
    """Partial update of an inference.

    Accepts a JSON body with any combination of:
      * ``notes``      — string, replaces ``user_notes``.
      * ``dismissed``  — bool, ``True`` to mark the inference as dismissed.

    At least one of the two fields must be present; unknown fields are ignored.
    """
    data = request.get_json(force=True, silent=True) or {}
    has_notes = "notes" in data
    has_dismissed = "dismissed" in data
    if not has_notes and not has_dismissed:
        return jsonify({"error": "At least one of 'notes' or 'dismissed' is required."}), 400

    if has_notes:
        update_inference_notes(inference_id, data.get("notes", ""))
    if has_dismissed and bool(data.get("dismissed")):
        dismiss_inference(inference_id)
    return jsonify({"ok": True})


@api_inferences_bp.route("/api/inferences/<int:inference_id>/tags", methods=["GET", "POST"])
@require_role("controller", "admin")
def tags(inference_id):
    if request.method == "GET":
        tags_list = get_inference_tags(inference_id)
        return jsonify(tags_list)
    if request.method == "POST":
        data = request.get_json(force=True)
        tag = data.get("tag", "").strip()
        confidence = data.get("confidence", 1.0)
        if not tag:
            return jsonify({"ok": False, "error": "tag is required"}), 400

        # Validate against controlled vocabulary when engine is available.
        from mlss_monitor import state as _state  # pylint: disable=import-outside-toplevel
        _engine = _state.detection_engine
        allowed = (
            _engine._attribution_engine.valid_tags
            if _engine and _engine._attribution_engine
            else None
        )
        if allowed is not None and tag not in allowed:
            return jsonify({
                "error": "invalid_tag",
                "valid_tags": sorted(allowed),
            }), 400

        add_inference_tag(inference_id, tag, confidence, allowed_tags=allowed)
        return jsonify({"ok": True})
    return jsonify({"error": "method not allowed"}), 405


@api_inferences_bp.route("/api/inferences/<int:inference_id>/sparkline")
def sparkline(inference_id):
    """Return sensor data covering the event window for sparkline charts."""

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
        # Composite multivariate anomaly events
        "anomaly_combustion_signature":   ["tvoc_ppb", "co_ppb", "no2_ppb", "pm25_ug_m3"],
        "anomaly_particle_distribution":  ["pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3"],
        "anomaly_ventilation_quality":    ["eco2_ppm", "tvoc_ppb", "nh3_ppb"],
        "anomaly_gas_relationship":       ["co_ppb", "no2_ppb", "nh3_ppb"],
        "anomaly_thermal_moisture":       ["temperature_c", "humidity_pct"],
        # User-tagged range events – show full gas profile
        "annotation_context_user_range":  ["tvoc_ppb", "eco2_ppm", "pm25_ug_m3", "nh3_ppb", "co_ppb", "no2_ppb"],
    }

    inf = get_inference_by_id(inference_id)
    if inf is None:
        return jsonify({"error": "not found"}), 404

    created_at = inf["created_at"]
    dt = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    window_start = (dt - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (dt + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = get_sensor_data_range(window_start, window_end)

    # Also query hot_tier (1-second resolution, ~60 min retention) so that
    # recent inferences that haven't been downsampled to sensor_data yet still
    # have chart data.  hot_tier lacks pm1/pm10, so those are left as None.
    # _DB_TO_API maps sensor_data col names → api names; build reverse to get db col from api name
    _api_to_db = {v: k for k, v in _DB_TO_API.items()}
    try:
        hot_raw = get_hot_tier_range(window_start, window_end)
        # Convert hot_tier rows to the same dict shape as sensor_data rows
        # (keyed by sensor_data column names so _DB_TO_API still works).
        hot_rows = []
        for hr in hot_raw:
            d = {}
            for hot_col in ("tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
                            "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb"):
                db_col = _api_to_db.get(hot_col)
                if db_col:
                    d[db_col] = hr.get(hot_col)
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
        try:
            evidence = _json.loads(evidence)
        except Exception:
            evidence = {}

    # For user-tagged range events, use the actual tagged range as the window.
    range_start = evidence.get("range_start")
    range_end = evidence.get("range_end")
    if range_start and range_end:
        try:
            dt_start = datetime.fromisoformat(range_start.rstrip("Z")).replace(tzinfo=timezone.utc)
            dt_end = datetime.fromisoformat(range_end.rstrip("Z")).replace(tzinfo=timezone.utc)
            dt = dt_start + (dt_end - dt_start) / 2  # centre for inference_at marker
            window_start = (dt_start - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            window_end = (dt_end + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = get_sensor_data_range(window_start, window_end)
            # Re-query hot_tier with the new window too
            try:
                hot_raw2 = get_hot_tier_range(window_start, window_end)
                hot_rows_range = []
                for hr in hot_raw2:
                    d = {}
                    for hot_col in ("tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
                                    "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb"):
                        db_col = _api_to_db.get(hot_col)
                        if db_col:
                            d[db_col] = hr.get(hot_col)
                    d["timestamp"] = hr["timestamp"]
                    d.setdefault("pm1_0", None)
                    d.setdefault("pm10", None)
                    hot_rows_range.append(d)
                if hot_rows_range:
                    cold_ts = {r["timestamp"] for r in rows}
                    extra = [r for r in hot_rows_range if r["timestamp"] not in cold_ts]
                    if extra:
                        rows = sorted(rows + extra, key=lambda r: r["timestamp"])
            except Exception:
                pass
        except Exception:
            pass  # fall back to created_at-based window

    event_type = inf.get("event_type", "")

    # Check whether the inference has user-applied tags.
    tags = get_inference_tags(inference_id)
    has_tags = len(tags) > 0

    _FULL_GAS_PROFILE = ["tvoc_ppb", "eco2_ppm", "pm25_ug_m3", "nh3_ppb", "co_ppb", "no2_ppb"]

    _FV_TO_API = {
        "tvoc_current": "tvoc_ppb", "eco2_current": "eco2_ppm", "temperature_current": "temperature_c",
        "humidity_current": "humidity_pct", "pm1_current": "pm1_ug_m3", "pm25_current": "pm25_ug_m3",
        "pm10_current": "pm10_ug_m3", "co_current": "co_ppb", "no2_current": "no2_ppb", "nh3_current": "nh3_ppb",
    }

    # Determine channel selection priority:
    # 1. User-tagged events → full gas profile (most relevant for attribution).
    # 2. Attribution / fingerprint events → full gas profile.
    # 3. Rule-based or ML anomaly events → _RULE_CHANNEL_MAP.
    # 4. Snapshot fallback → channels listed in evidence.sensor_snapshot.
    # 5. Last resort → all channels.
    _ATTRIBUTION_TYPES = {"attribution", "fingerprint_match", "ml_learned"}

    is_ml_anomaly = event_type.startswith("anomaly_")

    if has_tags or event_type in _ATTRIBUTION_TYPES:
        triggering = _FULL_GAS_PROFILE
    else:
        rule_channels = _RULE_CHANNEL_MAP.get(event_type, [])
        if rule_channels:
            triggering = rule_channels
        else:
            snapshot = evidence.get("sensor_snapshot", [])
            triggering = []
            for entry in snapshot:
                api_key = _FV_TO_API.get(entry.get("channel", ""), entry.get("channel", ""))
                if api_key and api_key not in triggering:
                    triggering.append(api_key)
            if not triggering:
                triggering = list(_DB_TO_API.values())

    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels = {
        api_key: [r.get(db_col) for r in rows]
        for db_col, api_key in _DB_TO_API.items() if api_key in triggering
    }

    return jsonify({
        "timestamps": timestamps, "channels": channels,
        "inference_at": created_at, "triggering_channels": triggering,
        "is_ml_anomaly": is_ml_anomaly,
        "range_start": range_start,
        "range_end": range_end,
    })

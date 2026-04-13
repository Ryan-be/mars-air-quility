"""History API routes — sensor data, baselines, ML context, narratives."""
from __future__ import annotations

import dataclasses
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import database.db_logger as _dbl
from database.db_logger import _normalise_ts, get_inferences, save_inference, add_inference_tag
from mlss_monitor.rbac import require_role
from mlss_monitor import narrative_engine, state
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.detection_engine import DetectionEngine
from mlss_monitor.feature_extractor import FeatureExtractor
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.narrative_engine import _DAY_NAMES

api_history_bp = Blueprint("api_history", __name__)

_narratives_cache: dict = {}
_narratives_cache_ttl: int = 60  # seconds


def _parse_utc_flexible(ts: str) -> datetime:
    """Parse a UTC timestamp in any common format to a timezone-aware datetime."""
    if not ts:
        raise ValueError("Empty timestamp")
    # Normalise: replace space with T, handle Z and +00:00
    ts = ts.strip().replace(" ", "T")
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    # fromisoformat handles +00:00 in Python 3.11+
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Strip subseconds if needed
        ts = ts[:19] + ts[19:].lstrip("0123456789.")
        if not ts.endswith("+00:00"):
            ts += "+00:00"
        return datetime.fromisoformat(ts)


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


def _query_hot_tier(db_file: str, start: str, end: str) -> list[dict]:
    start_db = start.rstrip("Z")
    end_db = end.rstrip("Z")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, tvoc_ppb, eco2_ppm, temperature_c, humidity_pct,
                  pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
           FROM hot_tier WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (start_db, end_db),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _normalise_iso_ts(ts: str) -> str:
    if not ts:
        return ts
    ts = ts.strip().replace(" ", "T")
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ts


def _get_field(row: dict, *keys) -> float | None:
    """Return the first non-None value from row for any of the given keys.

    Handles both sensor_data column names (e.g. 'tvoc') and hot_tier / API
    column names (e.g. 'tvoc_ppb') so that _build_range_readings correctly
    populates values from merged rows regardless of their origin.
    """
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _rows_to_readings(rows: list[dict], source: str) -> list[NormalisedReading]:
    readings: list[NormalisedReading] = []
    for row in rows:
        ts = row.get("timestamp")
        try:
            ts = ts.strip().replace(" ", "T")
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            timestamp = datetime.fromisoformat(ts).astimezone(timezone.utc)
        except Exception:
            continue
        readings.append(NormalisedReading(
            timestamp=timestamp,
            source=source,
            # Accept both sensor_data col names and hot_tier / API col names.
            tvoc_ppb=_get_field(row, "tvoc", "tvoc_ppb"),
            eco2_ppm=_get_field(row, "eco2", "eco2_ppm"),
            temperature_c=_get_field(row, "temperature", "temperature_c"),
            humidity_pct=_get_field(row, "humidity", "humidity_pct"),
            pm1_ug_m3=_get_field(row, "pm1_0", "pm1_ug_m3"),
            pm25_ug_m3=_get_field(row, "pm2_5", "pm25_ug_m3"),
            pm10_ug_m3=_get_field(row, "pm10", "pm10_ug_m3"),
            co_ppb=_get_field(row, "gas_co", "co_ppb"),
            no2_ppb=_get_field(row, "gas_no2", "no2_ppb"),
            nh3_ppb=_get_field(row, "gas_nh3", "nh3_ppb"),
        ))
    return readings


def _build_range_readings(start: str, end: str) -> list[NormalisedReading]:
    sensor_rows = _query_sensor_data(_dbl.DB_FILE, start, end)
    hot_rows = _query_hot_tier(_dbl.DB_FILE, start, end)
    merged: dict[str, dict] = {}
    for row in sensor_rows:
        key = row.get("timestamp", "").strip().replace(" ", "T")
        merged[key] = row
    for row in hot_rows:
        key = row.get("timestamp", "").strip()
        if key not in merged:
            merged[key] = row
    sorted_rows = sorted(merged.values(), key=lambda r: r.get("timestamp", ""))
    readings = _rows_to_readings(sorted_rows, source="history")
    return readings


_SENSOR_FIELDS = [
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb",
    "no2_ppb", "nh3_ppb",
]

# DB column name → NormalisedReading / _ALL_CHANNELS field name
_DB_COL_TO_FIELD = {
    "tvoc": "tvoc_ppb", "eco2": "eco2_ppm", "temperature": "temperature_c",
    "humidity": "humidity_pct", "pm1_0": "pm1_ug_m3", "pm2_5": "pm25_ug_m3",
    "pm10": "pm10_ug_m3", "gas_co": "co_ppb", "gas_no2": "no2_ppb", "gas_nh3": "nh3_ppb",
}


def _compute_historical_baselines(start: str) -> dict[str, float | None]:
    """Compute pre-event baseline for each sensor channel.

    Queries the 60-minute window ending at `start` from `sensor_data`,
    returns the median value per channel. Returns None for channels with
    no data in that window.
    """
    try:
        start_dt = _parse_utc_flexible(start)
        window_end = start_dt
        window_start = start_dt - timedelta(hours=1)
    except (ValueError, TypeError):
        return {f: None for f in _SENSOR_FIELDS}

    baselines: dict[str, float | None] = {}
    try:
        db_path = _dbl.DB_FILE
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            """
            SELECT tvoc, eco2, temperature, humidity,
                   pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3
            FROM sensor_data
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp
            """,
            (window_start.strftime("%Y-%m-%d %H:%M:%S"),
             window_end.strftime("%Y-%m-%d %H:%M:%S")),
        )
        rows = cur.fetchall()
        con.close()
    except Exception:
        return {f: None for f in _SENSOR_FIELDS}

    if not rows:
        return {f: None for f in _SENSOR_FIELDS}

    for db_col, field in _DB_COL_TO_FIELD.items():
        vals = [r[db_col] for r in rows if r[db_col] is not None]
        if vals:
            vals.sort()
            mid = len(vals) // 2
            baselines[field] = (vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)
        else:
            baselines[field] = None

    return baselines


def _build_feature_vector(start: str, end: str) -> dict[str, object]:
    readings = _build_range_readings(start, end)
    baselines = _compute_historical_baselines(start)
    fv = FeatureExtractor().extract(readings, baselines)
    fv_dict = dataclasses.asdict(fv)
    # Convert datetime to ISO string for JSON serialization
    if fv_dict.get("timestamp"):
        fv_dict["timestamp"] = fv_dict["timestamp"].isoformat()
    return {
        "feature_vector": fv_dict,
        "readings": [
            {
                "timestamp": r.timestamp.isoformat(),
                "tvoc_ppb": r.tvoc_ppb,
                "eco2_ppm": r.eco2_ppm,
                "temperature_c": r.temperature_c,
                "humidity_pct": r.humidity_pct,
                "pm1_ug_m3": r.pm1_ug_m3,
                "pm25_ug_m3": r.pm25_ug_m3,
                "pm10_ug_m3": r.pm10_ug_m3,
                "co_ppb": r.co_ppb,
                "no2_ppb": r.no2_ppb,
                "nh3_ppb": r.nh3_ppb,
            }
            for r in readings
        ],
    }


def _make_range_evaluator() -> DetectionEngine | None:
    engine = state.detection_engine
    if engine is None:
        return None
    try:
        return DetectionEngine(
            rules_path=engine._rules_path,
            anomaly_config_path=engine._anomaly_detector._config_path,
            model_dir=engine._anomaly_detector._model_dir,
            fingerprints_path=getattr(engine, '_fingerprints_path', None),
            dry_run=True,
        )
    except Exception:
        return None


def _choose_best_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get('confidence', 0.0)))


@api_history_bp.route("/api/history/range-analysis")
def range_analysis():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    evaluator = _make_range_evaluator()
    if evaluator is None:
        return jsonify({"error": "No detection engine available"}), 500
    try:
        fv_result = _build_feature_vector(start, end)
        fv_data = fv_result["feature_vector"]
        candidates = evaluator.evaluate(FeatureVector(**fv_data)) if fv_data else []
        return jsonify({
            "start": start,
            "end": end,
            "feature_vector": fv_result["feature_vector"],
            "readings": fv_result["readings"],
            "candidates": candidates,
            "best_candidate": _choose_best_candidate(candidates),
        })
    except Exception as exc:
        return jsonify({"error": f"Error analysing range: {str(exc)}"}), 500


@api_history_bp.route("/api/history/range-tag", methods=["POST"])
@require_role("controller", "admin")
def tag_range():
    data = request.get_json(force=True)
    start = data.get("start", "")
    end = data.get("end", "")
    tag = (data.get("tag") or "").strip()
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    fv_result = _build_feature_vector(start, end)
    evaluator = _make_range_evaluator()
    candidates = []
    best_candidate = None
    if evaluator is not None:
        try:
            candidates = evaluator.evaluate(FeatureVector(**fv_result["feature_vector"]))
            best_candidate = _choose_best_candidate(candidates)
        except Exception:
            candidates = []

    if best_candidate is not None:
        event_type = best_candidate["event_type"]
        severity = best_candidate["severity"]
        title = best_candidate["title"]
        description = best_candidate["description"]
        action = best_candidate["action"]
        confidence = float(best_candidate.get("confidence", 0.5) or 0.5)
        evidence = best_candidate.get("evidence", {})
    else:
        event_type = "annotation_context_user_range"
        severity = "warning"
        title = "User-tagged event"
        description = (
            "A custom event was tagged from the selected history range. "
            "This inference was generated from the selected readings."
        )
        action = "Review the selected range and add a tag to help the model learn."
        confidence = 0.5
        evidence = {
            "fv_timestamp": _normalise_iso_ts(start),
            "feature_vector": fv_result["feature_vector"],
            "range_start": start,
            "range_end": end,
        }

    evidence.setdefault("range_start", start)
    evidence.setdefault("range_end", end)
    evidence.setdefault("feature_vector", fv_result["feature_vector"])
    evidence.setdefault("readings", fv_result["readings"])

    inference_id = save_inference(
        event_type=event_type,
        severity=severity,
        title=title,
        description=description,
        action=action,
        evidence=evidence,
        confidence=confidence,
    )
    if tag:
        from mlss_monitor import state as _state  # pylint: disable=import-outside-toplevel,reimported
        _engine = _state.detection_engine
        _allowed = (
            _engine._attribution_engine.valid_tags
            if _engine and _engine._attribution_engine
            else None
        )
        if _allowed is not None and tag not in _allowed:
            return jsonify({
                "error": "invalid_tag",
                "valid_tags": sorted(_allowed),
            }), 400
        add_inference_tag(inference_id, tag, 1.0, allowed_tags=_allowed)

    return jsonify({
        "id": inference_id,
        "tag": tag,
        "candidate": best_candidate,
    })


@api_history_bp.route("/api/history/sensor")
def sensor_history():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    rows = _query_sensor_data(_dbl.DB_FILE, start, end)
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
        try:
            ev = json.loads(ev)
        except Exception:
            return None
    if not isinstance(ev, dict):
        return None
    # ML/anomaly inferences store the source as "attribution_source";
    # rule-fired inferences store it as "attribution".  Accept both.
    return ev.get("attribution_source") or ev.get("attribution") or None


def _extract_attribution_confidence(inf: dict) -> float:
    ev = inf.get("evidence") or {}
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except Exception:
            return 0.0
    if not isinstance(ev, dict):
        return 0.0
    return float(ev.get("attribution_confidence") or 0.0)


def _pearson_r(xs: list, ys: list) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    sx2 = sum(p[0] ** 2 for p in pairs)
    sy2 = sum(p[1] ** 2 for p in pairs)
    num = n * sxy - sx * sy
    den = math.sqrt((n * sx2 - sx ** 2) * (n * sy2 - sy ** 2))
    return num / den if den != 0 else None


_CHANNEL_LABELS = {
    "tvoc_ppb": "TVOC", "eco2_ppm": "CO₂ (estimated)", "temperature_c": "Temperature", "humidity_pct": "Humidity",
    "pm1_ug_m3": "PM1", "pm25_ug_m3": "PM2.5", "pm10_ug_m3": "PM10",
    "co_ppb": "CO (resistance)", "no2_ppb": "NO2 (resistance)", "nh3_ppb": "NH3 (resistance)",
}

# Channels whose raw DB values are resistance (kΩ) — higher resistance = lower concentration.
# We invert these before computing Pearson so that a simultaneous concentration spike
# (resistance drops together) produces a positive correlation with other rising channels.
_INVERTED_CHANNELS = {"co_ppb", "no2_ppb", "nh3_ppb"}

# All meaningful pairs to check for co-movement.
# Tuple: (channel_a, channel_b, description)
_COMOVEMENT_PAIRS: list[tuple[str, str, str]] = [
    ("tvoc_ppb",   "eco2_ppm",    "TVOC and eCO₂ rose together — shared indoor source (occupancy or VOC emission)."),
    ("tvoc_ppb",   "pm25_ug_m3",  "TVOC and PM2.5 moved together — may indicate combustion or cooking."),
    ("tvoc_ppb",   "co_ppb",      "TVOC and CO concentration rose together — aerosol, combustion or solvent."),
    ("tvoc_ppb",   "nh3_ppb",     "TVOC and NH3 rose together — aerosol spray or cleaning products."),
    ("tvoc_ppb",   "no2_ppb",     "TVOC and NO2 concentration rose together — possible combustion signature."),
    ("eco2_ppm",   "co_ppb",      "eCO₂ and CO rose together — possible combustion or occupancy build-up."),
    ("eco2_ppm",   "nh3_ppb",     "eCO₂ and NH3 concentration rose together — occupancy or biological off-gassing."),
    ("co_ppb",     "no2_ppb",     "CO and NO2 concentration rose together — typical combustion event."),
    ("co_ppb",     "nh3_ppb",     "CO and NH3 concentration rose together — aerosol spray or cleaning products."),
    ("no2_ppb",    "nh3_ppb",     "NO2 and NH3 concentration rose together — mixed gas source detected."),
    ("pm1_ug_m3",  "pm25_ug_m3",  "PM1 and PM2.5 tracked closely — fine particle source active."),
    ("pm25_ug_m3", "pm10_ug_m3",  "PM2.5 and PM10 moved together — particle event spanning multiple size fractions."),
    ("humidity_pct", "temperature_c", "Temperature and humidity changed together — ventilation or HVAC effect."),
]


def _invert_if_needed(values: list, channel: str) -> list:
    """Invert resistance channels so concentration and other channels correlate positively."""
    if channel not in _INVERTED_CHANNELS:
        return values
    return [-v if v is not None else None for v in values]


def _comovement_summary(readings: list[NormalisedReading]) -> str:
    """Return a plain-English description of which channel pairs moved together.

    Accepts a list of NormalisedReading objects (from _build_range_readings so
    that both sensor_data and hot_tier rows are included).

    Uses Pearson R on concentration-normalised values (resistance channels are
    inverted so a simultaneous spike reads as positive correlation).
    Threshold 0.65 — lower than the old 0.7 to catch short rapid spikes where
    a few noisy points at the tail can reduce R slightly below 0.7.
    """
    if len(readings) < 3:
        return ""
    ch_data: dict = {ch: [] for ch in _ALL_CHANNELS}
    for r in readings:
        for ch in _ALL_CHANNELS:
            ch_data[ch].append(getattr(r, ch, None))
    sentences = []
    for a, b, phrase in _COMOVEMENT_PAIRS:
        va = _invert_if_needed(ch_data.get(a, []), a)
        vb = _invert_if_needed(ch_data.get(b, []), b)
        r = _pearson_r(va, vb)
        if r is not None and r > 0.65:
            sentences.append(phrase)
        if len(sentences) >= 4:
            break
    return " ".join(sentences)


@api_history_bp.route("/api/history/ml-context")
def ml_context():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    window = get_inferences(limit=1000, include_dismissed=False, start=start, end=end)
    summary: dict = {}
    for inf in window:
        src = _extract_attribution_source(inf)
        if src:
            summary[src] = summary.get(src, 0) + 1
    dominant = max(summary, key=summary.get) if summary else None
    # Use _build_range_readings so that hot_tier (1-second resolution) rows are
    # included — short selections (< 3 min) would otherwise return too few rows
    # from sensor_data (1-min averages) to compute meaningful co-movement stats.
    readings = _build_range_readings(start, end)
    enriched = []
    for inf in window:
        ev = inf.get("evidence") or {}
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except Exception:
                ev = {}
        enriched.append({
            "id": inf["id"], "created_at": inf["created_at"],
            "title": inf.get("title", ""), "event_type": inf.get("event_type", ""),
            "severity": inf.get("severity", ""),
            "attribution_source": ev.get("attribution_source") or ev.get("attribution") or None,
            "attribution_confidence": ev.get("attribution_confidence"),
            "runner_up_source": ev.get("runner_up_source") or ev.get("runner_up") or None,
            "runner_up_confidence": ev.get("runner_up_confidence"),
            "detection_method": inf.get("detection_method", "rule"),
        })
    return jsonify({
        "inferences": enriched, "attribution_summary": summary,
        "dominant_source": dominant,
        "dominant_source_sentence": (
            narrative_engine.generate_period_summary(window, [], dominant) if window else "No events detected."
        ),
        "comovement_summary": _comovement_summary(readings),
    })


_KNOWN_SOURCES = [
    ("biological_offgas", "Biological Off-gassing", "🧬"),
    ("chemical_offgassing", "Chemical Off-gassing", "🧪"),
    ("cooking", "Cooking", "🍳"),
    ("combustion", "Combustion", "🔥"),
    ("external_pollution", "External Pollution", "🌫️"),
    ("cleaning_products", "Cleaning Products", "🧹"),
    ("human_activity", "Human Activity", "👤"),
    ("vehicle_exhaust", "Vehicle Exhaust", "🚗"),
    ("mould_voc", "Mould / Fungal VOC", "🍄"),
    ("personal_care", "Personal Care Products", "🧴"),
]
_ML_EVENT_TYPES_SET = {
    "anomaly_combustion_signature", "anomaly_particle_distribution",
    "anomaly_ventilation_quality", "anomaly_gas_relationship", "anomaly_thermal_moisture",
}
_MODEL_LABELS = {
    "anomaly_combustion_signature": "Combustion Signature",
    "anomaly_particle_distribution": "Particle Distribution",
    "anomaly_ventilation_quality": "Ventilation Quality",
    "anomaly_gas_relationship": "Gas Sensor Relationship",
    "anomaly_thermal_moisture": "Thermal-Moisture Stress",
}
_MODEL_DESCRIPTIONS = {
    "anomaly_combustion_signature": (
        "Watches for co-rises in CO resistance, TVOC, and particles — a pattern typical of nearby combustion."
    ),
    "anomaly_particle_distribution": (
        "Monitors the ratio relationship between PM1, PM2.5 and PM10 for unusual size distributions."
    ),
    "anomaly_ventilation_quality": (
        "Tracks CO₂ (estimated), TVOC and NH3 building up together — a sign of poor ventilation."
    ),
    "anomaly_gas_relationship": "Monitors the correlation structure of CO, NO2 and NH3 from the MICS6814 sensor.",
    "anomaly_thermal_moisture": "Scores temperature, humidity and VPD together to detect comfort-zone stress events.",
}
_FV_TO_API = {
    "tvoc_current": "tvoc_ppb", "eco2_current": "eco2_ppm", "temperature_current": "temperature_c",
    "humidity_current": "humidity_pct", "pm1_current": "pm1_ug_m3", "pm25_current": "pm25_ug_m3",
    "pm10_current": "pm10_ug_m3", "co_current": "co_ppb", "no2_current": "no2_ppb", "nh3_current": "nh3_ppb",
}


def _get_baselines_7d_ago(db_file: str, window_start: str) -> dict:
    try:
        start_dt = datetime.fromisoformat(window_start.rstrip("Z")).replace(tzinfo=timezone.utc)
        ago_end = start_dt - timedelta(days=7)
        ago_start = ago_end - timedelta(hours=24)
        ago_start_db = ago_start.strftime("%Y-%m-%d %H:%M:%S")
        ago_end_db = ago_end.strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(db_file)
        try:
            row = conn.execute(
                """SELECT AVG(tvoc), AVG(eco2), AVG(temperature), AVG(humidity),
                          AVG(pm1_0), AVG(pm2_5), AVG(pm10),
                          AVG(gas_co), AVG(gas_no2), AVG(gas_nh3)
                   FROM sensor_data WHERE timestamp >= ? AND timestamp < ?""",
                (ago_start_db, ago_end_db),
            ).fetchone()
        finally:
            conn.close()
        if row is None or all(v is None for v in row):
            return {}
        db_cols = ["tvoc", "eco2", "temperature", "humidity", "pm1_0", "pm2_5", "pm10",
                   "gas_co", "gas_no2", "gas_nh3"]
        return {_DB_TO_API[col]: row[i] for i, col in enumerate(db_cols)}
    except Exception:
        return {}


def _round_to_minute(ts: str) -> str:
    """Round an ISO timestamp string down to the nearest minute for cache key use."""
    try:
        dt = _parse_utc_flexible(ts)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return ts[:16]


@api_history_bp.route("/api/history/narratives")
def narratives():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    cache_key = _round_to_minute(start) + "|" + _round_to_minute(end)
    cached = _narratives_cache.get(cache_key)
    if cached is not None:
        payload, stored_at = cached
        if time.monotonic() - stored_at < _narratives_cache_ttl:
            return jsonify(payload)

    from mlss_monitor.inference_evidence import _CHANNEL_META
    from mlss_monitor.narrative_engine import _parse_utc

    window = get_inferences(limit=2000, include_dismissed=False, start=start, end=end,
                            parse_evidence=False)

    engine = state.detection_engine
    baselines_now = {}
    if engine and engine._anomaly_detector:
        baselines_now = {ch: engine._anomaly_detector.baseline(ch) for ch in _BASELINE_CHANNELS}
    baselines_7d = _get_baselines_7d_ago(_dbl.DB_FILE, start)

    ch_meta_api = {
        _FV_TO_API[k]: {"label": v["label"], "unit": v["unit"]}
        for k, v in _CHANNEL_META.items() if k in _FV_TO_API
    }
    trend_indicators = narrative_engine.compute_trend_indicators(baselines_now, baselines_7d, ch_meta_api)
    drift_flags = narrative_engine.detect_drift_flags(baselines_now, baselines_7d)

    summary: dict = {}
    for inf in window:
        src = _extract_attribution_source(inf)
        if src:
            summary[src] = summary.get(src, 0) + 1
    dominant = max(summary, key=summary.get) if summary else None

    period_summary = narrative_engine.generate_period_summary(window, trend_indicators, dominant)
    clean = narrative_engine.compute_longest_clean_period(window, start, end)
    heatmap = narrative_engine.compute_pattern_heatmap(window)

    if heatmap:
        top_key = max(heatmap, key=heatmap.get)
        day_i, hour_i = (int(x) for x in top_key.split("_"))
        pattern_sentence = f"Events most frequently occur on {_DAY_NAMES[day_i]}s around {hour_i:02d}:00."
    else:
        pattern_sentence = "No recurring time pattern detected in this period."

    fp_narratives = []
    for src_id, label, emoji in _KNOWN_SOURCES:
        src_events = [i for i in window if _extract_attribution_source(i) == src_id]
        avg_conf = (
            sum(_extract_attribution_confidence(i) for i in src_events) / len(src_events)
            if src_events else 0.0
        )
        typical_hours = [_parse_utc(i["created_at"]).hour for i in src_events if i.get("created_at")]
        fp_narratives.append({
            "source_id": src_id, "label": label, "emoji": emoji,
            "event_count": len(src_events), "avg_confidence": round(avg_conf, 2),
            "typical_hours": typical_hours,
            "narrative": narrative_engine.generate_fingerprint_narrative(
                src_id, label, src_events, avg_conf, typical_hours
            ),
        })

    model_narratives = []
    for et in sorted(_ML_EVENT_TYPES_SET):
        evts = [i for i in window if i.get("event_type") == et]
        if evts:
            mid = et.replace("anomaly_", "")
            lbl = _MODEL_LABELS.get(et, mid)
            desc = _MODEL_DESCRIPTIONS.get(et, "")
            model_narratives.append({
                "model_id": mid, "label": lbl, "event_count": len(evts), "description": desc,
                "narrative": narrative_engine.generate_anomaly_model_narrative(mid, lbl, len(evts), desc),
            })

    _SOURCE_FRIENDLY = {src_id: f"{emoji} {label}" for src_id, label, emoji in _KNOWN_SOURCES}
    dom_label = _SOURCE_FRIENDLY.get(dominant, dominant.replace("_", " ").capitalize()) if dominant else None
    dom_sentence = (
        f"{dom_label} accounts for {summary[dominant]} of {len(window)} events."
        if dominant and window
        else "No events were attributed to a source in this period."
    )

    method_breakdown: dict = {}
    for inf in window:
        m = inf.get("detection_method", "rule") or "rule"
        method_breakdown[m] = method_breakdown.get(m, 0) + 1

    window_infs_slim = []
    for inf in sorted(window, key=lambda x: x.get("created_at", ""), reverse=True)[:100]:
        window_infs_slim.append({
            "id": inf.get("id"),
            "event_type": inf.get("event_type", ""),
            "title": inf.get("title", ""),
            "severity": inf.get("severity", ""),
            "confidence": inf.get("confidence"),
            "created_at": inf.get("created_at", ""),
            "detection_method": inf.get("detection_method", "rule") or "rule",
        })

    result_payload = {
        "period_summary": period_summary, "trend_indicators": trend_indicators,
        "longest_clean_hours": clean["hours"], "longest_clean_start": clean["start"],
        "longest_clean_end": clean["end"],
        "attribution_breakdown": summary, "dominant_source_sentence": dom_sentence,
        "fingerprint_narratives": fp_narratives, "anomaly_model_narratives": model_narratives,
        "pattern_heatmap": heatmap, "pattern_sentence": pattern_sentence, "drift_flags": drift_flags,
        "detection_method_breakdown": method_breakdown, "total_events": len(window),
        "inferences": window_infs_slim,
    }
    _narratives_cache[cache_key] = (result_payload, time.monotonic())
    return jsonify(result_payload)

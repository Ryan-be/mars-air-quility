"""History API routes — sensor data, baselines, ML context, narratives."""
from __future__ import annotations

import dataclasses
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import database.db_logger as _dbl
from mlss_monitor.rbac import require_role
from database.db_logger import _normalise_ts, get_inferences, save_inference, add_inference_tag
from mlss_monitor import narrative_engine, state
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.detection_engine import DetectionEngine
from mlss_monitor.feature_extractor import FeatureExtractor
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.narrative_engine import _DAY_NAMES

api_history_bp = Blueprint("api_history", __name__)


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
            tvoc_ppb=row.get("tvoc"),
            eco2_ppm=row.get("eco2"),
            temperature_c=row.get("temperature"),
            humidity_pct=row.get("humidity"),
            pm1_ug_m3=row.get("pm1_0"),
            pm25_ug_m3=row.get("pm2_5"),
            pm10_ug_m3=row.get("pm10"),
            co_ppb=row.get("gas_co"),
            no2_ppb=row.get("gas_no2"),
            nh3_ppb=row.get("gas_nh3"),
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


def _build_feature_vector(start: str, end: str) -> dict[str, object]:
    readings = _build_range_readings(start, end)
    baselines: dict[str, float | None] = {}
    engine = state.detection_engine
    if engine and getattr(engine, '_anomaly_detector', None) is not None:
        for field in [
            "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
            "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb",
            "no2_ppb", "nh3_ppb",
        ]:
            try:
                baselines[field] = engine._anomaly_detector.baseline(field)
            except Exception:
                baselines[field] = None
    else:
        for field in [
            "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
            "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb",
            "no2_ppb", "nh3_ppb",
        ]:
            baselines[field] = None
    fv = FeatureExtractor().extract(readings, baselines)
    return {
        "feature_vector": dataclasses.asdict(fv),
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
        candidates = evaluator.evaluate(FeatureVector(**fv_result["feature_vector"])) if fv_result["feature_vector"] else []
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
        add_inference_tag(inference_id, tag, 1.0)

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
    sxy = sum(p[0]*p[1] for p in pairs)
    sx2 = sum(p[0]**2 for p in pairs)
    sy2 = sum(p[1]**2 for p in pairs)
    num = n*sxy - sx*sy
    den = math.sqrt((n*sx2 - sx**2) * (n*sy2 - sy**2))
    return num/den if den != 0 else None


_COMOVEMENT_PHRASES = {
    ("tvoc_ppb", "eco2_ppm"): "TVOC and CO₂ (estimated) rose together — consistent with indoor air pollutant build-up.",
    ("tvoc_ppb", "pm25_ug_m3"): "TVOC and PM2.5 moved together — may indicate combustion or cooking.",
    ("co_ppb", "no2_ppb"): "CO and NO2 resistance moved together — typical of a combustion event.",
    ("humidity_pct", "temperature_c"): "Temperature and humidity changed together — check ventilation or HVAC.",
    ("pm1_ug_m3", "pm25_ug_m3"): "PM1 and PM2.5 tracked closely — consistent with fine particle sources.",
}
_CHANNEL_LABELS = {
    "tvoc_ppb": "TVOC", "eco2_ppm": "CO₂ (estimated)", "temperature_c": "Temperature", "humidity_pct": "Humidity",
    "pm1_ug_m3": "PM1", "pm25_ug_m3": "PM2.5", "pm10_ug_m3": "PM10",
    "co_ppb": "CO (resistance)", "no2_ppb": "NO2 (resistance)", "nh3_ppb": "NH3 (resistance)",
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
        r = _pearson_r(ch_data.get(a, []), ch_data.get(b, []))
        if r is not None and abs(r) > 0.7:
            sentences.append(phrase)
        if len(sentences) >= 3:
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
    sensor_rows = _query_sensor_data(_dbl.DB_FILE, start, end)
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
        "comovement_summary": _comovement_summary(sensor_rows),
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
        rows = _query_sensor_data(
            db_file, ago_start.strftime("%Y-%m-%dT%H:%M:%SZ"), ago_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        if not rows:
            return {}
        result = {}
        for db_col, api_key in _DB_TO_API.items():
            vals = [r.get(db_col) for r in rows if r.get(db_col) is not None]
            result[api_key] = sum(vals)/len(vals) if vals else None
        return result
    except Exception:
        return {}


@api_history_bp.route("/api/history/narratives")
def narratives():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400
    from mlss_monitor.inference_evidence import _CHANNEL_META
    from mlss_monitor.narrative_engine import _parse_utc

    window = get_inferences(limit=2000, include_dismissed=False, start=start, end=end)

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

    return jsonify({
        "period_summary": period_summary, "trend_indicators": trend_indicators,
        "longest_clean_hours": clean["hours"], "longest_clean_start": clean["start"],
        "longest_clean_end": clean["end"],
        "attribution_breakdown": summary, "dominant_source_sentence": dom_sentence,
        "fingerprint_narratives": fp_narratives, "anomaly_model_narratives": model_narratives,
        "pattern_heatmap": heatmap, "pattern_sentence": pattern_sentence, "drift_flags": drift_flags,
        "detection_method_breakdown": method_breakdown, "total_events": len(window),
        "inferences": window_infs_slim,
    })

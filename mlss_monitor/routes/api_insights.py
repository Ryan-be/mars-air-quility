"""Insights Engine configuration API.

All routes require admin role. Writes are atomic via yaml_io.atomic_write.
Engine objects are reloaded in-place after each write (no restart required).
"""
from __future__ import annotations

import dataclasses
import logging

from flask import Blueprint, jsonify, request

from mlss_monitor import state
from mlss_monitor.rbac import require_role
from mlss_monitor.yaml_io import atomic_write, load_yaml

log = logging.getLogger(__name__)
api_insights_bp = Blueprint("api_insights", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _engine():
    """Return the live DetectionEngine or raise RuntimeError."""
    eng = state.detection_engine
    if eng is None:
        raise RuntimeError("DetectionEngine not initialised")
    return eng


def _not_initialised():
    return jsonify({"error": "Detection engine not initialised"}), 503


# ── Dry-run toggle (pre-existing endpoint, kept for compatibility) ────────────

@api_insights_bp.route("/insights-engine/dry-run", methods=["POST"])
@require_role("admin")
def toggle_dry_run():
    engine = state.detection_engine
    if engine is None:
        return jsonify({"error": "DetectionEngine not initialised"}), 503
    new_val = request.get_json(force=True, silent=True) or {}
    engine._dry_run = bool(new_val.get("dry_run", True))
    log.info("DetectionEngine dry_run set to %s", engine._dry_run)
    return jsonify({"dry_run": engine._dry_run})


# ── Rules ────────────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/rules", methods=["GET"])
@require_role("admin")
def get_rules():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    rules = eng._rule_engine._rules
    return jsonify(list(rules))


@api_insights_bp.route("/api/insights-engine/rules", methods=["POST"])
@require_role("admin")
def save_rules():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected JSON array of rules"}), 400
    rules_path = eng._rules_path
    try:
        atomic_write(rules_path, {"rules": data})
        eng._rule_engine.reload()
    except Exception as exc:
        log.error("save_rules: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": f"{len(data)} rule(s) saved and reloaded"})


@api_insights_bp.route("/api/insights-engine/rules/<rule_id>", methods=["PATCH"])
@require_role("admin")
def patch_rule(rule_id: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    updates = request.get_json() or {}
    rules_path = eng._rules_path
    raw = load_yaml(rules_path)
    rules = raw.get("rules", [])
    for rule in rules:
        if rule.get("id") == rule_id:
            rule.update(updates)
            try:
                atomic_write(rules_path, {"rules": rules})
                eng._rule_engine.reload()
            except Exception as exc:
                log.error("patch_rule %r: %s", rule_id, exc)
                return jsonify({"error": str(exc)}), 500
            return jsonify({"message": f"Rule {rule_id!r} updated"})
    return jsonify({"error": f"Rule {rule_id!r} not found"}), 404


# ── Fingerprints ─────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/fingerprints", methods=["GET"])
@require_role("admin")
def get_fingerprints():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    if eng._attribution_engine is None:
        return jsonify([])
    fps = [dataclasses.asdict(fp) for fp in eng._attribution_engine._fingerprints]
    return jsonify(fps)


@api_insights_bp.route("/api/insights-engine/fingerprints", methods=["POST"])
@require_role("admin")
def save_fingerprints():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected JSON array of fingerprints"}), 400
    fp_path = eng._fingerprints_path
    if fp_path is None:
        return jsonify({"error": "Fingerprints not configured"}), 503
    try:
        atomic_write(fp_path, {"sources": data})
        if eng._attribution_engine is not None:
            eng._attribution_engine.reload()
    except Exception as exc:
        log.error("save_fingerprints: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": f"{len(data)} fingerprint(s) saved and reloaded"})


@api_insights_bp.route("/api/insights-engine/fingerprints/<fp_id>", methods=["PATCH"])
@require_role("admin")
def patch_fingerprint(fp_id: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    updates = request.get_json() or {}
    fp_path = eng._fingerprints_path
    if fp_path is None:
        return jsonify({"error": "Fingerprints not configured"}), 503
    raw = load_yaml(fp_path)
    sources = raw.get("sources", [])
    for src in sources:
        if src.get("id") == fp_id:
            src.update(updates)
            try:
                atomic_write(fp_path, {"sources": sources})
                if eng._attribution_engine is not None:
                    eng._attribution_engine.reload()
            except Exception as exc:
                log.error("patch_fingerprint %r: %s", fp_id, exc)
                return jsonify({"error": str(exc)}), 500
            return jsonify({"message": f"Fingerprint {fp_id!r} updated"})
    return jsonify({"error": f"Fingerprint {fp_id!r} not found"}), 404


@api_insights_bp.route("/api/insights-engine/fingerprints/<fp_id>/preview", methods=["POST"])
@require_role("admin")
def preview_fingerprint(fp_id: str):
    """Score the named fingerprint against the current live FeatureVector.

    The FeatureVector is taken from state.feature_vector (updated every 60s
    by the detection cycle). Returns sensor_score, temporal_score, combined
    confidence. Returns 503 if no FeatureVector is available yet.
    """
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()

    fv = state.feature_vector
    if fv is None:
        return jsonify({"error": "No feature vector available yet (cold start)"}), 503

    if eng._attribution_engine is None:
        return jsonify({"error": "Attribution engine not configured"}), 503

    # Find the fingerprint in the live attribution engine
    fp = next(
        (f for f in eng._attribution_engine._fingerprints if f.id == fp_id),
        None,
    )
    if fp is None:
        return jsonify({"error": f"Fingerprint {fp_id!r} not found"}), 404

    from mlss_monitor.attribution.scorer import sensor_score, temporal_score, combine
    ss = sensor_score(fp, fv)
    ts = temporal_score(fp, fv)
    conf = combine(ss, ts)
    return jsonify({
        "fingerprint_id": fp_id,
        "sensor_score": round(ss, 4),
        "temporal_score": round(ts, 4),
        "confidence": round(conf, 4),
        "clears_floor": conf >= fp.confidence_floor,
        "confidence_floor": fp.confidence_floor,
    })


# ── Anomaly ──────────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/anomaly", methods=["GET"])
@require_role("admin")
def get_anomaly():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    det = eng._anomaly_detector
    cfg = det._config
    live = det.live_scores()
    channels = []
    for ch in det._channels():
        n = det._n_seen.get(ch, 0)
        cold_start = cfg.get("cold_start_readings", 1440)
        channels.append({
            "channel": ch,
            "n_seen": n,
            "cold_start": cold_start,
            "ready": n >= cold_start,
            "live_ema": live.get(ch),
        })
    return jsonify({
        "score_threshold": cfg.get("score_threshold", 0.7),
        "cold_start_readings": cfg.get("cold_start_readings", 1440),
        "channels": channels,
    })


@api_insights_bp.route("/api/insights-engine/anomaly", methods=["POST"])
@require_role("admin")
def save_anomaly():
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    data = request.get_json() or {}
    anomaly_path = eng._anomaly_config_path
    raw = load_yaml(anomaly_path)
    anomaly_cfg = raw.get("anomaly", {})

    if "score_threshold" in data:
        try:
            val = float(data["score_threshold"])
            if not 0.0 <= val <= 1.0:
                raise ValueError("out of range")
            anomaly_cfg["score_threshold"] = val
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"score_threshold: {exc}"}), 400

    if "cold_start_readings" in data:
        try:
            val = int(data["cold_start_readings"])
            if val < 0:
                raise ValueError("must be >= 0")
            anomaly_cfg["cold_start_readings"] = val
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"cold_start_readings: {exc}"}), 400

    try:
        atomic_write(anomaly_path, {"anomaly": anomaly_cfg})
        eng._anomaly_detector._load_config()   # reload from file
    except Exception as exc:
        log.error("save_anomaly: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"message": "Anomaly config updated"})


@api_insights_bp.route("/api/insights-engine/anomaly/<channel>/reset", methods=["POST"])
@require_role("admin")
def reset_anomaly_channel(channel: str):
    try:
        eng = _engine()
    except RuntimeError:
        return _not_initialised()
    det = eng._anomaly_detector
    if channel not in det._models:
        return jsonify({"error": f"Channel {channel!r} not found"}), 404
    det.reset_channel(channel)
    return jsonify({"message": f"Channel {channel!r} reset"})


# ── Engine status (summary for admin tab) ────────────────────────────────────

@api_insights_bp.route("/api/insights/engine-status")
@require_role("admin")
def engine_status():
    """Return a summary of the detection engine state for the admin settings tab."""
    engine = state.detection_engine

    # Rules
    rules_info = []
    if engine and engine._rule_engine:
        for r in engine._rule_engine._rules:
            rules_info.append({
                "id": r.get("id", ""),
                "expression": r.get("expression", ""),
                "severity": r.get("severity", ""),
                "confidence": float(r.get("confidence", 0)),
                "event_type": r.get("event_type", ""),
            })

    # Fingerprints
    fps_info = []
    if engine and engine._attribution_engine:
        for fp in engine._attribution_engine._fingerprints:
            fps_info.append({
                "id": fp.id,
                "label": fp.label,
                "floor": fp.confidence_floor,
                "sensors": list(fp.sensors.keys()),
            })

    # Anomaly channel status
    anomaly_info = []
    if engine and engine._anomaly_detector:
        det = engine._anomaly_detector
        for ch in det._config.get("channels", []):
            n = det._n_seen.get(ch, 0)
            cold_start = det._config.get("cold_start_readings", 1440)
            anomaly_info.append({
                "channel": ch,
                "n_seen": n,
                "cold_start": cold_start,
                "ready": n >= cold_start,
            })

    if engine and engine._multivar_detector:
        det = engine._multivar_detector
        cold_start = det._config.get("cold_start_readings", 500)
        for m in det._model_defs():
            mid = m["id"]
            n = det._n_seen.get(mid, 0)
            anomaly_info.append({
                "channel": mid,
                "n_seen": n,
                "cold_start": cold_start,
                "ready": n >= cold_start,
            })

    return jsonify({
        "dry_run": engine._dry_run if engine else True,
        "rules": rules_info,
        "fingerprints": fps_info,
        "anomaly_channels": anomaly_info,
        "rule_count": len(rules_info),
        "fp_count": len(fps_info),
    })


# ── Data sources ─────────────────────────────────────────────────────────────

@api_insights_bp.route("/api/insights-engine/sources", methods=["GET"])
@require_role("admin")
def get_sources():
    enabled_map = state.data_source_enabled
    # Build a lookup from name -> DataSource instance so we can read last_reading_at.
    source_obj_map = {ds.name: ds for ds in (state.data_sources or [])}
    result = []
    for name, enabled in enabled_map.items():
        ds = source_obj_map.get(name)
        lra = ds.last_reading_at if ds is not None else None
        result.append({
            "name": name,
            "enabled": enabled,
            "status": "active" if enabled else "disabled",
            "last_reading_at": lra.isoformat() + "Z" if lra is not None else None,
        })
    return jsonify(result)


@api_insights_bp.route("/api/insights-engine/sources/<name>/enable", methods=["POST"])
@require_role("admin")
def enable_source(name: str):
    if name not in state.data_source_enabled:
        return jsonify({"error": f"Source {name!r} not found"}), 404
    state.data_source_enabled[name] = True
    return jsonify({"message": f"Source {name!r} enabled"})


@api_insights_bp.route("/api/insights-engine/sources/<name>/disable", methods=["POST"])
@require_role("admin")
def disable_source(name: str):
    if name not in state.data_source_enabled:
        return jsonify({"error": f"Source {name!r} not found"}), 404
    state.data_source_enabled[name] = False
    return jsonify({"message": f"Source {name!r} disabled"})

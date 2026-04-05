"""Page routes: dashboard, history, controls, admin."""

from flask import Blueprint, redirect, render_template, url_for

from mlss_monitor import state
from mlss_monitor.rbac import require_role

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@pages_bp.route("/history")
def history_page():
    return render_template("history.html")


@pages_bp.route("/controls")
def controls_page():
    return render_template("controls.html")


@pages_bp.route("/admin")
@require_role("admin")
def admin():
    return render_template("admin.html")


@pages_bp.route("/settings/insights-engine")
@require_role("admin")
def insights_engine():
    engine = state.detection_engine

    # Rules — _rules is list[dict] (raw YAML dicts)
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

    # Multivar detector models
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

    return render_template(
        "insights_engine.html",
        dry_run=engine._dry_run if engine else True,
        rules=rules_info,
        fingerprints=fps_info,
        anomaly=anomaly_info,
        rule_count=len(rules_info),
        fp_count=len(fps_info),
    )


@pages_bp.route("/settings/insights-engine/config")
@require_role("admin")
def ie_config():
    return render_template("ie_config.html")


# Legacy per-section routes — redirect to the unified config page with an anchor
@pages_bp.route("/settings/insights-engine/rules")
@require_role("admin")
def ie_rules():
    return redirect(url_for("pages.ie_config") + "#rules")


@pages_bp.route("/settings/insights-engine/fingerprints")
@require_role("admin")
def ie_fingerprints():
    return redirect(url_for("pages.ie_config") + "#fingerprints")


@pages_bp.route("/settings/insights-engine/anomaly")
@require_role("admin")
def ie_anomaly():
    return redirect(url_for("pages.ie_config") + "#anomaly")


@pages_bp.route("/settings/insights-engine/sources")
@require_role("admin")
def ie_sources():
    return redirect(url_for("pages.ie_config") + "#sources")

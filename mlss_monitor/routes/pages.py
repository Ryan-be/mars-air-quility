"""Page routes: dashboard, history, controls, admin."""

from flask import Blueprint, redirect, render_template, session, url_for

from mlss_monitor import state
from mlss_monitor.grow.storage_check import get_storage_status
from mlss_monitor.rbac import require_role

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@pages_bp.route("/history")
def history_page():
    return render_template("history.html")


@pages_bp.route("/incidents")
def incidents_page():
    return render_template("incidents.html")


@pages_bp.route("/grow")
def grow_fleet():
    # Phase 3 Task 6: pass disk-usage info so the template can surface a
    # "storage almost full" banner when the grow_images mount point is
    # at/over the configured threshold. None on any check failure → the
    # template renders nothing (best-effort; never crashes the page).
    return render_template(
        "grow_fleet.html", storage_status=get_storage_status()
    )


@pages_bp.route("/grow/<int:unit_id>")
def grow_unit_detail(unit_id):
    return render_template("grow_unit_detail.html", unit_id=unit_id)


@pages_bp.route("/grow/errors")
@require_role("viewer", "controller", "admin")
def grow_errors_page():
    """Top-level fleet-wide error log. Viewer-readable; admin actions
    (resolve / snooze) gated client-side off `data-role` and enforced
    server-side by the PATCH endpoint.
    """
    return render_template(
        "grow_errors.html",
        current_role=session.get("user_role", "viewer"),
    )


@pages_bp.route("/grow/settings")
@require_role("admin")
def grow_settings_page():
    """Grow → Settings. Admin-only at the page level even though the
    individual API endpoints have their own RBAC — defence in depth.

    Lives under /grow/settings (rather than /settings/grow) so it sits
    naturally under the Grow sub-nav. The legacy /settings/grow URL is
    redirected to here for backwards compatibility — see
    grow_settings_page_legacy below.

    Phase 3 Task 6: also surfaces the same disk-usage banner as /grow,
    so admins reviewing settings see the warning without having to
    bounce back to the fleet page.
    """
    return render_template(
        "grow_settings.html", storage_status=get_storage_status()
    )


@pages_bp.route("/settings/grow")
@require_role("viewer", "controller", "admin")
def grow_settings_page_legacy():
    """Legacy URL — redirect to the canonical /grow/settings.

    Allows any logged-in role to hit the redirect (so existing bookmarks
    don't 403 on the way to a 302) — the destination route still gates
    on admin via require_role.
    """
    return redirect(url_for("pages.grow_settings_page"), code=302)


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

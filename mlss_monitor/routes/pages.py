"""Page routes: dashboard, history, controls, admin."""

from pathlib import Path

from flask import Blueprint, Response, abort, redirect, render_template, session, url_for

from mlss_monitor import state
from mlss_monitor.grow.storage_check import get_storage_status
from mlss_monitor.rbac import require_role


# Repo root, used by the docs route below to find the markdown files.
# pages.py lives at <repo>/mlss_monitor/routes/pages.py — three .parents up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Whitelist of grow-related docs that the in-app links may reference.
# Anything not in this set gets a 404 from /grow/docs/<name>, so a path-
# traversal attempt like /grow/docs/../../../../etc/passwd is impossible.
_GROW_DOCS = {
    "setup": _REPO_ROOT / "docs" / "PLANT_GROW_UNIT_SETUP.md",
    "hardware": _REPO_ROOT / "docs" / "PLANT_GROW_UNIT_HARDWARE.md",
    "usage": _REPO_ROOT / "docs" / "PLANT_GROW_UNIT_USAGE.md",
    "architecture": _REPO_ROOT / "docs" / "PLANT_GROW_UNIT_ARCHITECTURE.md",
    "database": _REPO_ROOT / "docs" / "DATABASE.md",
}

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@pages_bp.route("/sw.js")
def service_worker():
    """Serve the service worker at root scope.

    Must live at the root URL (not /static/sw.js) because a service
    worker's scope is the directory it's served from — serving from
    /static/ would limit scope to /static/* and the worker couldn't
    handle push notifications for the rest of the site.
    """
    sw_path = _REPO_ROOT / "static" / "sw.js"
    return Response(
        sw_path.read_text(encoding="utf-8"),
        mimetype="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@pages_bp.route("/history")
def history_page():
    return render_template("history.html")


@pages_bp.route("/incidents")
def incidents_page():
    return render_template("incidents.html")


@pages_bp.route("/notifications")
@require_role("viewer", "controller", "admin")
def notifications_page():
    """In-app inbox for recent push notifications (last 30 days)."""
    return render_template("notifications.html")


@pages_bp.route("/grow")
def grow_fleet():
    # Phase 3 Task 6: pass disk-usage info so the template can surface a
    # "storage almost full" banner when the grow_images mount point is
    # at/over the configured threshold. None on any check failure → the
    # template renders nothing (best-effort; never crashes the page).
    #
    # current_role is also passed through so the "+ Add Unit" button can
    # be hidden for non-admins (the underlying peek-once endpoint is
    # admin-only — defence in depth) and the add-unit modal can read
    # body.dataset.role to gate its reveal button.
    return render_template(
        "grow_fleet.html",
        storage_status=get_storage_status(),
        current_role=session.get("user_role", "viewer"),
    )


@pages_bp.route("/grow/<int:unit_id>")
def grow_unit_detail(unit_id):
    # Pass role + user through so the template can stamp them on body.dataset
    # for the journal editor's edit/delete-author gating (Phase 4 #7). The
    # server still enforces auth on every PATCH/DELETE — this is purely
    # visual.
    return render_template(
        "grow_unit_detail.html",
        unit_id=unit_id,
        current_role=session.get("user_role", "viewer"),
        current_user=session.get("user", ""),
    )


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


@pages_bp.route("/grow/docs/<doc_name>")
def grow_doc(doc_name):
    """Serve a grow-related markdown doc from the repo's docs/ directory.

    The Flask static handler can't see docs/ (it serves static/ only),
    so the in-app "Full setup guide" link from the empty-state panel
    used to 404. This route reads the markdown from disk and returns
    it as text/markdown — modern browsers render plain text legibly,
    and any markdown-preview extension renders it nicely. Anyone with
    GitHub access can also read the same file there.

    Whitelisted doc names only (see _GROW_DOCS) so this can't be
    abused as a generic file-read endpoint.
    """
    path = _GROW_DOCS.get(doc_name)
    if path is None or not path.is_file():
        abort(404)
    return Response(
        path.read_text(encoding="utf-8"),
        mimetype="text/markdown; charset=utf-8",
    )


@pages_bp.route("/controls")
def controls_page():
    return render_template("controls.html")


@pages_bp.route("/admin")
@require_role("admin")
def admin():
    return render_template("admin.html")


@pages_bp.route("/admin/backup")
@require_role("admin")
def admin_backup():
    """Backup-pipeline operator UI. Admin-only at the page level even
    though every consumed API endpoint also requires admin — defence
    in depth, and a non-admin landing here gets an honest 403 instead
    of a half-rendered "loading…" stuck panel."""
    return render_template("admin_backup.html")


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

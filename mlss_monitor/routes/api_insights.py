"""Insights Engine API routes: dry_run toggle."""

import logging

from flask import Blueprint, jsonify, request

from mlss_monitor import state
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_insights_bp = Blueprint("api_insights", __name__)


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

"""Generic effector API — list effectors and toggle them on/off.

This is a thin wrapper over :mod:`mlss_monitor.effectors`.  It replaces the
on/off write on ``POST /api/fan?state=on|off`` with a registry-driven
``POST /api/effector`` that dispatches to a named device.  Device-specific
read endpoints (``/api/fan/status``, ``/api/fan/auto-status``,
``/api/fan/settings``, ``/api/fan/mode``) remain on their own blueprint.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from mlss_monitor import effectors
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_effectors_bp = Blueprint("api_effectors", __name__)


@api_effectors_bp.route("/api/effectors", methods=["GET"])
def list_effectors():
    """Return a snapshot of every registered effector."""
    return jsonify([
        effectors.snapshot(effectors.get(k)) for k in effectors.all_keys()
    ])


@api_effectors_bp.route("/api/effector", methods=["POST"])
@require_role("controller", "admin")
def set_effector():
    """Toggle an effector on or off via ``{"key": ..., "state": "on"|"off"}``."""
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key")
    desired = data.get("state")

    if not key:
        return jsonify({"error": "'key' is required in the request body."}), 400
    if desired not in ("on", "off"):
        return jsonify({"error": "'state' must be 'on' or 'off'."}), 400

    effector = effectors.get(key)
    if effector is None:
        return jsonify({"error": f"Unknown effector {key!r}"}), 404

    try:
        effectors.set_state(effector, desired == "on")
    except Exception as exc:
        log.error("set_effector %r: %s", key, exc)
        return jsonify({"error": f"Error setting effector: {str(exc)}"}), 500

    return jsonify({"message": f"Effector {key!r} set to {desired}.",
                    "key": key, "state": desired}), 200

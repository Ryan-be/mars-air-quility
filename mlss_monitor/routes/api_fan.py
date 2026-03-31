"""Fan control API routes: toggle, status, settings, auto-status."""

import asyncio
import logging

from flask import Blueprint, jsonify, request

from database.db_logger import (
    get_fan_settings, get_unit_rate, set_fan_enabled, update_fan_settings,
)
from mlss_monitor import state
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_fan_bp = Blueprint("api_fan", __name__)


@api_fan_bp.route("/api/fan", methods=["POST"])
@require_role("controller", "admin")
def control_fan():
    try:
        cmd = request.args.get("state")
        if cmd not in ("on", "off", "auto"):
            return jsonify({"error": "'state' must be 'on', 'off', or 'auto'."}), 400

        if cmd == "auto":
            state.fan_mode = "auto"
            # Sync: also enable auto in the DB so settings page stays in sync
            set_fan_enabled(True)
        else:
            state.fan_mode = "manual"
            state.fan_state = cmd
            # Sync: disable auto in the DB so settings page stays in sync
            set_fan_enabled(False)
            asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.switch(cmd == "on"), state.thread_loop
            ).result()

        return jsonify({"message": f"Fan set to {cmd} successfully.", "mode": state.fan_mode}), 200
    except Exception as e:
        log.error("Error controlling fan: %s", e)
        return jsonify({"error": f"Error controlling fan: {str(e)}"}), 500


@api_fan_bp.route("/api/fan/status", methods=["GET"])
def get_fan_status():
    try:
        update_task = asyncio.run_coroutine_threadsafe(
            state.fan_smart_plug.plug.update(), state.thread_loop
        )
        update_task.result()

        state_task = asyncio.run_coroutine_threadsafe(
            state.fan_smart_plug.get_state(), state.thread_loop
        )
        plug_state = state_task.result()

        try:
            power_task = asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.get_power(), state.thread_loop
            )
            plug_state.update(power_task.result(timeout=5))
        except Exception as exc:
            log.error("[get_fan_status] get_power failed: %s", exc)
            plug_state["power_w"] = None
            plug_state["today_kwh"] = None

        plug_state["mode"] = state.fan_mode
        plug_state["unit_rate_pence"] = get_unit_rate()
        return jsonify(plug_state), 200
    except Exception as e:
        return jsonify({"error": f"Error retrieving fan state: {str(e)}"}), 500


@api_fan_bp.route("/api/fan/settings", methods=["GET"])
def get_fan_settings_route():
    return jsonify(get_fan_settings())


@api_fan_bp.route("/api/fan/settings", methods=["POST"])
@require_role("admin")
def update_fan_settings_route():
    data = request.get_json()
    enabled = data.get("enabled", False)
    update_fan_settings(
        tvoc_min=data.get("tvoc_min", 0),
        tvoc_max=data.get("tvoc_max", 500),
        temp_min=data.get("temp_min", 0.0),
        temp_max=data.get("temp_max", 20.0),
        enabled=enabled,
        temp_enabled=data.get("temp_enabled", True),
        tvoc_enabled=data.get("tvoc_enabled", True),
        humidity_enabled=data.get("humidity_enabled", False),
        humidity_max=data.get("humidity_max", 70.0),
        pm25_enabled=data.get("pm25_enabled", False),
        pm25_max=data.get("pm25_max", 25.0),
        pm_stale_minutes=data.get("pm_stale_minutes", 10.0),
    )
    # Sync: keep in-memory fan_mode consistent with the DB toggle
    state.fan_mode = "auto" if enabled else "manual"
    return jsonify({"message": "Fan settings updated"})


@api_fan_bp.route("/api/fan/auto-status", methods=["GET"])
def get_auto_status():
    """Return the last auto-evaluation results for the controls (i) tooltip."""
    settings = get_fan_settings()
    return jsonify({
        "mode": state.fan_mode,
        "auto_enabled": settings["enabled"],
        "action": state.last_auto_action,
        "rules": state.last_auto_evaluation or [],
    })

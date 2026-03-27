"""Fan control API routes: toggle, status, settings."""

import asyncio
import logging

from flask import Blueprint, jsonify, request

from database.db_logger import get_fan_settings, get_unit_rate, update_fan_settings
from mlss_monitor import state

log = logging.getLogger(__name__)

api_fan_bp = Blueprint("api_fan", __name__)


@api_fan_bp.route("/api/fan", methods=["POST"])
def control_fan():
    try:
        cmd = request.args.get("state")
        if cmd not in ("on", "off", "auto"):
            return jsonify({"error": "'state' must be 'on', 'off', or 'auto'."}), 400

        if cmd == "auto":
            state.fan_mode = "auto"
            state.fan_state = "off"
        else:
            state.fan_mode = "manual"
            state.fan_state = cmd
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
def update_fan_settings_route():
    data = request.get_json()
    update_fan_settings(
        tvoc_min=data.get("tvoc_min", 0),
        tvoc_max=data.get("tvoc_max", 500),
        temp_min=data.get("temp_min", 0.0),
        temp_max=data.get("temp_max", 20.0),
        enabled=data.get("enabled", False),
    )
    return jsonify({"message": "Fan settings updated"})

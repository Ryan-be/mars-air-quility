"""Fan control API routes: status, mode (auto/manual), settings, auto-status.

The on/off write has moved to the generic effector API
(:mod:`mlss_monitor.routes.api_effectors`); the remaining endpoints expose
fan-specific reads (live plug telemetry, last auto-evaluation) and the
auto-mode threshold config that is unique to this effector.
"""

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


@api_fan_bp.route("/api/fan/mode", methods=["POST"])
@require_role("controller", "admin")
def set_fan_mode():
    """Switch the fan between ``auto`` and ``manual`` modes.

    Accepts a JSON body ``{"mode": "auto"|"manual"}`` or the legacy
    ``?mode=`` query parameter.  Manual mode leaves the plug in its current
    state; use ``POST /api/effector`` to flip it on or off.
    """
    data = request.get_json(force=True, silent=True) or {}
    mode = data.get("mode") or request.args.get("mode")
    if mode not in ("auto", "manual"):
        return jsonify({"error": "'mode' must be 'auto' or 'manual'."}), 400
    state.set_fan_mode(mode)
    set_fan_enabled(mode == "auto")
    return jsonify({"message": f"Fan mode set to {mode}.", "mode": mode}), 200


@api_fan_bp.route("/api/fan/status", methods=["GET"])
def get_fan_status():
    try:
        try:
            update_task = asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.plug.update(), state.thread_loop
            )
            update_task.result(timeout=5)

            state_task = asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.get_state(), state.thread_loop
            )
            plug_state = state_task.result(timeout=5)
        except Exception as plug_exc:
            # Include the exception class name — some exceptions have an
            # empty `str()` (notably `concurrent.futures.TimeoutError`, which
            # is what `run_coroutine_threadsafe(...).result(timeout=...)`
            # raises), which would otherwise log as just "plug read failed:"
            # with nothing after the colon and hide the real failure mode.
            log.error(
                "[get_fan_status] plug read failed: %s: %s",
                type(plug_exc).__name__, plug_exc,
            )
            return jsonify({
                "error": f"Smart plug unavailable: {type(plug_exc).__name__}: {plug_exc}",
            }), 503

        try:
            power_task = asyncio.run_coroutine_threadsafe(
                state.fan_smart_plug.get_power(), state.thread_loop
            )
            plug_state.update(power_task.result(timeout=5))
        except Exception as exc:
            log.error(
                "[get_fan_status] get_power failed: %s: %s",
                type(exc).__name__, exc,
            )
            plug_state["power_w"] = None
            plug_state["today_kwh"] = None

        plug_state["mode"] = state.get_fan_snapshot()["fan_mode"]
        plug_state["unit_rate_pence"] = get_unit_rate()
        return jsonify(plug_state), 200
    except Exception as e:
        log.error(
            "[get_fan_status] unexpected failure: %s: %s",
            type(e).__name__, e,
        )
        return jsonify({
            "error": f"Error retrieving fan state: {type(e).__name__}: {e}",
        }), 500


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
    state.set_fan_mode("auto" if enabled else "manual")
    return jsonify({"message": "Fan settings updated"})


@api_fan_bp.route("/api/fan/auto-status", methods=["GET"])
def get_auto_status():
    """Return the last auto-evaluation results for the controls (i) tooltip."""
    settings = get_fan_settings()
    snap = state.get_fan_snapshot()
    return jsonify({
        "mode": snap["fan_mode"],
        "auto_enabled": settings["enabled"],
        "action": snap["last_auto_action"],
        "rules": snap["last_auto_evaluation"] or [],
    })

"""Per-unit Configure-tab PUT endpoints.

Five endpoints under /api/grow/units/<unit_id>/...
This module holds the first two: /profile and /pid. Calibration,
light_windows, and safety_override are added in Tasks 3-4.

Auth: session-based via the global check_auth middleware in
mlss_monitor.app. RBAC is enforced via @require_role —
controller+admin for routine config, admin-only for safety_override
(Task 4).

WS push: after a successful DB write, the route schedules a best-effort
`config_changed` command on the listener loop via
`asyncio.run_coroutine_threadsafe`. If the unit isn't connected (or the
underlying send raises), the request still returns 200 — the DB write
already committed and the firmware will re-pull on its next reconnect
(Task 8 wires the firmware-side handler).
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from database.init_db import DB_FILE
from mlss_contracts.config_payloads import PIDUpdate, ProfileUpdate
from mlss_monitor import state
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_grow_config_bp = Blueprint("api_grow_config", __name__)

# Best-effort WS push: short timeout so a slow listener can't wedge a
# request thread for long. The DB write is already committed by the time
# we get here — if the push times out, the firmware re-pulls on reconnect.
_PUSH_TIMEOUT_S = 2


def _serialise_validation_errors(errors: list) -> list:
    """Strip non-JSON-serializable values from pydantic ValidationError.errors().

    pydantic v2 puts a raw `ValueError` instance under `ctx.error` when a
    `model_validator` raises — Flask's jsonify can't serialise that. Convert
    it to a string so the client gets a useful detail block instead of a 500.
    """
    cleaned = []
    for err in errors:
        item = dict(err)
        ctx = item.get("ctx")
        if isinstance(ctx, dict) and isinstance(ctx.get("error"), Exception):
            item["ctx"] = {**ctx, "error": str(ctx["error"])}
        cleaned.append(item)
    return cleaned


def _push_config_changed(unit_id: int, section: str) -> None:
    """Schedule a `config_changed` WS command to a unit. Never raises.

    Match the framing established by `api_grow_units._push_command_blocking`:
    the registry's `send_to_unit` takes a JSON string, so we json.dumps the
    {type, ts, payload} envelope here. If the registry/loop aren't wired up
    (tests without a listener, or pre-startup) silently no-op. If the
    underlying ws.send raises (peer dropped, ConnectionClosed), log at
    DEBUG and return — the caller's request still returns 200 because the
    DB write already succeeded.
    """
    registry = state.grow_ws_registry
    listener_loop = state.grow_ws_loop
    if registry is None or listener_loop is None:
        return
    msg = json.dumps({
        "type": "command",
        "ts": datetime.utcnow().isoformat() + "Z",
        "payload": {"kind": "config_changed", "section": section},
    })
    try:
        future = asyncio.run_coroutine_threadsafe(
            registry.send_to_unit(unit_id, msg), listener_loop
        )
        future.result(timeout=_PUSH_TIMEOUT_S)
    except Exception as exc:
        log.debug(
            "config_changed push to unit %s (section=%s) failed (best-effort): %s",
            unit_id, section, exc,
        )


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/profile", methods=["PUT"]
)
@require_role("controller", "admin")
def put_profile(unit_id):
    body = request.get_json(silent=True) or {}
    try:
        payload = ProfileUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": _serialise_validation_errors(exc.errors()),
        }), 400

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        # Empty PUT — no-op success. Idempotent retry from the UI is fine.
        return jsonify({"ok": True})

    # A phase change is special: it stamps phase_set_by='user' + a fresh
    # phase_set_at so the timeline + image-classifier audit can distinguish
    # user-driven phase transitions from classifier-driven ones.
    phase_changed = "current_phase" in fields
    set_clauses = []
    values: list = []
    for k, v in fields.items():
        set_clauses.append(f"{k}=?")
        values.append(v)
    if phase_changed:
        set_clauses.extend(["phase_set_by=?", "phase_set_at=?"])
        values.extend(["user", datetime.utcnow()])

    values.append(unit_id)
    sql = f"UPDATE grow_units SET {', '.join(set_clauses)} WHERE id=?"

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cur = conn.execute(sql, values)
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "unit_not_found"}), 404
        conn.commit()
    finally:
        conn.close()

    _push_config_changed(unit_id, "profile")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# /pid
# ---------------------------------------------------------------------------


# Map PIDUpdate fields → grow_units column names.
#
# NOTE: deadband_pct has no override column in the schema (see
# database/grow_schema.py). We accept the field at the API boundary for
# forward-compat — frontend or firmware can send it without a 400 — but
# silently drop it. A future migration can add the column; until then,
# the resolved value comes from grow_plant_profiles.deadband_pct.
_PID_COLUMN_MAP = {
    "target_pct":      "watering_target_override",
    "kp":              "watering_kp_override",
    "ki":              "watering_ki_override",
    "kd":              "watering_kd_override",
    "soak_window_min": "soak_window_min_override",
    "min_pulse_s":     "pulse_min_s_override",
    "max_pulse_s":     "pulse_max_s_override",
}


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/pid", methods=["PUT"]
)
@require_role("controller", "admin")
def put_pid(unit_id):
    body = request.get_json(silent=True) or {}
    try:
        payload = PIDUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": _serialise_validation_errors(exc.errors()),
        }), 400

    fields = payload.model_dump(exclude_none=True)
    persistable = {k: v for k, v in fields.items() if k in _PID_COLUMN_MAP}
    if not persistable:
        # Either an empty body, or only deadband_pct (currently non-persisted).
        # Still verify the unit exists so the caller doesn't get a misleading
        # 200 for a unit that isn't there.
        conn = sqlite3.connect(DB_FILE, timeout=10)
        try:
            row = conn.execute(
                "SELECT 1 FROM grow_units WHERE id=?", (unit_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return jsonify({"error": "unit_not_found"}), 404
        return jsonify({"ok": True})

    set_clauses = [f"{_PID_COLUMN_MAP[k]}=?" for k in persistable]
    values = list(persistable.values()) + [unit_id]
    sql = f"UPDATE grow_units SET {', '.join(set_clauses)} WHERE id=?"

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cur = conn.execute(sql, values)
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "unit_not_found"}), 404
        conn.commit()
    finally:
        conn.close()

    _push_config_changed(unit_id, "pid")
    return jsonify({"ok": True})

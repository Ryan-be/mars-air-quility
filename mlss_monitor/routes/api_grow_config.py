"""Per-unit Configure-tab PUT endpoints.

Five endpoints under /api/grow/units/<unit_id>/...
  * /profile, /pid, /light_windows, /calibration  — controller+admin,
    best-effort config_changed WS push (DB-write durable on offline)
  * /safety_override                              — admin-only,
    synchronous WS push (202 on confirmed delivery, 503 on offline)

Plus one bearer-authenticated firmware pull endpoint:
  * GET /api/grow/units/<unit_id>/config          — bearer-token auth
    (firmware pulls fresh config after a `config_changed` push;
    no Flask session required, since the firmware has none).

Auth: session-based via the global check_auth middleware in
mlss_monitor.app — except for the GET /config endpoint, which auths
via the per-unit bearer token (same secret used to authenticate the
WS upgrade). RBAC is enforced via @require_role — controller+admin
for routine config, admin-only for safety_override.

WS push for routine config: after a successful DB write, the route
schedules a best-effort `config_changed` command on the listener loop
via `asyncio.run_coroutine_threadsafe`. If the unit isn't connected
(or the underlying send raises), the request still returns 200 — the
DB write already committed and the firmware will re-pull on its next
reconnect.

Safety override is the exception: it's intent-to-act-now, so a
disconnected unit should fail loudly (503) rather than silently. We
also write an audit row into grow_errors before returning 202.
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from typing import NamedTuple

from flask import Blueprint, jsonify, request, session
from pydantic import ValidationError

from database.init_db import DB_FILE
from mlss_contracts.config_payloads import (
    CalibrationUpdate,
    LightWindowsUpdate,
    PhotoScheduleUpdate,
    PIDUpdate,
    ProfileUpdate,
    SafetyOverrideRequest,
)
from mlss_monitor import state
from mlss_monitor.grow.api_helpers import serialise_validation_errors
from mlss_monitor.rbac import require_role
from mlss_monitor.routes.api_grow_ws import _validate_bearer

log = logging.getLogger(__name__)

api_grow_config_bp = Blueprint("api_grow_config", __name__)

# Best-effort WS push: short timeout so a slow listener can't wedge a
# request thread for long. The DB write is already committed by the time
# we get here — if the push times out, the firmware re-pulls on reconnect.
_PUSH_TIMEOUT_S = 2

# Safety override push: longer than the best-effort timeout because a
# 503 here is a real user-visible failure (the action didn't happen).
# Still bounded so a hung listener can't wedge an admin's browser.
_SAFETY_PUSH_TIMEOUT_S = 5


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
            "detail": serialise_validation_errors(exc.errors()),
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


# Single registry of every PID-tunable field, with all four representations
# the codebase needs (response key on firmware GET, override column on
# grow_units, profile column on grow_plant_profiles, contracts field on
# PIDUpdate). Replaces four parallel dicts that previously each encoded
# one direction of the same mapping.
#
# Note the asymmetry in the response_key/contracts_field row for the
# moisture target: PIDUpdate exposes `target_pct` (a write-side
# convenience name), but the firmware GET response uses `watering_target`
# (matches the override column prefix). The profile column is
# `target_moisture_pct`. The other six rows happen to use the same name
# across all three.
#
# NOTE on deadband_pct: it has no override column in the schema (see
# database/grow_schema.py), so it isn't in this registry. We still accept
# it at the API boundary for forward-compat (see put_pid) but silently
# drop it. A future migration can add the column, at which point a row
# would land here.
class _PIDFieldDef(NamedTuple):
    """One PID-tunable field, with all four representations.

    Adding a future PID-tunable means appending one tuple here rather
    than touching four scattered dicts.
    """
    response_key: str       # JSON field on firmware GET /config (overrides{})
    override_column: str    # column on grow_units (NULL → use profile default)
    profile_column: str     # column on grow_plant_profiles
    contracts_field: str    # field name on PIDUpdate (often == response_key)


_PID_FIELDS: tuple[_PIDFieldDef, ...] = (
    _PIDFieldDef("watering_target", "watering_target_override",
                 "target_moisture_pct", "target_pct"),
    _PIDFieldDef("kp", "watering_kp_override", "kp", "kp"),
    _PIDFieldDef("ki", "watering_ki_override", "ki", "ki"),
    _PIDFieldDef("kd", "watering_kd_override", "kd", "kd"),
    _PIDFieldDef("soak_window_min", "soak_window_min_override",
                 "soak_window_min", "soak_window_min"),
    _PIDFieldDef("min_pulse_s", "pulse_min_s_override",
                 "min_pulse_s", "min_pulse_s"),
    _PIDFieldDef("max_pulse_s", "pulse_max_s_override",
                 "max_pulse_s", "max_pulse_s"),
)

# Derived dicts — derived from _PID_FIELDS so the call sites that already
# expect dicts can keep working unchanged. If you need to add a new PID
# tunable, do it in _PID_FIELDS above; do not edit these.
_PID_COLUMN_MAP = {f.contracts_field: f.override_column for f in _PID_FIELDS}


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
            "detail": serialise_validation_errors(exc.errors()),
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


# ---------------------------------------------------------------------------
# /light_windows
# ---------------------------------------------------------------------------


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/light_windows", methods=["PUT"]
)
@require_role("controller", "admin")
def put_light_windows(unit_id):
    """Replace all light windows for one (unit, phase) pair.

    Strategy is delete-then-insert scoped to (unit_id, phase): the PUT
    body provides the full set of windows for one phase; the route
    deletes the existing rows for that phase and inserts the new set.
    Other phases' windows are untouched.

    Empty `windows` list is valid — it clears all rows for that
    (unit, phase) pair, which means the unit falls back to the plant
    profile's default light_hours on the firmware side.

    Each row gets a `sort_order` matching its index in the request so
    firmware + frontend render windows deterministically.
    """
    body = request.get_json(silent=True) or {}
    try:
        payload = LightWindowsUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": serialise_validation_errors(exc.errors()),
        }), 400

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        if not conn.execute(
            "SELECT 1 FROM grow_units WHERE id=?", (unit_id,),
        ).fetchone():
            return jsonify({"error": "unit_not_found"}), 404

        # Replace all windows for this (unit, phase). Other phases untouched.
        # sqlite3 default isolation_level='' opens an implicit transaction on
        # the first DML statement — DELETE + INSERTs share one transaction and
        # roll back together if any INSERT raises (since `finally: conn.close()`
        # runs without a prior commit). The conn.commit() at the end is what
        # makes the changes durable.
        conn.execute(
            "DELETE FROM grow_light_windows WHERE unit_id=? AND phase=?",
            (unit_id, payload.phase),
        )
        for i, w in enumerate(payload.windows):
            conn.execute(
                "INSERT INTO grow_light_windows "
                "(unit_id, phase, start_hh_mm, end_hh_mm, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (unit_id, payload.phase, w.start, w.end, i),
            )
        conn.commit()
    finally:
        conn.close()

    _push_config_changed(unit_id, "light_windows")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# /calibration
# ---------------------------------------------------------------------------


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/calibration", methods=["PUT"]
)
@require_role("controller", "admin")
def put_calibration(unit_id):
    """Write soil moisture sensor calibration (dry_raw + wet_raw).

    Both raw values are written together; the contracts model rejects an
    inverted (dry >= wet) pair, so the route only has to UPDATE and
    confirm the unit existed.
    """
    body = request.get_json(silent=True) or {}
    try:
        payload = CalibrationUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": serialise_validation_errors(exc.errors()),
        }), 400

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cur = conn.execute(
            "UPDATE grow_units SET soil_dry_raw=?, soil_wet_raw=? WHERE id=?",
            (payload.dry_raw, payload.wet_raw, unit_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "unit_not_found"}), 404
        conn.commit()
    finally:
        conn.close()

    _push_config_changed(unit_id, "calibration")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# /photo_schedule
# ---------------------------------------------------------------------------


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/photo_schedule", methods=["PUT"]
)
@require_role("controller", "admin")
def put_photo_schedule(unit_id):
    """Set the photo-capture window for one unit.

    Body (PhotoScheduleUpdate):
      * `{"start_hour": null, "end_hour": null}` — capture 24/7 (default)
      * `{"start_hour": 6, "end_hour": 22}` — capture between 06:00-22:00 UTC
      * Wraps midnight when start > end (e.g. 22..6 = 22:00 through 06:00)

    Wraps the same best-effort `config_changed` push as the rest of the
    Configure-tab endpoints — DB write is durable, firmware re-pulls
    on next reconnect if the push misses.
    """
    body = request.get_json(silent=True) or {}
    try:
        payload = PhotoScheduleUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": serialise_validation_errors(exc.errors()),
        }), 400

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        cur = conn.execute(
            "UPDATE grow_units SET "
            "photo_active_start_hour=?, photo_active_end_hour=? "
            "WHERE id=?",
            (payload.start_hour, payload.end_hour, unit_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "unit_not_found"}), 404
        conn.commit()
    finally:
        conn.close()

    _push_config_changed(unit_id, "photo_schedule")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# /safety_override (admin-only, synchronous push)
# ---------------------------------------------------------------------------


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/safety_override", methods=["POST"]
)
@require_role("admin")  # admin-only — stricter than the others
def post_safety_override(unit_id):
    """Push a safety_override command and audit-trail it.

    Unlike the other Configure-tab endpoints, this is intent-to-act-now:
    the unit must be online for the action to actually happen, so we
    push synchronously and surface a 503 if the unit isn't connected.
    Every successful invocation lands in grow_errors with severity=info,
    kind=safety_override_invoked, including the action, duration,
    acknowledged warnings, and the user who triggered it — so the
    bypass-PID path always leaves an audit trail.
    """
    body = request.get_json(silent=True) or {}
    try:
        payload = SafetyOverrideRequest(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": serialise_validation_errors(exc.errors()),
        }), 400

    # Verify unit exists before pushing — keeps a 404 from masquerading as
    # a 503 when the unit_id is wrong (different from a real-but-offline unit).
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        if not conn.execute(
            "SELECT 1 FROM grow_units WHERE id=?", (unit_id,),
        ).fetchone():
            return jsonify({"error": "unit_not_found"}), 404
    finally:
        conn.close()

    triggered_by = session.get("user") or "unknown"
    command = json.dumps({
        "type": "command",
        "ts": datetime.utcnow().isoformat() + "Z",
        "payload": {
            "kind": "safety_override",
            "action": payload.action,
            "duration_s": payload.duration_s,
        },
    })

    # Synchronous push — a 503 here is a real failure (action didn't
    # happen), so we audit-record only after the push confirms delivery.
    registry = state.grow_ws_registry
    listener_loop = state.grow_ws_loop
    if registry is None or listener_loop is None:
        return jsonify({"error": "ws_listener_not_running"}), 503
    try:
        future = asyncio.run_coroutine_threadsafe(
            registry.send_to_unit(unit_id, command), listener_loop
        )
        future.result(timeout=_SAFETY_PUSH_TIMEOUT_S)
    except Exception as exc:
        log.warning(
            "safety_override push to unit %s failed: %s", unit_id, exc
        )
        return jsonify({
            "error": "unit_not_connected",
            "detail": str(exc),
        }), 503

    # Audit trail. Recorded only after the push confirmed — if the unit
    # never received the command, no action happened and no audit row.
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute(
            "INSERT INTO grow_errors "
            "(unit_id, timestamp_utc, severity, kind, message, details_json) "
            "VALUES (?, ?, 'info', 'safety_override_invoked', ?, ?)",
            (
                unit_id,
                datetime.utcnow(),
                f"safety_override action={payload.action} "
                f"duration_s={payload.duration_s}",
                json.dumps({
                    "action": payload.action,
                    "duration_s": payload.duration_s,
                    "acknowledged_warnings": payload.acknowledged_warnings,
                    "triggered_by": triggered_by,
                }),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True}), 202


# ---------------------------------------------------------------------------
# GET /config (firmware pull, bearer-authenticated)
# ---------------------------------------------------------------------------


def _resolve_overrides(unit_row, profile_row) -> dict:
    """Combine unit overrides with plant profile defaults.

    For each PID field, prefer the unit's `*_override` column when it
    is non-NULL, else fall back to the matching plant profile column.
    Returns a dict with concrete (never-null) values for every response
    key in `_PID_FIELDS` — plant_profiles has NOT NULL constraints on
    every column we read from, so the fallback is always defined.

    Output ordering follows `_PID_FIELDS` declaration order, which is the
    canonical order the firmware expects in UnitConfig.overrides.
    """
    out = {}
    for f in _PID_FIELDS:
        override_val = unit_row[f.override_column]
        if override_val is not None:
            out[f.response_key] = override_val
            continue
        if profile_row is None:
            # Defensive: shouldn't happen because we seed a 'generic'
            # profile per phase, but if a unit's plant_type points to a
            # row that doesn't exist, surface NULL rather than crashing.
            out[f.response_key] = None
            continue
        out[f.response_key] = profile_row[f.profile_column]
    return out


@api_grow_config_bp.route(
    "/api/grow/units/<int:unit_id>/config", methods=["GET"]
)
def get_unit_config(unit_id):
    """Bearer-authenticated firmware pull of the latest config.

    Auth: per-unit bearer token in `Authorization: Bearer <token>`. The
    Flask session check_auth middleware is bypassed because this
    endpoint is registered in `_PUBLIC_ENDPOINTS` (mlss_monitor/app.py);
    the firmware has no GitHub OAuth identity, so session-based auth is
    not an option here.

    Response shape: same five top-level keys the Configure-tab GET on
    /api/grow/units/<id> introduced in Task 5, plus current_phase +
    plant_type so the firmware knows which phase's light_windows to
    apply. Override fields are RESOLVED inline against the matching
    grow_plant_profiles row before responding — this means firmware
    never sees a `null` field and doesn't have to maintain its own
    profile lookup table.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "missing_bearer"}), 401
    token = auth_header[7:].strip()
    if not _validate_bearer(unit_id, token):
        return jsonify({"error": "invalid_token"}), 401

    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        unit_row = conn.execute(
            "SELECT * FROM grow_units WHERE id=? AND is_active=1",
            (unit_id,),
        ).fetchone()
        if unit_row is None:
            # Bearer validated against the DB row, so this would only
            # happen on a deactivate-between-validate-and-fetch race.
            return jsonify({"error": "unit_not_found"}), 404

        # Plant profile defaults for null overrides. (plant_type, current_phase)
        # is UNIQUE; a missing row falls back to the seeded 'generic' for the
        # same phase, then NULL.
        profile_row = conn.execute(
            "SELECT * FROM grow_plant_profiles "
            "WHERE plant_type=? AND phase=?",
            (unit_row["plant_type"], unit_row["current_phase"]),
        ).fetchone()
        if profile_row is None:
            profile_row = conn.execute(
                "SELECT * FROM grow_plant_profiles "
                "WHERE plant_type='generic' AND phase=?",
                (unit_row["current_phase"],),
            ).fetchone()

        lw_rows = conn.execute(
            "SELECT phase, start_hh_mm, end_hh_mm "
            "FROM grow_light_windows WHERE unit_id=? "
            "ORDER BY phase, sort_order",
            (unit_id,),
        ).fetchall()

        # Household-wide holiday mode flag — included so firmware can
        # short-circuit pump pulses on the next reconnect-pull. Stored as
        # "0"/"1" in app_settings; absence (or any other value) means OFF.
        hm_row = conn.execute(
            "SELECT value FROM app_settings WHERE key='grow_holiday_mode'"
        ).fetchone()
        holiday_mode = hm_row is not None and hm_row[0] == "1"
    finally:
        conn.close()

    light_windows: dict[str, list] = {}
    for r in lw_rows:
        light_windows.setdefault(r["phase"], []).append(
            {"start": r["start_hh_mm"], "end": r["end_hh_mm"]}
        )

    # Photo capture schedule. Surfaces as a 2-tuple [start, end] so the
    # firmware can map directly to its `LoopConfig.photo_active_hours`
    # tuple field. Both NULL ⇒ capture 24/7 (the new default replacing
    # the previous hardcoded (6, 22) — see PhotoScheduleUpdate docstring
    # for why the old assumption was wrong).
    photo_active_hours = (
        [unit_row["photo_active_start_hour"], unit_row["photo_active_end_hour"]]
        if unit_row["photo_active_start_hour"] is not None
        and unit_row["photo_active_end_hour"] is not None
        else None
    )

    return jsonify({
        "overrides":     _resolve_overrides(unit_row, profile_row),
        "calibration":   {
            "dry_raw": unit_row["soil_dry_raw"],
            "wet_raw": unit_row["soil_wet_raw"],
        },
        "light_windows": light_windows,
        "current_phase": unit_row["current_phase"],
        "plant_type":    unit_row["plant_type"],
        "holiday_mode":  holiday_mode,
        # Per-unit buffer retention override (NULL → firmware uses its
        # _DEFAULT_BUFFER_RETENTION_DAYS, which mirrors the
        # `grow_default_buffer_retention_days` app_setting). Surfacing
        # the raw value lets firmware apply it on every reconnect-pull
        # without a separate API call. See ws_client.run_forever, which
        # passes this to LocalBuffer.prune via the
        # buffer_retention_days_provider closure built in service.py.
        "buffer_retention_days": unit_row["buffer_retention_days"],
        "photo_active_hours":    photo_active_hours,
    })

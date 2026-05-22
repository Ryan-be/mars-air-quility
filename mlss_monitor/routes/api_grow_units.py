"""REST endpoints for the browser to read grow unit state.

GET    /api/grow/units                                 — fleet view, list
GET    /api/grow/units/<id>                            — detail
POST   /api/grow/units/<id>/identify                   — push identify cmd via WS
POST   /api/grow/units/<id>/water-now                  — push manual watering cmd via WS
POST   /api/grow/units/<id>/rotate-token               — admin: rotate bearer token
GET    /api/grow/units/<id>/token/peek-once            — admin: one-shot reveal
DELETE /api/grow/units/<id>                            — admin: soft-delete unit
POST   /api/grow/units/<id>/clear-buffer               — admin: WS push clear_buffer
DELETE /api/grow/units/<id>/photos                     — admin: wipe photos (DB + disk)
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

from database.init_db import DB_FILE
from mlss_monitor import state
from mlss_monitor.backup import outbox
from mlss_monitor.grow import health_watchdog
from mlss_monitor.grow.auth import hash_secret
from mlss_monitor.grow.photo_storage import (
    _resolve_images_dir,
    clear_thumbnail_cache_for_unit,
)
from mlss_monitor.rbac import require_role
from mlss_monitor.routes.api_grow_ws import _invalidate_auth_cache_for_unit

log = logging.getLogger(__name__)

api_grow_units_bp = Blueprint("api_grow_units", __name__)

_PUSH_TIMEOUT_S = 5

_STALE_AFTER = timedelta(seconds=30)
_OFFLINE_AFTER = timedelta(minutes=5)


def _zone(value, t):
    """Classify a sensor reading into a happiness zone.

    Boundaries (deliberate inclusivity choices):

      value < critical_min            => "critical_low"
      critical_min <= value < ideal_min  => "tolerated_low"
      ideal_min <= value <= ideal_max     => "ideal"   (BOTH ends inclusive)
      ideal_max < value <= critical_max   => "tolerated_high"
      value > critical_max            => "critical_high"

    The inclusive top end on "ideal" matches operator intuition: 27 °C
    is still ideal for a chili-vegetative unit (its ideal_max is 27),
    not the start of tolerated_high. Mirrored on the bottom end so the
    range remains symmetric.

    Returns None when the value is None OR when any of the four
    thresholds is None — both cases mean "no happiness signal here"
    and the API surfaces the dimension with the value omitted so the
    frontend's defensive `?.` chain falls through to the variant-
    based colouring.
    """
    if value is None:
        return None
    if (t["critical_min"] is None or t["ideal_min"] is None
            or t["ideal_max"] is None or t["critical_max"] is None):
        return None
    if value < t["critical_min"]:
        return "critical_low"
    if value < t["ideal_min"]:
        return "tolerated_low"
    if value <= t["ideal_max"]:
        return "ideal"
    if value <= t["critical_max"]:
        return "tolerated_high"
    return "critical_high"


def _fetch_plant_profile_thresholds(conn, plant_type, phase):
    """Look up the soil_temp + soil_moisture thresholds for a unit's
    (plant_type, phase). Falls back to ("generic", phase) when the
    specific plant_type isn't in grow_plant_profiles — handles custom
    plant_type strings the operator typed manually or shipped after
    the SDUI Phase-4 sub-project lands.

    Returns a dict with keys "soil_temp" and "soil_moisture", each
    holding a 4-key threshold dict (critical_min / ideal_min / ideal_max
    / critical_max). Returns None when neither the specific nor the
    generic fallback row exists in the DB (true edge case: a user
    DELETEd the generic seed manually).
    """
    row = conn.execute(
        "SELECT soil_temp_critical_min_c, soil_temp_ideal_min_c, "
        "       soil_temp_ideal_max_c, soil_temp_critical_max_c, "
        "       soil_moisture_critical_min_pct, soil_moisture_ideal_min_pct, "
        "       soil_moisture_ideal_max_pct, soil_moisture_critical_max_pct "
        "FROM grow_plant_profiles WHERE plant_type=? AND phase=?",
        (plant_type, phase),
    ).fetchone()
    if row is None and plant_type != "generic":
        # Specific row missing — fall back to generic for the same
        # phase. We do NOT fall back across phases because dormant
        # tomato is a different beast from vegetative tomato.
        row = conn.execute(
            "SELECT soil_temp_critical_min_c, soil_temp_ideal_min_c, "
            "       soil_temp_ideal_max_c, soil_temp_critical_max_c, "
            "       soil_moisture_critical_min_pct, soil_moisture_ideal_min_pct, "
            "       soil_moisture_ideal_max_pct, soil_moisture_critical_max_pct "
            "FROM grow_plant_profiles WHERE plant_type='generic' AND phase=?",
            (phase,),
        ).fetchone()
    if row is None:
        return None
    return {
        "soil_temp": {
            "critical_min": row["soil_temp_critical_min_c"],
            "ideal_min":    row["soil_temp_ideal_min_c"],
            "ideal_max":    row["soil_temp_ideal_max_c"],
            "critical_max": row["soil_temp_critical_max_c"],
        },
        "soil_moisture": {
            "critical_min": row["soil_moisture_critical_min_pct"],
            "ideal_min":    row["soil_moisture_ideal_min_pct"],
            "ideal_max":    row["soil_moisture_ideal_max_pct"],
            "critical_max": row["soil_moisture_critical_max_pct"],
        },
    }


def _format_ideal_range(ideal_min, ideal_max, unit_suffix):
    """Format an "ideal_min–ideal_max unit" string for the UI subtext.

    Drops trailing .0 on whole-number thresholds so "21 °C" reads
    cleaner than "21.0 °C". The en-dash matches the spec
    ("21–27 °C") rather than a hyphen.
    """
    def _fmt(n):
        if n is None:
            return ""
        # Drop trailing .0 on integer-valued floats (21.0 -> 21).
        return str(int(n)) if float(n).is_integer() else str(n)
    return f"{_fmt(ideal_min)}–{_fmt(ideal_max)} {unit_suffix}"


def _build_happiness(conn, plant_type, phase, last_known_state):
    """Compute the `happiness` response block for one unit.

    Returns {} (empty dict — NOT None) when there's no usable signal
    so the frontend's defensive code stays a single `unit.happiness?.
    soil_temp_c?.zone` chain rather than two layers of nullability.
    The empty-dict path is taken when:
      * last_known_state is None (no telemetry rows yet for the unit), or
      * thresholds are unavailable for (plant_type, phase) AND for
        (generic, phase) — true edge case described above.

    On the happy path, returns a dict keyed by dimension:
      {
        "soil_temp_c": {
          "zone": "ideal" | "tolerated_low" | "tolerated_high"
                  | "critical_low" | "critical_high",
          "ideal_range": "21–27 °C",
          "current": 22.8,
          "thresholds": {critical_min, ideal_min, ideal_max, critical_max}
        },
        "soil_moisture_pct": { ... same shape ... }
      }

    A dimension whose current reading is None (e.g. unit reports
    soil_moisture only — no soil_temp sensor) gets `zone: None`.
    Keeping the dimension key with a null zone (rather than omitting
    it) lets the frontend tell "no sensor" apart from "we forgot to
    wire that dimension".
    """
    if last_known_state is None:
        return {}
    thresholds = _fetch_plant_profile_thresholds(conn, plant_type, phase)
    if thresholds is None:
        return {}

    out = {}
    for state_key, dim_key, unit_suffix in (
        ("soil_temp_c",       "soil_temp",     "°C"),
        ("soil_moisture_pct", "soil_moisture", "%"),
    ):
        current = last_known_state.get(state_key)
        t = thresholds[dim_key]
        out[state_key] = {
            "zone": _zone(current, t),
            "ideal_range": _format_ideal_range(
                t["ideal_min"], t["ideal_max"], unit_suffix,
            ),
            "current": current,
            "thresholds": t,
        }
    return out


# Telemetry columns surfaced in the `last_known_state` block on the GET
# response. Phase 2 schema cleanup replaced a denormalised JSON cache
# with a SELECT against grow_telemetry — the keys here match the
# previous LastKnownState TypedDict + what the frontend reads in
# unit_detail.mjs::CHANNEL_DISPLAY and grow-card.mjs.
_TELEMETRY_STATE_COLUMNS = (
    "soil_moisture_raw", "soil_moisture_pct", "light_state", "pump_state",
    "soil_temp_c", "ambient_lux", "air_temp_c", "air_humidity_pct",
    "reservoir_level_pct",
)


def _classify_status(last_seen_at: str | None) -> str:
    if last_seen_at is None:
        return "offline"
    seen = datetime.fromisoformat(last_seen_at) if isinstance(last_seen_at, str) else last_seen_at
    age = datetime.utcnow() - seen
    if age < _STALE_AFTER:
        return "online"
    if age < _OFFLINE_AFTER:
        return "stale"
    return "offline"


def _last_known_state(conn: sqlite3.Connection, unit_id: int) -> dict | None:
    """Build a `last_known_state` dict for fleet/detail responses.

    Returns None only when the unit has produced NEITHER telemetry nor
    photos — a unit with only-camera-connected (the "first deployment"
    posture) needs `last_known_state` populated so the fleet card can
    surface its latest photo even though no soil-moisture row exists yet.

    Keys (consumed by static/js/grow/components/grow-card.mjs and
    static/js/grow/unit_detail.mjs):
      * soil_moisture_pct/raw, light_state, pump_state, soil_temp_c,
        ambient_lux, air_temp_c, air_humidity_pct, reservoir_level_pct
      * last_pulse_at  — last `grow_watering_events.timestamp_utc`,
                         drives the Live tab's water-lock countdown
      * last_photo_url — URL of the most recent grow_photos row, used as
                         the fleet-card thumbnail. Points at the
                         /photos/<id> endpoint (immutable per id, so the
                         browser caches it indefinitely; URL changes
                         when a fresh photo lands which busts the cache
                         naturally). None when the unit has never been
                         photographed.
    """
    row = conn.execute(
        "SELECT soil_moisture_raw, soil_moisture_pct, light_state, pump_state, "
        "       soil_temp_c, ambient_lux, air_temp_c, air_humidity_pct, "
        "       reservoir_level_pct "
        "FROM grow_telemetry WHERE unit_id=? "
        "ORDER BY timestamp_utc DESC LIMIT 1",
        (unit_id,),
    ).fetchone()
    photo_row = conn.execute(
        "SELECT id FROM grow_photos WHERE unit_id=? "
        "ORDER BY taken_at DESC LIMIT 1",
        (unit_id,),
    ).fetchone()
    # Bug 5: serve the thumbnail variant here rather than asking the
    # client to append `?size=thumb`. The fleet card renders ~340px
    # wide, so the full ~2MB capture is wasted bytes; the ~30KB
    # cached thumbnail at /photos/<id>?size=thumb is the right
    # asset for this consumer. Centralising the responsibility
    # server-side means a future card-photo consumer can't forget
    # the param. The lightbox handoff (clicking the latest-photo
    # tile to open it big) constructs its own non-thumb URL from
    # unit.id + photo id, so this doesn't leak into the
    # "give me the big version" affordance.
    last_photo_url = (
        f"/api/grow/units/{unit_id}/photos/{photo_row['id']}?size=thumb"
        if photo_row else None
    )

    if row is None and last_photo_url is None:
        # No data of any kind for this unit yet — preserves the previous
        # contract (None when there's truly nothing to surface).
        return None

    if row is None:
        # Photo-only path: unit has captured a photo but no telemetry has
        # ever been recorded (e.g. camera wired up before soil sensor).
        # Stub all telemetry fields with None so the fleet card's
        # `last.soil_moisture_pct != null` checks fall through to the
        # "—" placeholder rather than crashing on a missing field.
        state: dict = {col: None for col in _TELEMETRY_STATE_COLUMNS}
    else:
        state = {col: row[col] for col in _TELEMETRY_STATE_COLUMNS}
        # SQLite stores light/pump_state as INTEGER 0/1; surface as bool
        # to match the previous JSON-cache shape the frontend expects.
        state["light_state"] = bool(state["light_state"])
        state["pump_state"] = bool(state["pump_state"])

    pulse_row = conn.execute(
        "SELECT timestamp_utc FROM grow_watering_events "
        "WHERE unit_id=? ORDER BY timestamp_utc DESC LIMIT 1",
        (unit_id,),
    ).fetchone()
    state["last_pulse_at"] = pulse_row["timestamp_utc"] if pulse_row else None
    state["last_photo_url"] = last_photo_url
    return state


@api_grow_units_bp.route("/api/grow/units", methods=["GET"])
def list_units():
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, label, plant_type, medium_type, current_phase, "
        "       sown_at, enrolled_at, last_seen_at, last_buffer_size "
        "FROM grow_units WHERE is_active=1 ORDER BY label"
    ).fetchall()

    units = []
    for r in rows:
        units.append({
            "id": r["id"],
            "label": r["label"],
            "plant_type": r["plant_type"],
            "medium_type": r["medium_type"],
            "current_phase": r["current_phase"],
            "sown_at": r["sown_at"],
            "enrolled_at": r["enrolled_at"],
            "last_seen_at": r["last_seen_at"],
            "last_buffer_size": r["last_buffer_size"],
            "status": _classify_status(r["last_seen_at"]),
            "last_known_state": _last_known_state(conn, r["id"]),
        })
    conn.close()
    return jsonify({"units": units})


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>", methods=["GET"])
def get_unit(unit_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_units WHERE id=? AND is_active=1", (unit_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    caps = conn.execute(
        "SELECT channel, hardware, is_required, unit_label, details_json, "
        "       health, last_seen_at "
        "FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,)
    ).fetchall()
    lw_rows = conn.execute(
        "SELECT phase, start_hh_mm, end_hh_mm "
        "FROM grow_light_windows WHERE unit_id=? ORDER BY phase, sort_order",
        (unit_id,),
    ).fetchall()
    last_known_state = _last_known_state(conn, unit_id)
    # Plant-happiness lookup: one extra SELECT against grow_plant_profiles
    # per request, keyed on the unit's current (plant_type, phase). Cheap
    # — the table has ~35 rows and (plant_type, phase) is UNIQUE so the
    # lookup hits a covering index. The block is purely additive on the
    # response; consumers that don't know about `happiness` are
    # unaffected. See _zone() / _build_happiness() for the classification
    # algorithm + fallback chain (specific → generic → empty dict).
    happiness = _build_happiness(
        conn, row["plant_type"], row["current_phase"], last_known_state,
    )
    conn.close()

    body = {k: row[k] for k in row.keys()}
    body.pop("bearer_token_hash", None)  # never expose
    body["status"] = _classify_status(row["last_seen_at"])
    body["last_known_state"] = last_known_state
    body["happiness"] = happiness
    body["capabilities"] = []
    for c in caps:
        details = json.loads(c["details_json"]) if c["details_json"] else None
        # Phase 2 sense-only-mode: the persisted `health` column drives UI
        # degradation. Lazy watchdog overlay: if a recent command went
        # unanswered, report unresponsive in this response only. The
        # persisted health stays alone — the next confirming
        # telemetry/event will promote it back via
        # _promote_capability_health.
        health = c["health"] or "untested"
        if c["channel"] in ("pump", "light"):
            if health_watchdog.check_unresponsive(unit_id, c["channel"]):
                health = "unresponsive"
        body["capabilities"].append({
            "channel": c["channel"],
            "hardware": c["hardware"],
            "is_required": bool(c["is_required"]),
            "unit_label": c["unit_label"],
            "details": details or None,
            "health": health,
            "last_seen_at": c["last_seen_at"],
        })
    # Configure-tab Task 5: surface PID/profile overrides + soil calibration +
    # light_windows so the frontend can render current values + "(default)" vs
    # "(custom)" indicators without a separate fetch. Field names strip the
    # `_override` suffix and `soil_` prefix for cleaner client-side access.
    body["overrides"] = {
        "watering_target": row["watering_target_override"],
        "kp": row["watering_kp_override"],
        "ki": row["watering_ki_override"],
        "kd": row["watering_kd_override"],
        "soak_window_min": row["soak_window_min_override"],
        "min_pulse_s": row["pulse_min_s_override"],
        "max_pulse_s": row["pulse_max_s_override"],
    }
    body["calibration"] = {
        "dry_raw": row["soil_dry_raw"],
        "wet_raw": row["soil_wet_raw"],
    }
    # Photo capture schedule. Same shape as PhotoScheduleUpdate so the UI
    # can round-trip the value into a PUT without translation. Both null
    # ⇒ capture 24/7 (new default); both set ⇒ capture between hours.
    body["photo_schedule"] = {
        "start_hour": row["photo_active_start_hour"],
        "end_hour":   row["photo_active_end_hour"],
    }
    light_windows: dict[str, list] = {}
    for r in lw_rows:
        light_windows.setdefault(r["phase"], []).append(
            {"start": r["start_hh_mm"], "end": r["end_hh_mm"]}
        )
    body["light_windows"] = light_windows
    # Phase 9 Task 9.3 — surface the smart_plugs scoped to this unit so
    # the Configure tab's "Effectors for this unit" section can render
    # without a second fetch. Lean payload (id, label, effector_type,
    # current_state, auto_mode) matches the plan's per-row brief — the
    # full plug data is still available via GET /api/effectors/<id>
    # when the operator opens the Phase 8 side panel.
    body["effectors"] = _list_effectors_for_unit(unit_id)
    return jsonify(body)


def _list_effectors_for_unit(unit_id: int) -> list[dict]:
    """Return the lean per-effector payload for one grow unit.

    Wraps a short-lived sqlite3.Row read so the Configure tab gets one
    fetch instead of two. Returns ``[]`` when the unit has no scoped
    plugs — the frontend's "+ Add effector" button renders against the
    empty list, so the shape must be present even with zero rows.
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        # ``smart_plugs`` was created by Phase 1 schema migration; on a
        # very old DB that pre-dates the topology feature the table may
        # not exist. Tolerate that by returning [] so the rest of the
        # response still renders.
        try:
            rows = conn.execute(
                "SELECT id, label, effector_type, current_state, auto_mode "
                "FROM smart_plugs WHERE grow_unit_id=? "
                "ORDER BY id",
                (unit_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [
        {
            "id":            r["id"],
            "label":         r["label"],
            "effector_type": r["effector_type"],
            "current_state": r["current_state"],
            # Boolean-ify the SQLite INTEGER so JSON callers can do
            # `if (eff.auto_mode)` without a parseInt — matches the
            # treatment for other 0/1 columns on this endpoint.
            "auto_mode":     bool(r["auto_mode"]),
        }
        for r in rows
    ]


def _push_command_blocking(unit_id: int, command: dict) -> tuple[int, dict]:
    """Send a command to a unit via the WS registry.

    Schedules `registry.send_to_unit` on the listener thread's event loop
    (where the WebSocket connection objects live) using
    `asyncio.run_coroutine_threadsafe`. Blocks the Flask request thread up
    to _PUSH_TIMEOUT_S seconds for the send to complete.

    Returns (status, body):
      - (503, {"error": "unit_not_connected"}) if registry empty / unit
        disconnected before-or-during the send (KeyError race)
      - (503, {"error": "send_failed"})        if the underlying ws.send
        raised (e.g. ConnectionClosed) — peer dropped between lookup and send
      - (504, {"error": "send_timeout"})       if the listener didn't
        complete the send within _PUSH_TIMEOUT_S
      - (202, {"queued": True})                on successful send
    """
    registry = state.grow_ws_registry
    listener_loop = state.grow_ws_loop
    if registry is None or listener_loop is None or not registry.is_connected(unit_id):
        return 503, {"error": "unit_not_connected"}
    msg = json.dumps({
        "type": "command",
        "ts": datetime.utcnow().isoformat() + "Z",
        "payload": command,
    })
    try:
        future = asyncio.run_coroutine_threadsafe(
            registry.send_to_unit(unit_id, msg), listener_loop
        )
        future.result(timeout=_PUSH_TIMEOUT_S)
    except KeyError:
        return 503, {"error": "unit_not_connected"}
    except concurrent.futures.TimeoutError:
        return 504, {"error": "send_timeout"}
    except Exception:
        # ws.send may raise ConnectionClosed or other transport errors if the
        # peer dropped between is_connected() and send. Treat as 503.
        return 503, {"error": "send_failed"}
    return 202, {"queued": True}


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/identify", methods=["POST"])
@require_role("controller", "admin")
def identify(unit_id):
    status, body = _push_command_blocking(unit_id, {
        "name": "identify",
        "args": {"duration_s": 10},
    })
    return jsonify(body), status


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/water-now", methods=["POST"])
@require_role("controller", "admin")
def water_now(unit_id):
    body_in = request.get_json(silent=True) or {}
    duration_s = max(1, min(30, int(body_in.get("duration_s", 5))))  # safety cap
    status, body = _push_command_blocking(unit_id, {
        "name": "water_now",
        "args": {"duration_s": duration_s},
    })
    # Watchdog: only record AFTER a successful 202 from the registry.
    # Recording on 503/504 would mean we mark "unresponsive" for commands
    # the unit never received in the first place — the user needs to know
    # the unit is offline (which 503 already conveys), not that the pump
    # itself is broken.
    if status == 202:
        health_watchdog.record_command_sent(unit_id, "pump")
    return jsonify(body), status


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/snap-photo",
                         methods=["POST"])
@require_role("controller", "admin")
def snap_photo(unit_id):
    """Push a snap_photo command to the unit. The firmware's dispatcher
    handles `name == "snap_photo"` (see grow_unit/src/mlss_grow/dispatch.py)
    by capturing a JPEG via picamera2 and sending it back as a binary WS
    frame. The photo lands in grow_photos a few seconds later (via
    handle_photo_frame on the WS receive side)."""
    status, body = _push_command_blocking(unit_id, {
        "name": "snap_photo",
        "args": {},
    })
    return jsonify(body), status


@api_grow_units_bp.route("/api/grow/units/<int:unit_id>/light-toggle",
                         methods=["POST"])
@require_role("controller", "admin")
def light_toggle(unit_id):
    """Flip the grow light's current state.

    Reads the unit's last-known light_state from grow_telemetry and
    pushes a `light_override` command with the inverse. The firmware's
    `_handle_light_override` (grow_unit/src/mlss_grow/dispatch.py) routes
    state="on" → force_light_on for `duration_min` minutes, state="off"
    → immediate off (no duration). One-hour override is a sensible
    operator default; the regular light schedule resumes on the next
    safety-loop tick whose schedule asks for on (when off) or after the
    duration elapses (when on).

    Defaults to "on" if no telemetry has arrived yet — clicking Toggle
    on a fresh unit should make SOMETHING happen visibly. Same default
    applies if the grow_telemetry table is missing entirely (fresh DB
    in CI before any telemetry ingest has migrated the schema) — we'd
    rather push an "on" command than 500 the operator.
    """
    row = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        try:
            row = conn.execute(
                "SELECT light_state FROM grow_telemetry "
                "WHERE unit_id=? ORDER BY timestamp_utc DESC LIMIT 1",
                (unit_id,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        # Missing table / locked DB on a fresh deploy — treat as
        # "no telemetry yet" so the toggle still pushes an "on" cmd.
        log.warning("light_toggle: telemetry read failed (%s); defaulting OFF→ON", exc)
    current_on = bool(row[0]) if row else False
    new_state = "off" if current_on else "on"
    status, body = _push_command_blocking(unit_id, {
        "name": "light_override",
        "args": {"state": new_state, "duration_min": 60},  # 1h override
    })
    if status == 202:
        health_watchdog.record_command_sent(unit_id, "light")
    return jsonify(body), status


# ---------------------------------------------------------------------------
# Per-unit bearer-token rotation (Phase 1 spec §5)
# ---------------------------------------------------------------------------
#
# When the operator suspects a unit's bearer token has leaked (or just on
# scheduled rotation), POST /api/grow/units/<id>/rotate-token mints a fresh
# token, replaces the argon2 hash on grow_units, stashes the raw value for
# one-shot reveal, and invalidates any cached bearer-verifications for the
# unit (otherwise the old token could survive its 60s TTL after rotation,
# defeating "immediate" invalidation).
#
# The response body's `token` field gives the operator the new raw value
# directly, so they can copy it onto /etc/mlss-grow/token.json on the Pi
# without a second round-trip. The peek-once GET mirrors the
# enrollment-key reveal flow for UI consistency — the operator can hit
# rotate, navigate away, come back, and still pick up the token once.


def _stash_token_key(unit_id: int) -> str:
    """app_settings key under which a freshly-rotated raw token is stashed
    for one-shot reveal. Keyed per-unit so rotating unit 1 doesn't blow
    away a still-pending reveal for unit 2."""
    return f"grow_unit_{unit_id}_token_pending_reveal"


@api_grow_units_bp.route(
    "/api/grow/units/<int:unit_id>/rotate-token", methods=["POST"]
)
@require_role("admin")
def rotate_unit_token(unit_id):
    """Mint a fresh bearer token for one unit and replace the stored hash.

    Atomic per-unit: the new hash, the raw stash, and the cache eviction
    all happen before the response returns, so a unit holding the old
    token cannot succeed at bearer-auth on its next reconnect (the stale
    cache entry is gone; the new hash won't match the old raw).

    Returns the raw token in the response body — admins copy it onto
    /etc/mlss-grow/token.json on the Pi. The same value is also stashed
    for one peek via GET /api/grow/units/<id>/token/peek-once, so the
    "click rotate → navigate to reveal panel" UX matches the enrollment
    key flow.

    404 if the unit doesn't exist (or is_active=0). The cache eviction
    runs only after a successful UPDATE — a missing-unit POST must not
    silently nuke a peer unit's cache through a typo.
    """
    raw_token = secrets.token_urlsafe(32)
    new_hash = hash_secret(raw_token)

    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE grow_units SET bearer_token_hash=? "
                "WHERE id=? AND is_active=1",
                (new_hash, unit_id),
            )
            if cur.rowcount == 0:
                # Return BEFORE enqueueing — a missing-unit POST mustn't
                # leave a phantom outbox pointer behind.
                return jsonify({"error": "unit_not_found"}), 404
            outbox.enqueue_row(conn, table="grow_units", pk=unit_id)
            # The token stash on app_settings is local-only (not replicated)
            # so no outbox enqueue here — it just rides the same transaction
            # to stay atomic with the hash rotation.
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                (_stash_token_key(unit_id), raw_token),
            )

    # Drop any cached (unit_id, old_token) entries so the previous token
    # can't survive its 60s TTL after rotation. Other units' entries are
    # untouched.
    _invalidate_auth_cache_for_unit(unit_id)

    return jsonify({"token": raw_token}), 201


# ---------------------------------------------------------------------------
# Phase 3 Task 4 — Diagnostics tab "Danger Zone" actions
# ---------------------------------------------------------------------------
#
# Two admin-only endpoints sit alongside the rotate-token mint above:
#
#   * DELETE /api/grow/units/<id>          — soft-delete (is_active=0). Telemetry
#                                            history + grow_photos are preserved
#                                            so we don't lose audit data, but the
#                                            unit drops out of the fleet view and
#                                            the WS bearer-validate now refuses
#                                            its connection.
#   * POST /api/grow/units/<id>/clear-buffer — synchronous WS push of a
#                                            {"name": "clear_buffer"} command.
#                                            Mirrors safety_override semantics
#                                            (202 on confirmed delivery, 503 if
#                                            disconnected). No audit row needed
#                                            — clear-buffer doesn't drive a
#                                            physical actuator, so the safety-
#                                            override audit-trail rationale
#                                            doesn't apply.


@api_grow_units_bp.route(
    "/api/grow/units/<int:unit_id>", methods=["DELETE"]
)
@require_role("admin")
def delete_unit(unit_id):
    """Soft-delete a grow unit.

    Sets `is_active=0` rather than DELETEing the row so telemetry history
    and grow_photos remain intact for audit / forensic purposes. The fleet
    list endpoint already filters on `WHERE is_active=1`, so the unit
    disappears from the UI immediately. A future operator can revive a
    soft-deleted unit only via a manual DB UPDATE — there's no in-product
    "undecommission" flow because the friction is intentional (the
    Diagnostics tab confirm-modal asks the operator to type the unit's
    label before firing the DELETE).

    The WS bearer-auth check (api_grow_ws._validate_bearer) verifies
    `is_active=1`, so a unit holding the old token can't reconnect after
    the soft-delete lands.

    Returns:
        200 with {"ok": True} on success
        404 if no active unit with that id (already soft-deleted, or never
            existed)
    """
    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE grow_units SET is_active=0 WHERE id=? AND is_active=1",
                (unit_id,),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "unit_not_found"}), 404
            outbox.enqueue_row(conn, table="grow_units", pk=unit_id)
    return jsonify({"ok": True})


@api_grow_units_bp.route(
    "/api/grow/units/<int:unit_id>/clear-buffer", methods=["POST"]
)
@require_role("admin")
def clear_buffer(unit_id):
    """Push a clear_buffer command to the unit synchronously.

    Mirrors safety_override semantics: synchronous WS push that returns
    202 on confirmed delivery or 503 if the unit is disconnected. The
    payload uses the legacy `name`-keyed shape (same as identify /
    water_now) since clear_buffer is firmware-side state-only — it
    doesn't share the safety-override audit-trail concern.

    No audit row written: clearing a remote buffer doesn't drive a
    physical actuator, so the safety-override "every fire leaves a
    trail" rationale doesn't apply here. The WS push itself is logged
    by the registry, which is enough for ops forensics.
    """
    status, body = _push_command_blocking(unit_id, {"name": "clear_buffer"})
    return jsonify(body), status


@api_grow_units_bp.route(
    "/api/grow/units/<int:unit_id>/photos", methods=["DELETE"]
)
@require_role("admin")
def clear_photos(unit_id):
    """Delete every photo (DB rows + JPEG files on disk) for one unit.

    Use case: wiping the test-data slate before going live with a real
    plant. Surfaced in the Diagnostics tab Danger Zone next to
    decommission + clear-buffer.

    Resilient to partial state:
      * DB rows whose JPEG file is already missing on disk are still
        deleted from the table — the row reflects the absence-of-file,
        not the cause.
      * If the JPEG unlink raises (permission denied, disk error), we
        log + continue. The DB row still gets deleted; an orphaned JPEG
        is the lesser evil vs a partial wipe that leaves stale rows
        pointing at deleted files (the latter looks "broken" in the
        timelapse with 404 scrubbing slots).
      * Empty unit (zero photos) returns 200 + {"deleted_count": 0}.

    Returns:
        200 with {"deleted_count": N} on success
        404 if no active unit with that id (matches the rest of the
            danger-zone endpoints)
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        # Verify the unit exists + is active (matches DELETE/<id> contract).
        # Refusing a wipe on a soft-deleted unit avoids confusing the audit
        # trail — the unit is gone from the UI; if you want its photos
        # cleaned up you'd do that as part of decommission, not after.
        unit_row = conn.execute(
            "SELECT id FROM grow_units WHERE id=? AND is_active=1", (unit_id,)
        ).fetchone()
        if unit_row is None:
            return jsonify({"error": "unit_not_found"}), 404

        # Snapshot file paths BEFORE the DELETE so we have something to
        # unlink even if the transaction commits mid-way through file
        # cleanup. (We could do file-then-row instead, but then a failed
        # row-delete would leave us with rows pointing at unlinked files,
        # which the timelapse renders as broken slots.)
        rows = conn.execute(
            "SELECT file_path FROM grow_photos WHERE unit_id=?", (unit_id,)
        ).fetchall()
        file_paths = [r[0] for r in rows]

        cur = conn.execute(
            "DELETE FROM grow_photos WHERE unit_id=?", (unit_id,)
        )
        deleted_count = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    # Files: best-effort. We've already committed the DB delete, so an
    # unlink failure is logged but doesn't fail the request — the
    # alternative (rolling back the DB on a single bad unlink) would
    # leave the operator unable to recover from a half-orphaned state.
    images_dir = _resolve_images_dir()
    unlinked = 0
    for rel_path in file_paths:
        abs_path = os.path.join(images_dir, rel_path)
        try:
            os.unlink(abs_path)
            unlinked += 1
        except FileNotFoundError:
            # Already gone — count as success since the desired end-state
            # (file absent) holds.
            unlinked += 1
        except OSError as exc:
            log.warning(
                "clear_photos: failed to unlink %s for unit %d: %s",
                abs_path, unit_id, exc,
            )

    # Wipe the thumbnail cache for this unit so the next ?size=thumb
    # request doesn't serve a stale derivative of a deleted source.
    # Best-effort — see clear_thumbnail_cache_for_unit() docstring for
    # the partial-failure rationale.
    clear_thumbnail_cache_for_unit(unit_id)

    log.info(
        "clear_photos: unit=%d deleted_rows=%d files_unlinked=%d files_attempted=%d",
        unit_id, deleted_count, unlinked, len(file_paths),
    )
    return jsonify({"deleted_count": deleted_count})


@api_grow_units_bp.route(
    "/api/grow/units/<int:unit_id>/token/peek-once", methods=["GET"]
)
@require_role("admin")
def peek_unit_token(unit_id):
    """Return the freshly-rotated raw bearer token once, then delete the stash.

    Mirror of api_grow_dist.peek_enrollment_key. Admin-only — exposing the
    raw token to anyone but admin would let them pose as the unit on the
    WS endpoint. 410 Gone if no rotation is pending reveal (or it was
    already consumed by an earlier peek).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (_stash_token_key(unit_id),),
        ).fetchone()
        if row is None or not row[0]:
            return jsonify({"error": "already_revealed"}), 410
        raw_token = row[0]
        conn.execute(
            "DELETE FROM app_settings WHERE key=?",
            (_stash_token_key(unit_id),),
        )
        conn.commit()
        return jsonify({"token": raw_token})
    finally:
        conn.close()

"""Grow settings management endpoints (admin-only and read-only-everyone).

Three endpoint families on this blueprint:

* POST /api/grow/enrollment-key/rotate         (admin)
    Generate a fresh enrollment key, replace the existing argon2 hash,
    stash the raw key for one-time reveal via the existing peek-once
    flow. Existing enrolled units carry per-unit bearer tokens and are
    unaffected by rotation; only future enrollment attempts are gated
    by the new key.

* GET  /api/grow/plant-profiles                 (controller+admin)
* PUT  /api/grow/plant-profiles/<id>            (admin)
    Read all seeded + custom plant profiles, edit any one's tunables.
    Admin-only for write so a curious controller can't shift the PID
    defaults under everyone, but read is open to controllers because
    they routinely need to know what the per-unit overrides are
    overriding.

* GET  /api/grow/settings/holiday-mode          (viewer+controller+admin)
* PUT  /api/grow/settings/holiday-mode          (admin)
    Toggle the household-wide holiday mode flag. When ON, firmware skips
    pump pulses but keeps light schedule + telemetry running. The PUT
    deliberately does NOT broadcast a config_changed push for v1 —
    units pick up the new flag on their next reconnect-pull (the
    offline-reconnect-pull behaviour from the Configure plan). Documented
    as a v1 simplification; can be promoted to a live broadcast if
    operators report that lag is unacceptable.
"""
import secrets
import sqlite3
from contextlib import closing
from typing import Optional

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, ValidationError

from database.init_db import DB_FILE
from mlss_contracts._validators import make_min_le_max_validator
from mlss_monitor.backup import outbox
from mlss_monitor.grow.api_helpers import serialise_validation_errors
from mlss_monitor.grow.auth import hash_secret
from mlss_monitor.rbac import require_role

api_grow_settings_bp = Blueprint("api_grow_settings", __name__)


# ---------------------------------------------------------------------------
# 2a. Enrollment key rotation
# ---------------------------------------------------------------------------


@api_grow_settings_bp.route(
    "/api/grow/enrollment-key/rotate", methods=["POST"]
)
@require_role("admin")
def rotate_enrollment_key():
    """Generate a fresh enrollment key, replace the hash, stash for one-time reveal.

    The previous raw-pending-reveal stash (if any) is overwritten — a
    rotation always supersedes any pending peek. The argon2 hash is the
    only persistent verification material; the raw value is gone after
    a single peek-once GET (or the next rotation).
    """
    raw_key = secrets.token_urlsafe(32)
    new_hash = hash_secret(raw_key)

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_hash", new_hash),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_enrollment_key_raw_pending_reveal", raw_key),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"key": raw_key}), 201


# ---------------------------------------------------------------------------
# 2b. Plant profiles editor
# ---------------------------------------------------------------------------


class _ProfileUpdate(BaseModel):
    """Validation shape for PUT /api/grow/plant-profiles/<id>.

    All fields optional — the editor sends only the fields the user
    changed. Cross-field rule: min_pulse_s <= max_pulse_s when both are
    present in the same payload (else the resulting unit profile would
    be unschedulable).
    """
    target_moisture_pct: Optional[float] = Field(None, ge=0, le=100)
    deadband_pct: Optional[float] = Field(None, ge=0, le=20)
    kp: Optional[float] = Field(None, ge=0, le=10)
    ki: Optional[float] = Field(None, ge=0, le=10)
    kd: Optional[float] = Field(None, ge=0, le=10)
    min_pulse_s: Optional[float] = Field(None, ge=0, le=60)
    max_pulse_s: Optional[float] = Field(None, ge=0, le=60)
    soak_window_min: Optional[int] = Field(None, ge=0, le=240)
    default_light_hours: Optional[float] = Field(None, ge=0, le=24)
    notes: Optional[str] = Field(None, max_length=500)

    _min_le_max = make_min_le_max_validator("min_pulse_s", "max_pulse_s")


@api_grow_settings_bp.route("/api/grow/plant-profiles", methods=["GET"])
@require_role("controller", "admin")
def list_plant_profiles():
    """Return every plant_profile row (shipped + custom) for the editor.

    Open to controllers + admins so a controller diagnosing a unit can see
    what the per-unit override is overriding. Write side is admin-only
    (see update_plant_profile).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, plant_type, phase, target_moisture_pct, deadband_pct, "
            "kp, ki, kd, min_pulse_s, max_pulse_s, soak_window_min, "
            "default_light_hours, is_shipped, notes "
            "FROM grow_plant_profiles ORDER BY plant_type, phase"
        ).fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


@api_grow_settings_bp.route(
    "/api/grow/plant-profiles/<int:profile_id>", methods=["PUT"]
)
@require_role("admin")
def update_plant_profile(profile_id):
    """Update a plant profile (shipped or custom). Admin-only.

    Empty body → 200 no-op (idempotent retry). Unknown id → 404.
    Note: shipped rows ARE editable — the `is_shipped` flag is a UI
    breadcrumb ("modified from default" badge) not a write lock.
    """
    body = request.get_json(silent=True) or {}
    try:
        payload = _ProfileUpdate(**body)
    except ValidationError as exc:
        return jsonify({
            "error": "invalid_payload",
            "detail": serialise_validation_errors(exc.errors()),
        }), 400

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return jsonify({"ok": True})

    set_clauses = [f"{k}=?" for k in fields]
    values = list(fields.values()) + [profile_id]
    sql = (
        f"UPDATE grow_plant_profiles SET {', '.join(set_clauses)} WHERE id=?"
    )

    with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
        with conn:
            cur = conn.execute(sql, values)
            if cur.rowcount == 0:
                return jsonify({"error": "profile_not_found"}), 404
            outbox.enqueue_row(
                conn, table="grow_plant_profiles", pk=profile_id,
            )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# 2c. Holiday mode toggle
# ---------------------------------------------------------------------------


@api_grow_settings_bp.route(
    "/api/grow/settings/holiday-mode", methods=["GET"]
)
@require_role("viewer", "controller", "admin")
def get_holiday_mode():
    """Read the household-wide holiday mode flag.

    Stored in app_settings as a string "0"/"1"; absence means OFF.
    Open to viewers because the flag affects what they see in the UI
    (the toggle's current state should match across all logged-in users).
    """
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='grow_holiday_mode'"
        ).fetchone()
    finally:
        conn.close()
    enabled = row is not None and row[0] == "1"
    return jsonify({"enabled": enabled})


@api_grow_settings_bp.route(
    "/api/grow/settings/holiday-mode", methods=["PUT"]
)
@require_role("admin")
def set_holiday_mode():
    """Set the household-wide holiday mode flag. Admin-only.

    v1 deliberately does NOT broadcast a config_changed push — units pick
    the flag up on their next reconnect-pull (the offline-reconnect-pull
    behaviour from the Configure plan). This keeps the broadcast logic
    simple; if operators report the lag is unacceptable, promote to a
    real fan-out push iterating registry connections.
    """
    body = request.get_json(silent=True) or {}
    if "enabled" not in body or not isinstance(body["enabled"], bool):
        return jsonify({"error": "missing_or_invalid_enabled"}), 400

    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            ("grow_holiday_mode", "1" if body["enabled"] else "0"),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "enabled": body["enabled"]})

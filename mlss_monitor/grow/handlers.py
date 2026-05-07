"""Per-message-type handlers for the grow WebSocket listener.

Each handler is a pure function over (unit_id, ts, payload) — easy to unit
test without spinning up a real WebSocket. The WS listener (Task 4.6)
dispatches incoming text frames to these by message type.
"""
import json
import sqlite3
from datetime import datetime
from typing import Optional

from database.init_db import DB_FILE


def _compute_moisture_pct(raw: int, dry: Optional[int], wet: Optional[int]) -> Optional[float]:
    """Linear-map raw → %. Returns None if calibration not present."""
    if dry is None or wet is None or wet <= dry:
        return None
    pct = (raw - dry) / (wet - dry) * 100
    return max(0.0, min(100.0, round(pct, 2)))


def handle_telemetry(unit_id: int, ts: datetime, payload: dict) -> int:
    """Insert one grow_telemetry row + refresh grow_units.last_seen_at.

    If payload['soil_moisture_pct'] is missing but the unit has calibration
    set, computes pct server-side from the raw reading.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic — this function trusts `light_state`/`pump_state`
    to be coercible to int and `soil_moisture_raw` to be present.

    Returns the inserted grow_telemetry.id (used by photo upload to backfill
    the telemetry_id join key).

    Phase 2 schema cleanup: the previous implementation rewrote a
    denormalised JSON blob (last_known_state_json) on every frame so the
    fleet-view API could read state without a join. That cache has been
    replaced with a SELECT against grow_telemetry ORDER BY timestamp_utc
    DESC LIMIT 1 (already indexed) — eliminating a hot write path.
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Server-side pct fill if missing
        pct = payload.get("soil_moisture_pct")
        if pct is None and "soil_moisture_raw" in payload:
            cal = conn.execute(
                "SELECT soil_dry_raw, soil_wet_raw FROM grow_units WHERE id=?",
                (unit_id,),
            ).fetchone()
            if cal:
                pct = _compute_moisture_pct(payload["soil_moisture_raw"],
                                             cal["soil_dry_raw"], cal["soil_wet_raw"])

        cur = conn.execute(
            "INSERT INTO grow_telemetry "
            "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
            " light_state, pump_state, soil_temp_c, ambient_lux, "
            " air_temp_c, air_humidity_pct, reservoir_level_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (unit_id, ts, payload["soil_moisture_raw"], pct,
             int(payload["light_state"]), int(payload["pump_state"]),
             payload.get("soil_temp_c"), payload.get("ambient_lux"),
             payload.get("air_temp_c"), payload.get("air_humidity_pct"),
             payload.get("reservoir_level_pct")),
        )
        inserted_id = cur.lastrowid

        # NOTE: both timestamps bound to payload `ts`. Once heartbeats land
        # (post-Phase 1), last_seen_at should be set from server clock so the
        # fleet view can show "online recently" independently of telemetry cadence.
        conn.execute(
            "UPDATE grow_units SET last_telemetry_at=?, last_seen_at=? WHERE id=?",
            (ts, ts, unit_id),
        )

        # Phase 2 sense-only-mode: promote actuator capabilities to
        # "connected" when their state is non-zero. Why only on state=1?
        # Because pump_state=0 / light_state=0 is the normal idle state,
        # not evidence of disconnection. The watchdog handles regression.
        if int(payload["pump_state"]):
            _promote_capability_health(conn, unit_id, "pump", "connected", ts)
        if int(payload["light_state"]):
            _promote_capability_health(conn, unit_id, "light", "connected", ts)

        conn.commit()
        return inserted_id
    finally:
        conn.close()


def handle_capabilities(unit_id: int, ts: datetime, payload: dict) -> None:
    """Replace the unit's full capability set with what was just declared.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic. Idempotent: a re-pushed identical payload simply
    rewrites the same rows. A removed sensor disappears on next push.

    Phase 2 schema cleanup: `health` is now a typed column (TEXT NOT NULL
    DEFAULT 'untested', CHECK enum on fresh schemas, indexed on
    (unit_id, health)). Previously stored as details_json.health which
    forced read-modify-write and prevented cross-fleet querying.
    `details_json` retains driver-specific heterogeneous metadata only
    (e.g. {"i2c_address": "0x36"}).
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute("DELETE FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,))
        for cap in payload["capabilities"]:
            details = cap.get("details") or None
            details_json = json.dumps(details) if details else None
            conn.execute(
                "INSERT INTO grow_unit_capabilities "
                "(unit_id, channel, hardware, is_required, unit_label, "
                " installed_at, details_json, health, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (unit_id, cap["channel"], cap.get("hardware"),
                 int(cap["is_required"]), cap.get("unit_label"), ts,
                 details_json, cap.get("health", "untested"), ts),
            )
        conn.execute(
            "UPDATE grow_units SET last_seen_at=? WHERE id=?",
            (ts, unit_id),
        )
        conn.commit()
    finally:
        conn.close()


def _promote_capability_health(conn: sqlite3.Connection, unit_id: int,
                               channel: str, new_health: str,
                               last_seen_at: Optional[datetime] = None) -> None:
    """Update grow_unit_capabilities.health in-place.

    Promotes a capability to a stronger health state when telemetry or an
    event proves the actuator is responding. A single column UPDATE —
    no JSON read-modify-write.

    Idempotent: if the capability already has the target health, this
    is a no-op write. Silently no-ops if the row doesn't exist (the
    telemetry handler shouldn't crash on a unit whose capabilities
    haven't yet been declared — that's a transient ordering quirk).
    """
    row = conn.execute(
        "SELECT health FROM grow_unit_capabilities "
        "WHERE unit_id=? AND channel=?",
        (unit_id, channel),
    ).fetchone()
    if row is None:
        return
    if row[0] == new_health:
        return
    conn.execute(
        "UPDATE grow_unit_capabilities SET health=?, last_seen_at=? "
        "WHERE unit_id=? AND channel=?",
        (new_health, last_seen_at or datetime.utcnow(), unit_id, channel),
    )


def handle_event(unit_id: int, ts: datetime, payload: dict) -> None:
    """Dispatch by event kind.

    Watering events → grow_watering_events; sensor_*/safety_* and other
    diagnostic events → grow_errors. sensor_recovered closes any matching
    open sensor_degraded row for the same sensor. Unknown kinds are
    log-only (no DB row in Phase 1).

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic.
    """
    kind = payload["kind"]
    details = payload.get("details") or {}
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        if kind == "watering_pulse":
            conn.execute(
                "INSERT INTO grow_watering_events "
                "(unit_id, timestamp_utc, trigger, duration_s, soil_pct_before, "
                " triggered_by, pid_error, pid_p_term, pid_i_term, pid_d_term) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (unit_id, ts, details.get("trigger", "pid"),
                 details["duration_s"], details.get("soil_pct_before"),
                 details.get("triggered_by", "system"), details.get("pid_error"),
                 details.get("pid_p_term"), details.get("pid_i_term"),
                 details.get("pid_d_term")),
            )
            # Phase 2 sense-only-mode: a completed watering pulse is the
            # strongest evidence the pump works. Promote regardless of
            # current health so a previously-"unresponsive" capability
            # snaps back to "connected" the moment evidence arrives.
            _promote_capability_health(conn, unit_id, "pump", "connected", ts)
        elif kind == "sensor_degraded":
            sensor = details.get("sensor", "unknown")
            conn.execute(
                "INSERT INTO grow_errors "
                "(unit_id, timestamp_utc, severity, kind, message, details_json, "
                " subject_sensor) "
                "VALUES (?, ?, 'warning', 'sensor_degraded', ?, ?, ?)",
                (unit_id, ts, f"Sensor {sensor} reporting bad reads",
                 json.dumps(details), sensor),
            )
        elif kind == "sensor_recovered":
            sensor = details.get("sensor", "unknown")
            conn.execute(
                "UPDATE grow_errors SET resolved_at=? "
                "WHERE unit_id=? AND kind='sensor_degraded' AND resolved_at IS NULL "
                "AND subject_sensor=?",
                (ts, unit_id, sensor),
            )
        elif kind == "safety_cap_hit":
            conn.execute(
                "INSERT INTO grow_errors "
                "(unit_id, timestamp_utc, severity, kind, message, details_json) "
                "VALUES (?, ?, 'warning', 'safety_cap_hit', ?, ?)",
                (unit_id, ts, f"Safety cap hit: {details.get('cap', '')}",
                 json.dumps(details)),
            )
        # Other event kinds (startup, shutdown, identify_complete, etc.) are
        # logged-only — no DB row needed in Phase 1.
        conn.execute(
            "UPDATE grow_units SET last_seen_at=? WHERE id=?", (ts, unit_id),
        )
        conn.commit()
    finally:
        conn.close()

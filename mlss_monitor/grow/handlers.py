"""Per-message-type handlers for the grow WebSocket listener.

Each handler is a pure function over (unit_id, ts, payload) — easy to unit
test without spinning up a real WebSocket. The WS listener (Task 4.6)
dispatches incoming text frames to these by message type.
"""
import json
import sqlite3
from datetime import datetime
from typing import Optional, TypedDict

from database.init_db import DB_FILE


class LastKnownState(TypedDict):
    """Schema of the JSON blob written to grow_units.last_known_state_json.

    Consumed by the fleet-view API (api_grow_units.py) to render unit cards
    without joining grow_telemetry. Any field added/removed here must be
    coordinated with the consumer.
    """
    soil_moisture_raw: int
    soil_moisture_pct: Optional[float]
    light_state: bool
    pump_state: bool
    soil_temp_c: Optional[float]
    ambient_lux: Optional[float]
    air_temp_c: Optional[float]
    air_humidity_pct: Optional[float]
    reservoir_level_pct: Optional[float]


def _compute_moisture_pct(raw: int, dry: Optional[int], wet: Optional[int]) -> Optional[float]:
    """Linear-map raw → %. Returns None if calibration not present."""
    if dry is None or wet is None or wet <= dry:
        return None
    pct = (raw - dry) / (wet - dry) * 100
    return max(0.0, min(100.0, round(pct, 2)))


def handle_telemetry(unit_id: int, ts: datetime, payload: dict) -> int:
    """Insert one grow_telemetry row + refresh grow_units.last_known_state_json.

    If payload['soil_moisture_pct'] is missing but the unit has calibration
    set, computes pct server-side from the raw reading.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic — this function trusts `light_state`/`pump_state`
    to be coercible to int and `soil_moisture_raw` to be present.

    Returns the inserted grow_telemetry.id (used by photo upload to backfill
    the telemetry_id join key).
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

        # Update unit's cached last_known_state for fleet rendering
        state: LastKnownState = {
            "soil_moisture_raw": payload["soil_moisture_raw"],
            "soil_moisture_pct": pct,
            "light_state": bool(payload["light_state"]),
            "pump_state": bool(payload["pump_state"]),
            "soil_temp_c": payload.get("soil_temp_c"),
            "ambient_lux": payload.get("ambient_lux"),
            "air_temp_c": payload.get("air_temp_c"),
            "air_humidity_pct": payload.get("air_humidity_pct"),
            "reservoir_level_pct": payload.get("reservoir_level_pct"),
        }
        # NOTE: both timestamps bound to payload `ts`. Once heartbeats land
        # (post-Phase 1), last_seen_at should be set from server clock so the
        # fleet view can show "online recently" independently of telemetry cadence.
        conn.execute(
            "UPDATE grow_units SET last_known_state_json=?, "
            "last_telemetry_at=?, last_seen_at=? WHERE id=?",
            (json.dumps(state), ts, ts, unit_id),
        )
        conn.commit()
        return inserted_id
    finally:
        conn.close()


def handle_capabilities(unit_id: int, ts: datetime, payload: dict) -> None:
    """Replace the unit's full capability set with what was just declared.

    Caller (the WS listener in Task 4.5) is expected to have validated the
    payload via pydantic. Idempotent: a re-pushed identical payload simply
    rewrites the same rows. A removed sensor disappears on next push.
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    try:
        conn.execute("DELETE FROM grow_unit_capabilities WHERE unit_id=?", (unit_id,))
        for cap in payload["capabilities"]:
            conn.execute(
                "INSERT INTO grow_unit_capabilities "
                "(unit_id, channel, hardware, is_required, unit_label, "
                " installed_at, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unit_id, cap["channel"], cap.get("hardware"),
                 int(cap["is_required"]), cap.get("unit_label"), ts,
                 json.dumps(cap["details"]) if cap.get("details") else None),
            )
        conn.execute(
            "UPDATE grow_units SET last_seen_at=? WHERE id=?",
            (ts, unit_id),
        )
        conn.commit()
    finally:
        conn.close()


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

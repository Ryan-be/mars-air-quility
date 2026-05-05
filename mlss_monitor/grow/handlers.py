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
    """Insert one grow_telemetry row + refresh grow_units.last_known_state_json.

    If payload['soil_moisture_pct'] is missing but the unit has calibration
    set, computes pct server-side from the raw reading.

    Returns the inserted grow_telemetry.id (used by photo upload to backfill
    the telemetry_id join key).
    """
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row

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
    state = {
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
    conn.execute(
        "UPDATE grow_units SET last_known_state_json=?, "
        "last_telemetry_at=?, last_seen_at=? WHERE id=?",
        (json.dumps(state), ts, ts, unit_id),
    )
    conn.commit()
    conn.close()
    return inserted_id

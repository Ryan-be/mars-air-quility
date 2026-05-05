"""WebSocket message envelope + payload schemas.

All text frames on the per-unit WS are JSON: {type, ts, payload}.
Binary frames (photo upload) use a different framing (see PhotoFrame docstring).
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

MessageType = Literal[
    "telemetry", "event", "capabilities",
    "command", "config", "ack",
]


class WSMessage(BaseModel):
    """The envelope every text frame uses on the per-unit WebSocket."""
    type: MessageType
    ts: datetime
    payload: dict


class TelemetryPayload(BaseModel):
    """One reading from the unit's sensors. NULL = unit lacks the sensor."""
    # Required (every unit reports these)
    soil_moisture_raw: int
    light_state: bool
    pump_state: bool
    # Required-but-derived (computed locally if calibration available)
    soil_moisture_pct: float | None = None
    # Optional sensors — present only if the unit has the hardware
    soil_temp_c: float | None = None
    ambient_lux: float | None = None
    air_temp_c: float | None = None
    air_humidity_pct: float | None = None
    reservoir_level_pct: float | None = None

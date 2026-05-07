"""WebSocket message envelope + payload schemas.

All text frames on the per-unit WS are JSON: {type, ts, payload}.
Binary frames (photo upload) use a different framing (see PhotoFrame docstring).

Note on the "ack" type: an earlier draft of the spec defined an
AckPayload that the unit would emit after each command. The
firmware never implemented it because command success is already
observable via existing channels (the next telemetry frame shows
the actuator state, watering pulses surface as `event` frames with
duration_s, safety overrides record audit rows). Adding acks would
duplicate that signal for marginal benefit. The ack class was
removed from this module in Commit C2 (2026-05-07). The server
still accepts (and silently logs) any frame with type=ack so a
unit running an older firmware that emits them won't be torn down.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

from mlss_contracts.enums import EventKind, CommandName, Phase
from mlss_contracts.capabilities import Capability
from mlss_contracts.plant_profiles import LightWindow, WateringConfig

MessageType = Literal[
    "telemetry", "event", "capabilities",
    "command", "config",
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


class EventPayload(BaseModel):
    """Discrete event the unit reports — watering pulse, sensor degraded, etc."""
    kind: EventKind
    details: dict = Field(default_factory=dict)


class CapabilitiesPayload(BaseModel):
    """Sent by unit on WS handshake; declares all detected sensors and actuators."""
    capabilities: list[Capability]
    firmware_version: str
    hardware_serial: str


class CommandPayload(BaseModel):
    """MLSS → unit command, e.g. {name: 'identify', args: {duration_s: 10}}."""
    name: CommandName
    args: dict | None = None


class ConfigPayload(BaseModel):
    """Full config push from MLSS to unit. Resolved values (no NULLs)."""
    plant_type: str
    current_phase: Phase
    light_windows: list[LightWindow]
    watering: WateringConfig
    photo_interval_min: int = Field(ge=1, le=1440)
    photo_active_hours: tuple[int, int] | None = None  # (start_hour, end_hour)
    soil_dry_raw: int | None = None
    soil_wet_raw: int | None = None
    buffer_retention_days: int = Field(default=7, ge=1)


# AckPayload was removed in Commit C2 (2026-05-07). See module docstring
# for the rationale: the firmware never emitted acks, and the existing
# telemetry/event paths already surface command success.

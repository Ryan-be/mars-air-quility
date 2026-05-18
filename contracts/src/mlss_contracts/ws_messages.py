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

from mlss_contracts.enums import EventKind
from mlss_contracts.capabilities import Capability

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
    # Phase 3 diagnostics — populated by the firmware on every frame so
    # the server can cache the latest values into grow_units for the
    # Diagnostics tab. Optional for backward compat with older firmware
    # that doesn't yet emit them.
    uptime_s: float | None = None
    buffer_size: int | None = None
    # Buffer-inspection UI (Phase 3 follow-up): the firmware piggybacks
    # full summaries of both on-disk buffers (text + photo) on every
    # Nth telemetry frame so the Diagnostics tab can render WHAT is
    # queued. Both are dicts with shape:
    #   {"size": int, "total_bytes": int,
    #    "oldest_ts": str|None, "newest_ts": str|None,
    #    "kinds": {<msg_type>: int, ...}}
    # (`kinds` is text-buffer-only — photos are all the same kind.)
    # Stored on the unit at SafetyLoop level; persisted server-side as
    # JSON-in-TEXT on grow_units.last_*_summary_json with omit-doesnt-
    # clobber semantics in handle_telemetry.
    buffer_summary: dict | None = None
    photo_buffer_summary: dict | None = None


class EventPayload(BaseModel):
    """Discrete event the unit reports — watering pulse, sensor degraded, etc."""
    kind: EventKind
    details: dict = Field(default_factory=dict)


class CapabilitiesPayload(BaseModel):
    """Sent by unit on WS handshake; declares all detected sensors and actuators."""
    capabilities: list[Capability]
    firmware_version: str
    hardware_serial: str
    # Phase 3 diagnostics — uptime at the moment the capabilities message
    # is built (typically very small at boot). Optional for backward
    # compat with firmware that doesn't yet emit it.
    uptime_s: float | None = None


# CommandPayload + ConfigPayload were removed in the pre-Phase-4 audit
# cleanup (Bucket C4): both were defined here but no production endpoint
# validated against them — the actual command framing is the loose
# `{type: "command", payload: {name|kind, args}}` shape that the
# firmware dispatcher accepts (mlss_grow.dispatch.dispatch_command).
# Config pushes use a `kind: config_changed` notification + a separate
# bearer-authenticated GET /api/grow/units/<id>/config (api_grow_config.
# get_unit_config) that returns a resolved-overrides dict, not a
# ConfigPayload model. See docs/superpowers/audits/2026-05-08-grow-data-flow-audit.md
# Flow 3 #3 + Flow 4 #1.
#
# CommandName + Phase + LightWindow + WateringConfig stay defined in
# their respective modules — they're imported by config_payloads.py for
# the per-unit Configure-tab PUT endpoints, which IS the live config
# surface.

# AckPayload was removed in Commit C2 (2026-05-07). See module docstring
# for the rationale: the firmware never emitted acks, and the existing
# telemetry/event paths already surface command success.

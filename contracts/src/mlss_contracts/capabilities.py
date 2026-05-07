"""Capability declaration: what sensors and actuators a unit reports."""
from typing import Literal

from pydantic import BaseModel, Field

from mlss_contracts.enums import Channel


# Sense-only-mode (Phase 2): each capability carries a four-state health
# flag so the UI can grey out actuators whose hardware is not yet wired
# without needing an explicit "disabled mode" toggle. See
# docs/superpowers/plans/2026-05-07-grow-phase2-finisher.md (Task 1).
CapabilityHealth = Literal["connected", "untested", "unresponsive", "no_hardware"]


class Capability(BaseModel):
    """One sensor or actuator channel a unit declares it has.

    The unit's firmware auto-detects hardware on the I2C bus + camera CSI
    at startup, then sends one Capability per detected channel to MLSS
    on WebSocket handshake. MLSS persists these to grow_unit_capabilities
    and the dashboard renders only tiles for declared channels.
    """
    channel: Channel
    hardware: str = Field(description="Driver class name, e.g. 'Adafruit_Seesaw_4026'")
    is_required: bool
    unit_label: str = Field(description="Display unit, e.g. '%', '°C', 'lux'")
    details: dict | None = Field(default=None, description="e.g. {'i2c_address': '0x36'}")
    # Sense-only-mode UI degradation:
    # - "connected"    — sensor reading observed / actuator command echoed
    # - "untested"     — declared but never observed (default for actuators
    #                    on a fresh boot before any actuation has happened)
    # - "unresponsive" — server sent a command, didn't see follow-up evidence
    # - "no_hardware"  — firmware init failed (e.g. Automation pHAT not
    #                    powered or absent)
    health: CapabilityHealth = "untested"

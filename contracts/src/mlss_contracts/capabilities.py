"""Capability declaration: what sensors and actuators a unit reports."""
from pydantic import BaseModel, Field
from mlss_contracts.enums import Channel


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

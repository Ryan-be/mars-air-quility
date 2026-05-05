"""Plant + watering + light schedule schemas."""
import re
from pydantic import BaseModel, Field, field_validator
from mlss_contracts.enums import Phase

_HH_MM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class LightWindow(BaseModel):
    """A single on-window in 24h time. Multiple windows per phase allowed."""
    start_hh_mm: str = Field(description="'HH:MM' 24h, e.g. '06:00'")
    end_hh_mm: str = Field(description="'HH:MM' 24h, e.g. '22:00'")

    @field_validator("start_hh_mm", "end_hh_mm")
    @classmethod
    def _hh_mm_format(cls, v: str) -> str:
        if not _HH_MM.match(v):
            raise ValueError(f"must be 'HH:MM' 24h format, got {v!r}")
        return v


class WateringConfig(BaseModel):
    """PID watering tunables. Resolved on the unit at config-apply time."""
    target_moisture_pct: float = Field(ge=0, le=100)
    deadband_pct: float = Field(default=5, ge=0, le=50)
    kp: float = Field(default=0.4)
    ki: float = Field(default=0)
    kd: float = Field(default=0)
    min_pulse_s: float = Field(default=2, gt=0)
    max_pulse_s: float = Field(default=8, gt=0, le=30)  # 30 = hardware safety cap
    soak_window_min: int = Field(default=30, ge=0)


class PlantProfile(BaseModel):
    """A reusable bundle of watering + light defaults for a (plant_type, phase)."""
    plant_type: str
    phase: Phase
    watering: WateringConfig
    light_windows: list[LightWindow]

"""Plant + watering + light schedule schemas.

Pre-Phase-4 audit Bucket C4: this module ships `LightWindow` +
`WateringConfig` for forward-compat. Both were originally consumed by
`ConfigPayload` in ws_messages.py — that model has been deleted as
unused, so neither type is currently imported by production code.
The values they declare ARE enforced via parallel definitions in
`config_payloads.py` (the LightWindowsUpdate / PIDUpdate models that
the live PUT endpoints validate against). Keep the file for now; if
they're still unused after Phase 5 ships, drop the whole module.

`PlantProfile` was also deleted from this module — same reason.
"""
import re
from pydantic import BaseModel, Field, field_validator

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


# PlantProfile model removed in pre-Phase-4 audit Bucket C4 — only
# tests imported it. Plant profile data lives in the grow_plant_profiles
# DB table; the Settings → Grow plant-profile editor uses ad-hoc dicts
# rather than a contract model. Re-introduce if a future feature needs
# wire-format validation.

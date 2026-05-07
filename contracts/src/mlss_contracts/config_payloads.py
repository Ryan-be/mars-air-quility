"""Pydantic schemas for the per-unit Configure-tab PUT endpoints.

Five payload models, each one mapping to a specific PUT route under
`/api/grow/units/<id>/...`:

  * ProfileUpdate         → /profile        (label, plant_type, medium_type,
                                              sown_at, current_phase)
  * PIDUpdate             → /pid            (target%, deadband, kp/ki/kd,
                                              soak_window_min, min/max_pulse)
  * LightWindowsUpdate    → /light_windows  (per-phase HH:MM windows)
  * CalibrationUpdate     → /calibration    (dry_raw, wet_raw)
  * SafetyOverrideRequest → /safety_override (intentional-friction admin path)

All fields are bounds-checked tightly enough that an attacker who's
already through bearer auth can't push a PID config that pumps for ten
minutes or a calibration that inverts the moisture scale.

Lives in `mlss_contracts` so both the Flask server (which validates
incoming requests) and the firmware (which will eventually pull and
apply the same shapes) import from the same source of truth.
"""
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from mlss_contracts._validators import make_min_le_max_validator

_PHASE = Literal["seedling", "vegetative", "flowering", "fruiting", "dormant"]
_MEDIUM = Literal["soil", "coco", "rockwool", "custom"]

# Strict 24-hour HH:MM. Pre-compiled because LightWindow.model_validate()
# fires on every window of every PUT.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ProfileUpdate(BaseModel):
    """All fields optional — the endpoint applies a partial update."""
    label: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=500)
    plant_type: Optional[str] = Field(None, max_length=32)
    medium_type: Optional[_MEDIUM] = None
    sown_at: Optional[datetime] = None
    current_phase: Optional[_PHASE] = None


class PIDUpdate(BaseModel):
    """Tunables for the soil-moisture control loop. Optional → partial update.

    Bounds chosen so a misconfiguration (or a malicious request that's
    somehow through admin auth) can't drive the pump into runaway. The
    firmware enforces its own absolute pulse cap as defence-in-depth, but
    rejecting at the API boundary keeps junk out of grow_units.
    """
    target_pct: Optional[float] = Field(None, ge=0, le=100)
    deadband_pct: Optional[float] = Field(None, ge=0, le=20)
    kp: Optional[float] = Field(None, ge=0, le=10)
    ki: Optional[float] = Field(None, ge=0, le=10)
    kd: Optional[float] = Field(None, ge=0, le=10)
    soak_window_min: Optional[int] = Field(None, ge=0, le=240)
    min_pulse_s: Optional[float] = Field(None, ge=0, le=60)
    max_pulse_s: Optional[float] = Field(None, ge=0, le=60)

    _min_le_max = make_min_le_max_validator("min_pulse_s", "max_pulse_s")


class LightWindow(BaseModel):
    """A single ON window. start == end is rejected (zero-length is a UI bug)."""
    start: str
    end: str

    @model_validator(mode="after")
    def _check_format_and_nonzero(self):
        if not _HHMM_RE.match(self.start):
            raise ValueError(f"start must be HH:MM 24h: {self.start!r}")
        if not _HHMM_RE.match(self.end):
            raise ValueError(f"end must be HH:MM 24h: {self.end!r}")
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class LightWindowsUpdate(BaseModel):
    """Replaces all light windows for one (unit, phase) pair.

    Empty `windows` list is valid — it falls back to the plant profile
    default. max_length=8 caps a single-day schedule at eight on/off
    cycles, which is more than any real grow needs but bounds storage.
    """
    phase: _PHASE
    windows: list[LightWindow] = Field(default_factory=list, max_length=8)


class CalibrationUpdate(BaseModel):
    """Soil-moisture sensor calibration. dry_raw < wet_raw is enforced —
    the firmware computes `pct = (raw - dry) / (wet - dry) * 100` so an
    inverted calibration would silently produce negative or >100% values.
    """
    dry_raw: int = Field(..., ge=0, le=4095)
    wet_raw: int = Field(..., ge=0, le=4095)

    @model_validator(mode="after")
    def _dry_lt_wet(self):
        if self.dry_raw >= self.wet_raw:
            raise ValueError("dry_raw must be < wet_raw")
        return self


_SAFETY_ACTION = Literal[
    "force_pump_on", "force_pump_off",
    "force_light_on", "force_light_off",
    "skip_next_soak",
]


class SafetyOverrideRequest(BaseModel):
    """Intentional-friction override path. The 3-click confirmation
    happens UI-side; the server schema just records the intent.

    duration_s capped at 5 minutes — a stuck override that survives a
    server restart shouldn't be able to drown a plant.
    """
    action: _SAFETY_ACTION
    duration_s: float = Field(..., ge=0, le=300)
    acknowledged_warnings: list[str] = Field(default_factory=list)

from mlss_contracts.plant_profiles import LightWindow, WateringConfig
from pydantic import ValidationError
import pytest


def test_light_window_basic():
    w = LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")
    assert w.start_hh_mm == "06:00"
    assert w.end_hh_mm == "22:00"


def test_light_window_rejects_invalid_format():
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="6am", end_hh_mm="22:00")
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="25:00", end_hh_mm="22:00")
    with pytest.raises(ValidationError):
        LightWindow(start_hh_mm="06:60", end_hh_mm="22:00")


def test_watering_config_defaults():
    w = WateringConfig(target_moisture_pct=55)
    assert w.target_moisture_pct == 55
    assert w.deadband_pct == 5
    assert w.kp == 0.4
    assert w.ki == 0
    assert w.kd == 0
    assert w.min_pulse_s == 2
    assert w.max_pulse_s == 8
    assert w.soak_window_min == 30


# PlantProfile round-trip test removed in pre-Phase-4 audit Bucket C4 —
# the model itself was deleted (no production import). The remaining
# tests pin LightWindow + WateringConfig which are kept for forward-
# compat (see plant_profiles.py module docstring).

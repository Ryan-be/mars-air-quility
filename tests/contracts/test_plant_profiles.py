from mlss_contracts.plant_profiles import PlantProfile, LightWindow, WateringConfig
from mlss_contracts.enums import Phase
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


def test_plant_profile_round_trip():
    p = PlantProfile(
        plant_type="tomato",
        phase=Phase.VEGETATIVE,
        watering=WateringConfig(target_moisture_pct=55),
        light_windows=[LightWindow(start_hh_mm="06:00", end_hh_mm="22:00")],
    )
    blob = p.model_dump_json()
    parsed = PlantProfile.model_validate_json(blob)
    assert parsed == p

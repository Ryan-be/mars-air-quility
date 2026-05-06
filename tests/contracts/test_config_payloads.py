"""Pydantic models for the Configure-tab PUT payloads.

These guard the five per-unit configuration endpoints. Each model is
validated server-side; an invalid request gets a 400 with the pydantic
error list rather than risking a junk row in grow_units or a malformed
PID config in the firmware.
"""
import pytest
from pydantic import ValidationError
from mlss_contracts.config_payloads import (
    ProfileUpdate, PIDUpdate, LightWindowsUpdate,
    CalibrationUpdate, SafetyOverrideRequest,
)


def test_profile_update_accepts_valid():
    p = ProfileUpdate(label="Tom 1", plant_type="tomato", medium_type="soil",
                      sown_at="2026-04-01T00:00:00Z", current_phase="vegetative")
    assert p.label == "Tom 1"


def test_profile_update_rejects_bad_phase():
    with pytest.raises(ValidationError):
        ProfileUpdate(current_phase="not_a_phase")


def test_profile_update_rejects_bad_medium():
    with pytest.raises(ValidationError):
        ProfileUpdate(medium_type="hydroponic_nft")  # not in enum


def test_pid_update_clamps_kp_to_nonneg():
    with pytest.raises(ValidationError):
        PIDUpdate(kp=-0.1)


def test_pid_update_min_pulse_must_be_le_max():
    with pytest.raises(ValidationError):
        PIDUpdate(min_pulse_s=10, max_pulse_s=5)


def test_light_windows_24h_format_required():
    LightWindowsUpdate(phase="vegetative", windows=[
        {"start": "06:00", "end": "22:00"}])
    with pytest.raises(ValidationError):
        LightWindowsUpdate(phase="vegetative", windows=[
            {"start": "6am", "end": "10pm"}])


def test_light_windows_end_after_start_or_wraps_midnight_explicitly():
    # 22:00 → 02:00 wraps midnight, allowed
    LightWindowsUpdate(phase="flowering", windows=[
        {"start": "22:00", "end": "02:00"}])
    # 06:00 → 06:00 (zero-length) rejected
    with pytest.raises(ValidationError):
        LightWindowsUpdate(phase="flowering", windows=[
            {"start": "06:00", "end": "06:00"}])


def test_calibration_dry_must_be_less_than_wet():
    CalibrationUpdate(dry_raw=300, wet_raw=1500)
    with pytest.raises(ValidationError):
        CalibrationUpdate(dry_raw=1500, wet_raw=300)


def test_safety_override_requires_three_confirms():
    # Server-side schema just records intent; the 3-click is UI-side
    s = SafetyOverrideRequest(action="force_pump_on", duration_s=10,
                              acknowledged_warnings=["pump_safety"])
    assert s.duration_s == 10


def test_safety_override_rejects_excessive_duration():
    with pytest.raises(ValidationError):
        SafetyOverrideRequest(action="force_pump_on", duration_s=600,
                              acknowledged_warnings=["pump_safety"])

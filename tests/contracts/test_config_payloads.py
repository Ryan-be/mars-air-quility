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
    CalibrationUpdate, PhotoScheduleUpdate, SafetyOverrideRequest,
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


# ─── PhotoScheduleUpdate ────────────────────────────────────────────


def test_photo_schedule_both_none_means_24x7():
    p = PhotoScheduleUpdate(start_hour=None, end_hour=None)
    assert p.start_hour is None
    assert p.end_hour is None


def test_photo_schedule_both_set_means_window():
    p = PhotoScheduleUpdate(start_hour=6, end_hour=22)
    assert (p.start_hour, p.end_hour) == (6, 22)


def test_photo_schedule_wrap_midnight_allowed():
    """22..06 means "capture overnight" — explicit and useful (e.g. for a
    seedling grown under artificial light during human-night hours)."""
    p = PhotoScheduleUpdate(start_hour=22, end_hour=6)
    assert (p.start_hour, p.end_hour) == (22, 6)


def test_photo_schedule_only_one_set_rejected():
    """Half-set is ambiguous between "open-ended capture" and "user error"
    — surface the ambiguity at the boundary rather than guessing."""
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=6, end_hour=None)
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=None, end_hour=22)


def test_photo_schedule_equal_hours_rejected():
    """start == end is zero-length on the firmware (silent never-capture)."""
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=12, end_hour=12)


def test_photo_schedule_out_of_range_rejected():
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=-1, end_hour=22)
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=6, end_hour=24)
    with pytest.raises(ValidationError):
        PhotoScheduleUpdate(start_hour=25, end_hour=22)

"""apply_config: take a UnitConfig dict (from the server pull) and mutate
the running PIDConfig + light schedule in place. No restart required.

The server resolves null overrides against grow_plant_profiles before
sending, so apply_config can write any non-None field directly into the
PIDConfig — no per-field defaulting in the firmware. Light windows are
filtered to the unit's current_phase since the firmware only ever
schedules one phase at a time.
"""
from datetime import time
from mlss_grow.config_sync import UnitConfig, apply_config
from mlss_grow.pid import PIDConfig
from mlss_grow.safety_loop import LoopConfig
from mlss_grow.light_schedule import parse_window


def _basic_loop_config():
    """LoopConfig pre-populated with original values; tests assert
    apply_config rewrites the right fields."""
    return LoopConfig(
        light_windows=[parse_window("00:00", "06:00")],     # original
        pid=PIDConfig(target_pct=99, kp=99, ki=99, kd=99,
                      min_pulse_s=99, max_pulse_s=99,
                      soak_window_min=99),                  # original
        soil_calibration=(0, 1),                            # original
    )


def test_apply_config_writes_overrides_to_pid_config():
    """Each overrides key maps to a PIDConfig attribute. Resolved values
    arrive non-None from the server; firmware just writes them in."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={
            "watering_target": 55, "kp": 0.4, "ki": 0.1, "kd": 0.05,
            "soak_window_min": 30, "min_pulse_s": 2, "max_pulse_s": 8,
        },
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={"vegetative": [{"start": "06:00", "end": "22:00"}]},
        current_phase="vegetative",
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    pid = loop_cfg.pid
    assert pid.target_pct == 55
    assert pid.kp == 0.4
    assert pid.ki == 0.1
    assert pid.kd == 0.05
    assert pid.soak_window_min == 30
    assert pid.min_pulse_s == 2
    assert pid.max_pulse_s == 8


def test_apply_config_skips_null_overrides():
    """Defensively: if the server ever sends a null value (shouldn't
    happen post-resolution but defensive), the existing PID field is
    preserved rather than overwritten with None."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={"kp": None, "watering_target": 55},  # kp explicitly null
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={"vegetative": []},
        current_phase="vegetative",
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    # watering_target was a real value → written
    assert loop_cfg.pid.target_pct == 55
    # kp was null → preserved at original 99
    assert loop_cfg.pid.kp == 99


def test_apply_config_writes_calibration():
    """Soil calibration tuple gets written to LoopConfig.soil_calibration."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 250, "wet_raw": 1700},
        light_windows={"vegetative": []},
        current_phase="vegetative",
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.soil_calibration == (250, 1700)


def test_apply_config_skips_calibration_when_dry_raw_null():
    """If dry_raw or wet_raw is null (unit hasn't been calibrated), don't
    write a partial tuple — keep whatever was already there."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": None, "wet_raw": None},
        light_windows={"vegetative": []},
        current_phase="vegetative",
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.soil_calibration == (0, 1)  # unchanged


def test_apply_config_replaces_light_schedule_with_current_phase_windows():
    """Only the current_phase's windows are loaded — firmware doesn't
    multi-phase schedule. Other phases' windows present in the payload
    are ignored."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={
            "vegetative": [
                {"start": "06:00", "end": "12:00"},
                {"start": "14:00", "end": "20:00"},
            ],
            "flowering": [{"start": "07:00", "end": "19:00"}],  # ignored
        },
        current_phase="vegetative",
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    assert len(loop_cfg.light_windows) == 2
    assert loop_cfg.light_windows[0] == (time(6, 0), time(12, 0))
    assert loop_cfg.light_windows[1] == (time(14, 0), time(20, 0))


def test_apply_config_handles_phase_with_no_windows():
    """If the current_phase has no windows configured (operator cleared
    them), the schedule becomes empty — `is_light_on` will return False
    everywhere, which is the documented opt-out behaviour."""
    loop_cfg = _basic_loop_config()
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={"flowering": [{"start": "07:00", "end": "19:00"}]},
        current_phase="vegetative",  # no vegetative windows in payload
        plant_type="tomato",
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.light_windows == []


def test_unit_config_dataclass_has_holiday_mode_with_default_false():
    """UnitConfig accepts (and defaults) holiday_mode for backward compat
    with older server responses that don't include the field."""
    # No holiday_mode kwarg — should default False
    cfg = UnitConfig(
        overrides={}, calibration={}, light_windows={},
        current_phase="vegetative", plant_type="tomato",
    )
    assert cfg.holiday_mode is False
    # Explicitly True
    cfg2 = UnitConfig(
        overrides={}, calibration={}, light_windows={},
        current_phase="vegetative", plant_type="tomato",
        holiday_mode=True,
    )
    assert cfg2.holiday_mode is True


def test_apply_config_writes_holiday_mode_to_loop_config():
    """apply_config copies holiday_mode from UnitConfig onto LoopConfig
    so the SafetyLoop tick can short-circuit pump pulses."""
    loop_cfg = _basic_loop_config()
    assert loop_cfg.holiday_mode is False
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={},
        current_phase="vegetative",
        plant_type="tomato",
        holiday_mode=True,
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.holiday_mode is True


def test_apply_config_clears_holiday_mode_when_server_says_off():
    loop_cfg = _basic_loop_config()
    loop_cfg.holiday_mode = True   # was on; coming back from vacation
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={},
        current_phase="vegetative",
        plant_type="tomato",
        holiday_mode=False,
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.holiday_mode is False


# ─── photo_active_hours (Phase 4 polish) ────────────────────────────


def test_apply_config_writes_photo_active_hours_to_loop_config():
    """apply_config copies photo_active_hours from UnitConfig onto
    LoopConfig so SafetyLoop._photo_due() can gate captures by hour."""
    loop_cfg = _basic_loop_config()
    assert loop_cfg.photo_active_hours is None  # new default
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={},
        current_phase="vegetative",
        plant_type="tomato",
        photo_active_hours=(6, 22),
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.photo_active_hours == (6, 22)


def test_apply_config_clears_photo_active_hours_when_server_says_24x7():
    """A unit that previously had a window, then has it cleared via the
    UI (start_hour=null, end_hour=null), should resume 24/7 capture
    on the next config_changed pull."""
    loop_cfg = _basic_loop_config()
    loop_cfg.photo_active_hours = (6, 22)  # was windowed
    new_cfg = UnitConfig(
        overrides={},
        calibration={"dry_raw": 220, "wet_raw": 1600},
        light_windows={},
        current_phase="vegetative",
        plant_type="tomato",
        photo_active_hours=None,
    )
    apply_config(new_cfg, loop_cfg)
    assert loop_cfg.photo_active_hours is None

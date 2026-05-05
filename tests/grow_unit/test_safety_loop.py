"""SafetyLoop: orchestrates sensors → PID → actuators every tick.

Tests verify the orchestration: sensors are read, light state flips
to match the schedule, PID decisions trigger pump pulses, events get
emitted to the supplied callback.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock
from mlss_grow.safety_loop import SafetyLoop, LoopConfig
from mlss_grow.pid import PIDConfig, PIDState
from mlss_grow.light_schedule import parse_window


def _basic_config():
    return LoopConfig(
        light_windows=[parse_window("06:00", "22:00")],
        pid=PIDConfig(target_pct=55),
        photo_interval_min=30,
        photo_active_hours=(6, 22),
    )


def test_tick_reads_sensors_and_emits_telemetry():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda kind, payload: emitted.append((kind, payload)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    kinds = [e[0] for e in emitted]
    assert "telemetry" in kinds
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel["soil_moisture_raw"] == 612


def test_tick_turns_light_on_in_window():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=MagicMock(return_value=False))

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),  # mid-window
    )
    loop.tick()
    light.on.assert_called_once()


def test_tick_turns_light_off_outside_window():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=MagicMock(return_value=True))

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 4, 0),  # before window
    )
    loop.tick()
    light.off.assert_called_once()


def test_tick_fires_pid_pulse_when_dry_and_emits_watering_event(tmp_path):
    """Soil at 30%, target 55, deadband 5, kp=0.4 → pulse 8s (clamped to max 8)."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 612 → ~31.7%

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),  # past soak
    )
    loop.tick()
    pump.pulse.assert_called_once()
    pulse_arg = pump.pulse.call_args[0][0]
    assert pulse_arg > 0
    assert any(k == "event" and p.get("kind") == "watering_pulse"
               for k, p in emitted)


def test_sensor_degraded_emits_event():
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {},  # empty = bad read
                       healthy=lambda: False)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    assert any(k == "event" and p.get("kind") == "sensor_degraded"
               for k, p in emitted)


def test_camera_captured_at_interval(tmp_path):
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612}, healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    camera = MagicMock(capture=MagicMock(
        return_value=(b"\xff\xd8FAKE", {"width": 1920, "height": 1080}),
    ))
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=camera,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    loop.tick()
    camera.capture.assert_called_once()
    assert any(k == "photo" for k, _ in emitted)

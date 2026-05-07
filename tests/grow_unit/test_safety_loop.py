"""SafetyLoop: orchestrates sensors → PID → actuators every tick.

Tests verify the orchestration: sensors are read, light state flips
to match the schedule, PID decisions trigger pump pulses, events get
emitted to the supplied callback.
"""
import logging
from datetime import datetime, timedelta
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
        state_path=str(tmp_path / "watering_state.json"),
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


def test_sensor_read_exception_is_logged_not_swallowed(caplog):
    """A sensor that raises on .read() must produce a warning the operator
    can see, not be silently dropped. The loop continues — tick still
    completes — but the failure is observable via the log.
    """
    boom = MagicMock(channels=lambda: ["soil_moisture"],
                     healthy=lambda: True)
    boom.read.side_effect = RuntimeError("i2c transaction failed")
    boom.__class__.__name__ = "BoomSensor"
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    loop = SafetyLoop(
        sensors=[boom], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    with caplog.at_level(logging.WARNING, logger="mlss_grow.safety_loop"):
        loop.tick()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning to be logged on sensor read failure"
    msg = " ".join(r.getMessage() for r in warnings)
    assert "i2c transaction failed" in msg, (
        f"warning should include the underlying exception text; got: {msg}"
    )


def test_sensor_read_failure_does_not_crash_tick(caplog):
    """Pinning behaviour: even when a sensor raises, .tick() returns normally
    and downstream actuator logic still runs (telemetry still emitted)."""
    boom = MagicMock(channels=lambda: ["soil_moisture"],
                     healthy=lambda: True)
    boom.read.side_effect = ValueError("bad reading")
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[boom], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    with caplog.at_level(logging.WARNING, logger="mlss_grow.safety_loop"):
        loop.tick()  # must not raise

    # Telemetry still emitted despite the sensor failure
    kinds = [k for k, _ in emitted]
    assert "telemetry" in kinds


def test_camera_capture_exception_is_logged_not_swallowed(caplog):
    """A camera that raises on .capture() must surface a warning, not be
    silently swallowed. Without the log the operator has no signal that
    photos have stopped flowing."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    camera = MagicMock()
    camera.capture.side_effect = OSError("libcamera busy")

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=camera,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    with caplog.at_level(logging.WARNING, logger="mlss_grow.safety_loop"):
        loop.tick()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning to be logged on camera capture failure"
    msg = " ".join(r.getMessage() for r in warnings)
    assert "libcamera busy" in msg, (
        f"warning should include the underlying exception text; got: {msg}"
    )


def test_camera_failure_does_not_crash_tick(caplog):
    """Pinning behaviour: a raising camera must not stop the loop —
    telemetry still emits."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    camera = MagicMock()
    camera.capture.side_effect = RuntimeError("hw stall")
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=camera,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
    )
    with caplog.at_level(logging.WARNING, logger="mlss_grow.safety_loop"):
        loop.tick()  # must not raise

    kinds = [k for k, _ in emitted]
    assert "telemetry" in kinds


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


def test_light_budget_blocks_relay_when_20h_already_used_today():
    """Spec §7: even if the schedule says light should be on, refuse if
    >=20h of on-time already accumulated today. Emits safety_cap_hit
    so the operator sees what happened."""
    from mlss_grow.light_budget import LightBudget

    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    # Light starts ON — we want to test that the loop opens the relay
    # because the budget is exhausted.
    light_state = [True]
    light = MagicMock(
        state=lambda: light_state[0],
        on=MagicMock(),
        off=MagicMock(side_effect=lambda: light_state.__setitem__(0, False)),
    )
    emitted = []

    # Pre-populate budget with 21h of on-time, then point the loop's
    # clock just before midnight so we're still on day 1.
    budget = LightBudget()
    midnight_day1 = datetime(2026, 5, 6, 0, 0)
    budget.record_on(midnight_day1)
    budget.record_off(midnight_day1 + timedelta(hours=21))

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        # 22:00 — within scheduled window 06:00-22:00? Let's test at
        # 21:00 which is inside the window.
        now_fn=lambda: datetime(2026, 5, 6, 21, 0),
        light_budget=budget,
    )
    loop.tick()
    light.off.assert_called_once()
    light.on.assert_not_called()
    cap_events = [
        p for k, p in emitted
        if k == "event" and p.get("kind") == "safety_cap_hit"
    ]
    assert cap_events, "expected safety_cap_hit when light budget exhausted"
    assert cap_events[0]["details"]["cap"] == "light_20h_per_day"


def test_light_budget_recovers_after_midnight_utc():
    """Cross UTC midnight with the budget exhausted on day 1 → on day 2
    the schedule turns the light on normally."""
    from mlss_grow.light_budget import LightBudget

    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    budget = LightBudget()
    midnight_day1 = datetime(2026, 5, 6, 0, 0)
    budget.record_on(midnight_day1)
    budget.record_off(midnight_day1 + timedelta(hours=21))

    # Day 2 at 08:00, well inside scheduled window — fresh budget.
    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(), emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 7, 8, 0),
        light_budget=budget,
    )
    loop.tick()
    light.on.assert_called_once()


def test_pump_pulse_hard_capped_at_30s_regardless_of_config(tmp_path):
    """Spec §7: pump pulse is hard-capped at 30s on the unit, even if
    config.max_pulse_s is bigger and the PID wants a longer pulse.
    The cap is a hardware-protection invariant — not a tunable.

    Setup: max_pulse_s=60, soil at 0% (huge error), kp=2 → PID wants
    ~110s clamped to config.max_pulse_s=60. The safety loop must
    *further* clamp to 30s before calling pump.pulse.
    """
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 200},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 200 → 0%
    cfg.pid = PIDConfig(target_pct=55, kp=2.0, max_pulse_s=60)

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),
        state_path=str(tmp_path / "watering_state.json"),
    )
    loop.tick()
    pump.pulse.assert_called_once()
    pulse_arg = pump.pulse.call_args[0][0]
    assert pulse_arg == 30.0, (
        f"hard cap broken — pump.pulse called with {pulse_arg}s, "
        f"expected 30s ceiling per spec §7"
    )
    # Cap-hit event surfaces to dashboard
    cap_events = [
        p for k, p in emitted
        if k == "event" and p.get("kind") == "safety_cap_hit"
    ]
    assert cap_events, "expected a safety_cap_hit event when pump cap fires"
    assert cap_events[0]["details"]["cap"] == "pump_30s_max_pulse"


def test_pump_cooldown_after_hard_cap_blocks_subsequent_pulses(tmp_path):
    """After a hard-capped pulse, the next 5 minutes are cooldown — even
    if PID wants to fire and the soak window has elapsed (which it
    won't normally, but a future config change could shorten soak)."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 200},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)
    cfg.pid = PIDConfig(target_pct=55, kp=2.0, max_pulse_s=60,
                         soak_window_min=0)  # no soak so we test cooldown alone

    t0 = datetime(2026, 5, 3, 12, 0)
    state = PIDState(last_pulse_at=datetime(2025, 1, 1))
    now_holder = [t0]

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda *a, **k: None,
        now_fn=lambda: now_holder[0],
        pid_state=state,
        state_path=str(tmp_path / "watering_state.json"),
    )
    # First tick: hard cap fires, cooldown is armed.
    loop.tick()
    assert pump.pulse.call_count == 1
    assert state.pump_cooldown_until is not None

    # Advance 30s (well within the 5-min cooldown). PID would
    # otherwise want to fire again (soak=0, error still huge).
    pump.pulse.reset_mock()
    now_holder[0] = t0 + timedelta(seconds=30)
    loop.tick()
    pump.pulse.assert_not_called()


def test_pump_cooldown_clears_after_5_minutes(tmp_path):
    """After 5 minutes + a tick, the cooldown lifts. PID can fire again
    (and may hit the cap again, re-arming the cooldown)."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 200},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)
    cfg.pid = PIDConfig(target_pct=55, kp=2.0, max_pulse_s=60,
                         soak_window_min=0)

    t0 = datetime(2026, 5, 3, 12, 0)
    state = PIDState(last_pulse_at=datetime(2025, 1, 1))
    now_holder = [t0]

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda *a, **k: None,
        now_fn=lambda: now_holder[0],
        pid_state=state,
        state_path=str(tmp_path / "watering_state.json"),
    )
    loop.tick()  # Hard cap, cooldown armed
    pump.pulse.reset_mock()

    # Advance past the 5-minute cooldown.
    now_holder[0] = t0 + timedelta(minutes=5, seconds=1)
    loop.tick()
    pump.pulse.assert_called_once()


def test_pump_normal_pulse_not_clamped_when_under_30s(tmp_path):
    """Sanity: a PID-decided pulse of 8s passes through unchanged (no
    cooldown armed, no cap event). The cap only fires when PID actually
    asks for >30s."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # ~31.7% → kp=0.4 * (55-31.7) = 9.32
                                        # clamped to default max=8

    state = PIDState(last_pulse_at=datetime(2025, 1, 1))
    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        pid_state=state,
        state_path=str(tmp_path / "watering_state.json"),
    )
    loop.tick()
    pump.pulse.assert_called_once()
    assert pump.pulse.call_args[0][0] <= 30.0  # well under cap
    assert state.pump_cooldown_until is None  # no cooldown armed
    cap_events = [
        p for k, p in emitted
        if k == "event" and p.get("kind") == "safety_cap_hit"
    ]
    assert not cap_events


def test_holiday_mode_suppresses_pump_pulse_but_still_emits_telemetry():
    """Reproduces the test_tick_fires_pid_pulse setup but with
    holiday_mode=True — pump must NOT pulse, but light decision +
    telemetry must continue normally so the operator sees a continuous
    log + a lit plant when they come home."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 612 → ~31.7%, would pulse
    cfg.holiday_mode = True

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),
    )
    loop.tick()
    # No pump pulse — holiday mode short-circuited the PID branch
    pump.pulse.assert_not_called()
    # No watering_pulse event either
    kinds = [(k, p.get("kind") if isinstance(p, dict) else None)
             for k, p in emitted]
    assert ("event", "watering_pulse") not in kinds
    # But telemetry + light decision still happened
    assert any(k == "telemetry" for k, _ in emitted)
    light.on.assert_called_once()  # noon falls inside the default window


def test_tick_telemetry_includes_uptime_s_when_provider_supplied():
    """Phase 3 diagnostics: when a uptime_provider is wired, every
    telemetry frame carries uptime_s so the server can cache it on
    grow_units.last_uptime_s for the Diagnostics tab."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        uptime_provider=lambda: 123.45,
    )
    loop.tick()
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel["uptime_s"] == 123.45


def test_tick_telemetry_includes_buffer_size_when_buffer_supplied():
    """Phase 3 diagnostics: when a buffer is wired, every telemetry frame
    reports the current buffered-row count via buffer.size()."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    fake_buffer = MagicMock(size=lambda: 7)
    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        buffer=fake_buffer,
    )
    loop.tick()
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel["buffer_size"] == 7


def test_tick_includes_buffer_summary_every_10th_tick():
    """Phase 3 buffer-inspection UI: full buffer summary piggybacks on
    every 10th telemetry frame (not every tick — see
    _BUFFER_SUMMARY_EVERY_N_TICKS rationale). Tick 9 must NOT carry a
    summary; tick 10 must carry one."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    fake_summary = {
        "size": 247, "total_bytes": 78423,
        "oldest_ts": "2026-05-07T03:42:00",
        "newest_ts": "2026-05-07T04:17:30",
        "kinds": {"telemetry": 240, "event": 6, "capabilities": 1},
    }
    fake_buffer = MagicMock(
        size=lambda: 247,
        summary=lambda: fake_summary,
    )

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        buffer=fake_buffer,
    )
    # Tick 9 times — none should carry a summary (count is 1..9, not
    # divisible by 10).
    for _ in range(9):
        emitted.clear()
        loop.tick()
        tel = next(p for k, p in emitted if k == "telemetry")
        assert "buffer_summary" not in tel, (
            "buffer_summary must NOT appear on ticks 1..9 — only every 10th"
        )
    # 10th tick — must carry a summary.
    emitted.clear()
    loop.tick()
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel.get("buffer_summary") == fake_summary, (
        f"tick 10 must carry buffer_summary; got telemetry={tel}"
    )


def test_tick_includes_photo_buffer_summary_on_piggyback_tick():
    """Photo buffer gets the same every-10th-tick treatment as the text
    buffer. Both summaries hitch a ride on the same telemetry frame."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    photo_summary = {
        "size": 12,
        "total_bytes": 4_800_000,
        "oldest_ts": "2026-05-07T03:00:00Z",
        "newest_ts": "2026-05-07T05:30:00Z",
    }
    fake_photo_buffer = MagicMock(summary=lambda: photo_summary)

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        photo_buffer=fake_photo_buffer,
    )
    # Skip to tick 10.
    for _ in range(9):
        loop.tick()
    emitted.clear()
    loop.tick()  # 10th
    tel = next(p for k, p in emitted if k == "telemetry")
    assert tel.get("photo_buffer_summary") == photo_summary


def test_tick_omits_buffer_summary_when_buffer_is_none():
    """Defensive: a SafetyLoop wired without a buffer must not crash on
    the piggyback tick — it just skips the summary fields."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)
    emitted = []

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda k, p: emitted.append((k, p)),
        now_fn=lambda: datetime(2026, 5, 3, 12, 0),
        # No buffer / photo_buffer wired.
    )
    # Drive 10 ticks — the piggyback branch fires on tick 10 and must
    # be a no-op when the buffers aren't wired.
    for _ in range(10):
        loop.tick()  # must not raise
    tel = next(p for k, p in emitted if k == "telemetry")
    # Neither summary should be in any payload.
    assert all(
        "buffer_summary" not in p and "photo_buffer_summary" not in p
        for k, p in emitted if k == "telemetry"
    )


def test_tick_telemetry_omits_diagnostics_fields_when_not_wired():
    """Backward compat: a SafetyLoop built without uptime_provider /
    buffer must not crash and must not put the new keys in the
    telemetry payload (so old listeners aren't surprised)."""
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
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
    tel = next(p for k, p in emitted if k == "telemetry")
    assert "uptime_s" not in tel
    assert "buffer_size" not in tel


# ----------------------------------------------------------------------
# Phase 3 Task 7: watering_state.json firmware persistence.
# These tests pin the integration between SafetyLoop and the
# state_persistence module: hydrate on init, save after each pulse.
# ----------------------------------------------------------------------


def test_safety_loop_loads_pid_state_on_init(tmp_path):
    """Hydrate PID state from disk on SafetyLoop construction. Without
    this, a service restart cold-starts the integral — at Ki=0 it's
    invisible but at higher Ki it'd briefly mis-shape pulses."""
    import json

    state_path = tmp_path / "watering_state.json"
    persisted = {
        "error_integral": 17.5,
        "last_error": -2.25,
        "last_pulse_at_iso": "2026-05-06T10:00:00",
    }
    state_path.write_text(json.dumps(persisted))

    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    pid_state = PIDState(last_pulse_at=datetime(2000, 1, 1))
    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=_basic_config(),
        emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 6, 12, 0),
        pid_state=pid_state,
        state_path=str(state_path),
    )
    # Hydration: the in-memory PIDState now reflects the file.
    assert loop._pid_state.error_integral == 17.5
    assert loop._pid_state.last_error == -2.25
    assert loop._pid_state.last_pulse_at == datetime(2026, 5, 6, 10, 0, 0)


def test_safety_loop_saves_pid_state_after_pulse_decision(tmp_path):
    """A tick that fires a pulse must persist the new last_pulse_at +
    integral so a restart picks up where we left off (no integral
    cold-start, no skipped soak window)."""
    import json

    state_path = tmp_path / "watering_state.json"
    assert not state_path.exists()

    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 612},
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 612 → ~31.7%, fires PID

    fire_time = datetime(2026, 5, 6, 12, 0)
    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda *a, **k: None,
        now_fn=lambda: fire_time,
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),  # past soak
        state_path=str(state_path),
    )
    loop.tick()

    pump.pulse.assert_called_once()  # sanity: tick actually fired
    assert state_path.exists(), "save_state must persist after a fire"
    on_disk = json.loads(state_path.read_text())
    # last_pulse_at_iso reflects the fire time, not the pre-fire default
    assert on_disk["last_pulse_at_iso"] == fire_time.isoformat()


def test_safety_loop_does_not_save_when_in_deadband(tmp_path):
    """A read-only tick (soil already wet → in deadband) must NOT
    rewrite the state file. Pinning this keeps SD-card write churn
    minimal: pulses are infrequent, so persisting only on fires is
    enough to survive restarts without hammering the card every 30s.
    """
    import json

    # Pre-seed a state file we can detect mutation against.
    state_path = tmp_path / "watering_state.json"
    seed = {
        "error_integral": 99.0,
        "last_error": 88.0,
        "last_pulse_at_iso": "2025-12-31T23:59:59",
    }
    state_path.write_text(json.dumps(seed))

    # Soil at target (55%) → error 0 → in deadband, no fire.
    sensor = MagicMock(channels=lambda: ["soil_moisture"],
                       read=lambda: {"soil_moisture": 915},  # raw 915 → ~55%
                       healthy=lambda: True)
    pump = MagicMock(state=lambda: False)
    light = MagicMock(state=lambda: False)

    cfg = _basic_config()
    cfg.soil_calibration = (200, 1500)  # raw 915 → ~55%, in deadband

    loop = SafetyLoop(
        sensors=[sensor], pump=pump, light=light, camera=None,
        config=cfg, emit=lambda *a, **k: None,
        now_fn=lambda: datetime(2026, 5, 6, 12, 0),
        pid_state=PIDState(last_pulse_at=datetime(2025, 1, 1)),
        state_path=str(state_path),
    )
    loop.tick()

    pump.pulse.assert_not_called()  # sanity: deadband = no fire
    # File still has the seeded values — no rewrite happened.
    on_disk = json.loads(state_path.read_text())
    assert on_disk == seed

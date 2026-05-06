"""invoke_safety_override: drive an actuator directly for a bounded
duration, bypassing the normal PID loop.

Critical property: must NOT block the caller thread for `duration_s`
seconds. The dispatcher thread services the WS receive loop; if a
safety_override stalled it for 60s the unit would appear offline to
the server while the override was running. invoke_safety_override
schedules the off-flip on a timer thread and returns immediately.
"""
import threading
import time as time_mod
from unittest.mock import MagicMock

import pytest

from mlss_grow.safety_override import (
    SafetyOverrideState,
    invoke_safety_override,
)


def _mock_actuators():
    """Build a (pump, light) pair of MagicMock actuators."""
    pump = MagicMock(state=MagicMock(return_value=False))
    light = MagicMock(state=MagicMock(return_value=False))
    return pump, light


def _wait_for(predicate, timeout=2.0, poll=0.01):
    """Poll until predicate() is true or timeout elapses. Tests use this
    to wait for a Timer-scheduled callback rather than time.sleep'ing
    blindly."""
    deadline = time_mod.monotonic() + timeout
    while time_mod.monotonic() < deadline:
        if predicate():
            return True
        time_mod.sleep(poll)
    return False


def test_invoke_safety_override_force_pump_on_turns_pump_on_immediately():
    """force_pump_on flips the pump on synchronously before returning."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_pump_on", duration_s=0.1, pump=pump,
                            light=light, state=state)
    pump.on.assert_called_once()


def test_invoke_safety_override_force_pump_on_runs_for_duration():
    """After duration_s elapses, the pump is flipped off via a Timer."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_pump_on", duration_s=0.05, pump=pump,
                            light=light, state=state)
    # Wait for the timer-driven .off() — bounded so a hang doesn't stall.
    assert _wait_for(lambda: pump.off.called, timeout=2.0)


def test_invoke_safety_override_does_not_block_caller_thread():
    """Key non-functional requirement: the call must return in well under
    `duration_s` seconds so the dispatcher thread isn't stalled."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    t0 = time_mod.monotonic()
    invoke_safety_override("force_pump_on", duration_s=10.0, pump=pump,
                            light=light, state=state)
    elapsed = time_mod.monotonic() - t0
    # 100ms is plenty of headroom for thread spawn + a generous CI fudge.
    assert elapsed < 0.2, (
        f"invoke_safety_override blocked caller for {elapsed:.3f}s — must "
        f"schedule the off-flip on a Timer, not sleep inline"
    )
    # Cancel the pending off-timer so we don't leak a thread into other tests.
    state.cancel_pending()


def test_invoke_safety_override_force_pump_off_is_immediate_no_timer():
    """force_pump_off has no duration — the pump goes off and stays off,
    no Timer scheduled."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_pump_off", duration_s=0, pump=pump,
                            light=light, state=state)
    pump.off.assert_called_once()
    pump.on.assert_not_called()
    assert state.pending_timer is None


def test_invoke_safety_override_force_light_on_runs_for_duration():
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_light_on", duration_s=0.05, pump=pump,
                            light=light, state=state)
    light.on.assert_called_once()
    assert _wait_for(lambda: light.off.called, timeout=2.0)


def test_invoke_safety_override_force_light_off_is_immediate():
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_light_off", duration_s=0, pump=pump,
                            light=light, state=state)
    light.off.assert_called_once()
    light.on.assert_not_called()


def test_invoke_safety_override_skip_next_soak_sets_flag():
    """skip_next_soak doesn't touch actuators — sets a flag the safety
    loop checks on its next pass to bypass the soak-window guard."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("skip_next_soak", duration_s=0, pump=pump,
                            light=light, state=state)
    pump.on.assert_not_called()
    pump.off.assert_not_called()
    light.on.assert_not_called()
    light.off.assert_not_called()
    assert state.skip_next_soak is True


def test_invoke_safety_override_unknown_action_logs_no_op():
    """Unknown action → swallow + log; don't raise. The dispatcher thread
    must not die on a malformed payload."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    # No exception raised:
    invoke_safety_override("nuke_plant", duration_s=10, pump=pump,
                            light=light, state=state)
    pump.on.assert_not_called()
    light.on.assert_not_called()


def test_invoke_safety_override_replaces_pending_timer():
    """A second override replaces the pending off-timer of the first.
    Otherwise back-to-back overrides would leak Timer threads."""
    pump, light = _mock_actuators()
    state = SafetyOverrideState()
    invoke_safety_override("force_pump_on", duration_s=10, pump=pump,
                            light=light, state=state)
    first_timer = state.pending_timer
    assert first_timer is not None

    invoke_safety_override("force_light_on", duration_s=10, pump=pump,
                            light=light, state=state)
    # Old timer was cancelled; new one took its place.
    assert state.pending_timer is not None
    assert state.pending_timer is not first_timer
    state.cancel_pending()


def test_safety_override_state_cancel_pending_idempotent():
    """cancel_pending is safe to call when no timer is scheduled — keeps
    the dispatcher's cleanup path simple."""
    state = SafetyOverrideState()
    # No pending timer; should be a no-op.
    state.cancel_pending()
    state.cancel_pending()


def test_safety_override_state_consume_skip_next_soak_returns_and_clears():
    """The safety-loop polls this each tick — it must return True exactly
    once after skip_next_soak was set, then return False until set again."""
    state = SafetyOverrideState()
    state.skip_next_soak = True
    assert state.consume_skip_next_soak() is True
    assert state.consume_skip_next_soak() is False

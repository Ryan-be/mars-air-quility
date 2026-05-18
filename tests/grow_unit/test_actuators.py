"""Actuator ABC + Pump (sinking output) + Light (relay) for Automation pHAT."""
import time
from unittest.mock import MagicMock
from mlss_grow.actuators.base import Actuator
from mlss_grow.actuators.automation_phat import (
    AutomationPHATPump, AutomationPHATLight,
)
import pytest


def test_actuator_is_abstract():
    with pytest.raises(TypeError):
        Actuator()


def test_pump_on_off_drives_sinking_output(monkeypatch):
    fake_phat = MagicMock()
    fake_phat.output.one.on = MagicMock()
    fake_phat.output.one.off = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)

    p = AutomationPHATPump()
    p.on()
    fake_phat.output.one.on.assert_called_once()
    p.off()
    fake_phat.output.one.off.assert_called_once()


def test_pump_state_tracks_on_off(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump()
    assert p.state() is False
    p.on()
    assert p.state() is True
    p.off()
    assert p.state() is False


def test_pump_pulse_runs_for_duration_then_stops(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump()
    t0 = time.monotonic()
    p.pulse(0.5)
    elapsed = time.monotonic() - t0
    assert 0.45 < elapsed < 0.7  # ran for ~0.5s
    assert p.state() is False     # back off after pulse


def test_pump_pulse_capped_via_constructor(monkeypatch):
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    p = AutomationPHATPump(safety_max_pulse_s=0.3)
    t0 = time.monotonic()
    p.pulse(999)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5  # capped


def test_light_on_off_drives_relay(monkeypatch):
    fake_phat = MagicMock()
    fake_phat.relay.one.on = MagicMock()
    fake_phat.relay.one.off = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    l = AutomationPHATLight()
    l.on()
    fake_phat.relay.one.on.assert_called_once()
    l.off()
    fake_phat.relay.one.off.assert_called_once()


def test_light_pulse_blinks_n_times(monkeypatch):
    """Used by identify command — blink relay every 500ms."""
    fake_phat = MagicMock()
    monkeypatch.setattr(
        "mlss_grow.actuators.automation_phat._automationhat", fake_phat)
    l = AutomationPHATLight()
    l.blink_pattern(duration_s=1.0, period_s=0.2)
    on_calls = fake_phat.relay.one.on.call_count
    off_calls = fake_phat.relay.one.off.call_count
    # ~1.0s / 0.2s period = 5 cycles. Roughly equal on/off counts.
    assert on_calls >= 4
    assert off_calls >= 4

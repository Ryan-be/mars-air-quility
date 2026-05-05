"""Pimoroni Automation pHAT actuators: pump on sinking OUT 1, light on relay."""
import logging
import time
from mlss_grow.actuators.base import Actuator

log = logging.getLogger(__name__)

try:
    import automationhat as _automationhat
except ImportError:
    _automationhat = None


class AutomationPHATPump(Actuator):
    """Water pump driven by the pHAT's sinking output OUT 1."""

    def __init__(self, safety_max_pulse_s: float = 30.0) -> None:
        self._on = False
        self._safety_max = safety_max_pulse_s

    def on(self) -> None:
        if _automationhat is not None:
            _automationhat.output.one.on()
        self._on = True

    def off(self) -> None:
        if _automationhat is not None:
            _automationhat.output.one.off()
        self._on = False

    def state(self) -> bool:
        return self._on

    def pulse(self, seconds: float) -> None:
        duration = min(seconds, self._safety_max)
        if duration <= 0:
            return
        self.on()
        try:
            time.sleep(duration)
        finally:
            self.off()


class AutomationPHATLight(Actuator):
    """Grow light driven by the pHAT's relay (NO contact, fail-safe to dark)."""

    def __init__(self, safety_max_pulse_s: float = 86400.0) -> None:
        self._on = False
        self._safety_max = safety_max_pulse_s

    def on(self) -> None:
        if _automationhat is not None:
            _automationhat.relay.one.on()
        self._on = True

    def off(self) -> None:
        if _automationhat is not None:
            _automationhat.relay.one.off()
        self._on = False

    def state(self) -> bool:
        return self._on

    def pulse(self, seconds: float) -> None:
        duration = min(seconds, self._safety_max)
        if duration <= 0:
            return
        self.on()
        try:
            time.sleep(duration)
        finally:
            self.off()

    def blink_pattern(self, duration_s: float, period_s: float = 0.5) -> None:
        """Blink the light at given period for total duration. Used by identify command."""
        end = time.monotonic() + duration_s
        while time.monotonic() < end:
            self.on()
            time.sleep(period_s / 2)
            self.off()
            time.sleep(period_s / 2)

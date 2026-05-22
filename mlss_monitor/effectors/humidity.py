"""Humidity-balancing controllers.

Symmetric pair acting on the same reading field (``humidity``) but
voting in opposite directions:

* :class:`Humidifier` — ON when humidity < target. Hub *or* grow scope
  because per-canopy humidifiers (e.g. a small reservoir mister inside
  a propagation tent) are common.
* :class:`Dehumidifier` — ON when humidity > target. Hub-only because
  per-canopy dehumidification is rare in the topology the spec covers;
  the room-scale unit is what the operator deploys.

Rule shape: ``{"target": <float>}``. Like the heater pair, deadband
lives at the evaluator level for v1 (only flip on desired != current);
v2 can layer per-rule hysteresis once we have a real-world chatter
case to calibrate against.
"""
from __future__ import annotations

from mlss_monitor.effectors.base import EffectorController, Scope


def _humidity(reading: dict) -> float | None:
    """Pull humidity from a reading; tolerates missing field."""
    val = reading.get("humidity")
    if val is None:
        return None
    return float(val)


class Humidifier(EffectorController):
    """Humidifier — ON when humidity < target."""

    effector_type = "humidifier"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        humidity = _humidity(reading)
        target = rules.get("target")
        if humidity is None or target is None:
            return False
        return humidity < float(target)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        # Per the COMPATIBLE_SCOPES matrix in base.py — humidifiers can
        # be deployed at hub-room scale or inside an individual grow
        # unit (e.g. a propagation tent).
        return {Scope.HUB, Scope.GROW_UNIT}


class Dehumidifier(EffectorController):
    """Dehumidifier — ON when humidity > target."""

    effector_type = "dehumidifier"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        humidity = _humidity(reading)
        target = rules.get("target")
        if humidity is None or target is None:
            return False
        return humidity > float(target)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}

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

from datetime import datetime

from mlss_monitor.effectors.base import EffectorController, Scope


def _humidity(reading: dict) -> float | None:
    """Pull humidity from a reading; tolerates missing field.

    The key is ``humidity_pct`` because the evaluator's reading dict
    comes from ``dataclasses.asdict(NormalisedReading)`` which surfaces
    the canonical column name. See the 2026-05-31 incident note in
    ``tests/test_effectors_dispatch.py``.
    """
    val = reading.get("humidity_pct")
    if val is None:
        return None
    return float(val)


def _humidity_reason(rule_name: str, humidity: float | None,
                     target: float | None, *, direction: str) -> dict:
    """One-rule reason for the symmetric humidifier/dehumidifier pair.

    ``direction='below'`` fires when humidity < target (Humidifier);
    ``direction='above'`` fires when humidity > target (Dehumidifier).
    """
    if humidity is None:
        return {
            "rule":   rule_name,
            "fired":  False,
            "detail": "No humidity reading available",
        }
    if target is None:
        return {
            "rule":   rule_name,
            "fired":  False,
            "detail": "No target humidity configured",
        }
    if direction == "below":
        fired = humidity < target
        detail = (f"{humidity:.0f}% < {target}% target" if fired
                  else f"{humidity:.0f}% ≥ {target}% target")
    else:
        fired = humidity > target
        detail = (f"{humidity:.0f}% > {target}% target" if fired
                  else f"{humidity:.0f}% ≤ {target}% target")
    return {"rule": rule_name, "fired": fired, "detail": detail}


class Humidifier(EffectorController):
    """Humidifier — ON when humidity < target."""

    effector_type = "humidifier"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        humidity = _humidity(reading)
        target = rules.get("target")
        if humidity is None or target is None:
            return False
        return humidity < float(target)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        humidity = _humidity(reading)
        target_raw = rules.get("target")
        target = float(target_raw) if target_raw is not None else None
        reason = _humidity_reason(
            "HumidityBelowTarget", humidity, target, direction="below",
        )
        return {
            "decision":     "on" if reason["fired"] else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      [reason],
        }

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

    def evaluate(self, reading: dict, rules: dict) -> dict:
        humidity = _humidity(reading)
        target_raw = rules.get("target")
        target = float(target_raw) if target_raw is not None else None
        reason = _humidity_reason(
            "HumidityAboveTarget", humidity, target, direction="above",
        )
        return {
            "decision":     "on" if reason["fired"] else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      [reason],
        }

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}

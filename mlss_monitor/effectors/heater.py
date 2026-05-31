"""Heater-family controllers.

Two controllers live here because they sit on opposite scopes:

* :class:`WholeRoomHeater` — hub-only, reads the hub ``temperature``
  reading (canonical sensor name) and turns ON when below the target.
* :class:`HeatPad` — grow-only, reads the per-unit ``soil_temp_c``
  reading and falls back to ``air_temp_c`` when no soil probe is
  present so an operator without a soil sensor still gets useful
  control.

Rule shape: ``{"target": <float>}``. Deadband / hysteresis is a v2
enhancement — for now the controllers are simple comparisons so the
evaluator's de-dupe (only flip when desired != current_state) bears
the burden of preventing rapid-cycle chatter. That's correct for the
slow-response thermal mass of a heated room or pad.
"""
from __future__ import annotations

from datetime import datetime

from mlss_monitor.effectors.base import EffectorController, Scope


def _hub_temperature(reading: dict) -> float | None:
    """Pull the hub-scope temperature reading.

    Tolerates two field names: the canonical hub one (``temperature_c``,
    surfaced by ``dataclasses.asdict(NormalisedReading)`` in
    :func:`mlss_monitor.effectors.evaluator._read_for_plug`) and the
    grow-scope one (``air_temp_c``, from ``grow_telemetry``). The
    fallback exists so a misconfigured row that swaps scopes between
    controller and reading still degrades gracefully rather than
    silently never firing.

    The earlier Phase-3 implementation read ``temperature`` (no suffix)
    which never matches the dataclass field — see the 2026-05-31
    incident note in ``tests/test_effectors_dispatch.py``.
    """
    if "temperature_c" in reading and reading["temperature_c"] is not None:
        return float(reading["temperature_c"])
    if "air_temp_c" in reading and reading["air_temp_c"] is not None:
        return float(reading["air_temp_c"])
    return None


def _below_target_reason(rule_name: str, temp: float | None,
                         target: float | None, units: str,
                         missing_label: str) -> dict:
    """One-rule reason row for the heater family (ON when temp < target)."""
    if temp is None:
        return {
            "rule":   rule_name,
            "fired":  False,
            "detail": f"No {missing_label} reading available",
        }
    if target is None:
        return {
            "rule":   rule_name,
            "fired":  False,
            "detail": "No target temperature configured",
        }
    fired = temp < target
    detail = (f"{temp:.1f}{units} < {target}{units} target" if fired
              else f"{temp:.1f}{units} ≥ {target}{units} target")
    return {"rule": rule_name, "fired": fired, "detail": detail}


class WholeRoomHeater(EffectorController):
    """Hub-scope room heater. ON when air temp < target."""

    effector_type = "whole_room_heater"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        temp = _hub_temperature(reading)
        target = rules.get("target")
        if temp is None or target is None:
            return False
        return temp < float(target)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        temp = _hub_temperature(reading)
        target_raw = rules.get("target")
        target = float(target_raw) if target_raw is not None else None
        reason = _below_target_reason(
            "RoomTempRule", temp, target, "°C", "temperature",
        )
        return {
            "decision":     "on" if reason["fired"] else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      [reason],
        }

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}


class HeatPad(EffectorController):
    """Grow-scope soil heat pad.

    ON when ``soil_temp_c < target``. When no soil sensor is present
    (``soil_temp_c is None``), falls back to ``air_temp_c`` so a unit
    without a probe still gets useful control. With neither reading the
    controller plays safe and votes OFF — better to leave the pad off
    than to drive it on with no telemetry feedback.
    """

    effector_type = "heat_pad"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        target = rules.get("target")
        if target is None:
            return False
        target_f = float(target)
        soil = reading.get("soil_temp_c")
        if soil is not None:
            return float(soil) < target_f
        air = reading.get("air_temp_c")
        if air is not None:
            return float(air) < target_f
        return False

    def evaluate(self, reading: dict, rules: dict) -> dict:
        target_raw = rules.get("target")
        target = float(target_raw) if target_raw is not None else None
        soil = reading.get("soil_temp_c")
        if soil is not None:
            reason = _below_target_reason(
                "SoilTempRule", float(soil), target, "°C", "soil temperature",
            )
        else:
            air = reading.get("air_temp_c")
            reason = _below_target_reason(
                "AirTempFallbackRule",
                float(air) if air is not None else None,
                target, "°C", "soil or air temperature",
            )
        return {
            "decision":     "on" if reason["fired"] else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      [reason],
        }

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.GROW_UNIT}

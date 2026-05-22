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

from mlss_monitor.effectors.base import EffectorController, Scope


def _hub_temperature(reading: dict) -> float | None:
    """Pull the hub-scope temperature reading.

    Tolerates two field names: the canonical hub one (``temperature``,
    from :meth:`mlss_monitor.hot_tier.HotTier.snapshot`) and the
    grow-scope one (``air_temp_c``, from ``grow_telemetry``). The
    fallback exists so a misconfigured row that swaps scopes between
    controller and reading still degrades gracefully rather than
    silently never firing.
    """
    if "temperature" in reading and reading["temperature"] is not None:
        return float(reading["temperature"])
    if "air_temp_c" in reading and reading["air_temp_c"] is not None:
        return float(reading["air_temp_c"])
    return None


class WholeRoomHeater(EffectorController):
    """Hub-scope room heater. ON when air temp < target."""

    effector_type = "whole_room_heater"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        temp = _hub_temperature(reading)
        target = rules.get("target")
        if temp is None or target is None:
            return False
        return temp < float(target)

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

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.GROW_UNIT}

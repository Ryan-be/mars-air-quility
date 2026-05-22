"""Per-type controller lookup for the evaluator loop.

Single-flat-dict mapping from the ``effector_type`` string (matching one
entry in :data:`database.effectors_schema._EFFECTOR_TYPES`) to the
concrete :class:`mlss_monitor.effectors.base.EffectorController`
subclass implementing the per-type rule.

The :mod:`mlss_monitor.effectors.evaluator` loop calls
:func:`controller_for` on every ``smart_plugs`` row each tick and
instantiates a fresh controller per call — controllers are stateless
in v1, so allocation cost is negligible and future per-tick state
(e.g. AC min-off timer) can be carried on the instance without
leaking across plugs.

Parity contract: every type in
:data:`database.effectors_schema._EFFECTOR_TYPES` must have an entry
here. A parametrised test
(``tests/test_effectors_dispatch.py::TestControllerRegistry``) walks
the canonical type tuple and asserts the registry resolves each one;
adding a new type to the schema without a registry entry fails CI.
"""
from __future__ import annotations

from mlss_monitor.effectors.ac import AC
from mlss_monitor.effectors.base import EffectorController
from mlss_monitor.effectors.fan import CirculationFan, Fan, FanCarbonFilter
from mlss_monitor.effectors.generic import CO2Injector, Generic
from mlss_monitor.effectors.heater import HeatPad, WholeRoomHeater
from mlss_monitor.effectors.humidity import Dehumidifier, Humidifier
from mlss_monitor.effectors.light import LightSupplementary

# Keys mirror ``database.effectors_schema._EFFECTOR_TYPES``. Order in
# this dict is presentation-only (it doesn't affect lookup) — grouped
# by family for human readability.
_REGISTRY: dict[str, type[EffectorController]] = {
    # Fan family
    "fan":                 Fan,
    "fan_carbon_filter":   FanCarbonFilter,
    "circulation_fan":     CirculationFan,
    # Heating / cooling
    "ac":                  AC,
    "whole_room_heater":   WholeRoomHeater,
    "heat_pad":            HeatPad,
    # Humidity balancing
    "humidifier":          Humidifier,
    "dehumidifier":        Dehumidifier,
    # Lighting
    "light_supplementary": LightSupplementary,
    # Manual-only
    "generic":             Generic,
    "co2_injector":        CO2Injector,
}


def controller_for(effector_type: str) -> type[EffectorController] | None:
    """Return the controller *class* for *effector_type*, or ``None``.

    Returning the class (rather than an instance) keeps any per-tick
    state local to the evaluator caller — important for the v2
    additions (AC min-off timer, carbon-filter min-on) that will carry
    "last switched at" on the instance.
    """
    return _REGISTRY.get(effector_type)

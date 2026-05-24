"""Shared enums + type/scope compatibility matrix for the v2 effector API.

Kept tiny and stdlib-only so it can be imported from the API blueprint,
the (Phase 3) per-type controllers, and the (Phase 3) evaluator loop
without pulling in any of those layers' transitive imports.

The single source of truth for the list of supported effector types is
:data:`database.effectors_schema._EFFECTOR_TYPES` (the DB CHECK
constraint). We re-export it here so the API validator and the schema
constraint can never disagree.

Phase 3 adds :class:`EffectorController`, the ABC every per-type
controller subclasses. The evaluator loop
(:mod:`mlss_monitor.effectors.evaluator`) instantiates a fresh
controller per tick via :func:`mlss_monitor.effectors.registry.controller_for`
and calls :meth:`EffectorController.should_be_on` to decide whether to
flip the matching live plug handle on or off.

For the side-panel "Why is the fan on/off?" surface, controllers also
expose :meth:`EffectorController.evaluate` returning the rich
``{decision, evaluated_at, reasons}`` shape — see method docstring.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import FrozenSet

from database.effectors_schema import _EFFECTOR_TYPES


# Re-exported for callers (api_effectors_v2 + Phase 3 controllers) that
# need the canonical list without reaching into a private name on the
# DB module. New types must be added to ``_EFFECTOR_TYPES`` first (so
# the DB CHECK accepts them), then will appear here automatically.
EFFECTOR_TYPES: tuple[str, ...] = _EFFECTOR_TYPES


class Scope(str, Enum):
    """The two places an effector can attach in the MLSS topology."""

    HUB = "hub"
    GROW_UNIT = "grow_unit"


# Per-type scope whitelist. Used by the v2 API to reject e.g. attaching
# a hub-only "ac" to a specific grow unit, or a per-grow-only "heat_pad"
# to the hub. Plan §"Cross-cutting decisions" + Phase 2 Task 2.5.
COMPATIBLE_SCOPES: dict[str, FrozenSet[str]] = {
    "fan":                 frozenset({"hub"}),
    "fan_carbon_filter":   frozenset({"hub"}),
    "ac":                  frozenset({"hub"}),
    "whole_room_heater":   frozenset({"hub"}),
    "dehumidifier":        frozenset({"hub"}),
    "humidifier":          frozenset({"hub", "grow_unit"}),
    "light_supplementary": frozenset({"hub", "grow_unit"}),
    "heat_pad":            frozenset({"grow_unit"}),
    "generic":             frozenset({"hub", "grow_unit"}),
    # The two extra types reserved for the topology UI's "Add effector"
    # picker. circulation_fan = hub-only (whole-room mixing); the
    # CO2 injector is reserved for a future canopy-mounted enrichment
    # rig and behaves like a hub-room appliance.
    "circulation_fan":     frozenset({"hub"}),
    "co2_injector":        frozenset({"hub"}),
}


def is_scope_compatible(effector_type: str, scope: str) -> bool:
    """Return True iff *scope* is one of the legal placements for *type*."""
    return scope in COMPATIBLE_SCOPES.get(effector_type, frozenset())


# ── Per-type controller interface (Phase 3) ────────────────────────────────


class EffectorController(ABC):
    """Abstract base class for per-type effector control rules.

    Concrete subclasses (e.g. :class:`mlss_monitor.effectors.fan.Fan`,
    :class:`mlss_monitor.effectors.ac.AC`) declare:

    * ``effector_type`` — the string key matching one entry in
      :data:`EFFECTOR_TYPES`. The :func:`mlss_monitor.effectors.registry.controller_for`
      registry maps from that key to the subclass.
    * :meth:`should_be_on` — pure decision: given the latest sensor
      reading dict and the operator-configured rules dict, return True
      to switch the plug on, False to switch off. The evaluator loop
      only flips the plug when the desired state differs from the
      currently-persisted ``current_state``, so this method must be
      idempotent and side-effect-free.
    * :meth:`compatible_scopes` — class method mirroring the static
      :data:`COMPATIBLE_SCOPES` dict above. Reserved for the v2 API to
      consult per-type if the matrix ever moves off the static dict.
    """

    effector_type: str

    @abstractmethod
    def should_be_on(self, reading: dict, rules: dict) -> bool:
        """Return True iff the plug should be ON for this reading + rules."""

    def evaluate(self, reading: dict, rules: dict) -> dict:
        """Return a rich decision dict for the side-panel "Why?" surface.

        Default implementation wraps :meth:`should_be_on` with an empty
        reason list — fine for manual-only controllers (``generic``,
        ``co2_injector``) where there's nothing to explain. Sensor-driven
        controllers (Fan, AC, Heater, Humidifier, Dehumidifier, HeatPad,
        LightSupplementary) override to populate one row per rule
        iteration so the panel can render a vote-by-vote breakdown.

        Shape::

            {
              "decision": "on" | "off",
              "evaluated_at": "<ISO UTC>",
              "reasons": [
                {"rule": "TemperatureRule", "fired": True,
                 "detail": "21.3 > 20.0 max"},
                ...
              ],
            }

        The ``rule`` value is the rule class name (or any short
        machine-readable token) so the UI can colour-code each row;
        ``fired`` is the per-rule ON vote; ``detail`` is the
        human-readable threshold comparison.
        """
        want_on = self.should_be_on(reading, rules)
        return {
            "decision":     "on" if want_on else "off",
            "evaluated_at": datetime.utcnow().isoformat(),
            "reasons":      [],
        }

    @classmethod
    @abstractmethod
    def compatible_scopes(cls) -> set[Scope]:
        """Return the set of :class:`Scope` values this controller supports."""

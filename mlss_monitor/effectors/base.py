"""Shared enums + type/scope compatibility matrix for the v2 effector API.

Kept tiny and stdlib-only so it can be imported from the API blueprint,
the (Phase 3) per-type controllers, and the (Phase 3) evaluator loop
without pulling in any of those layers' transitive imports.

The single source of truth for the list of supported effector types is
:data:`database.effectors_schema._EFFECTOR_TYPES` (the DB CHECK
constraint). We re-export it here so the API validator and the schema
constraint can never disagree.
"""
from __future__ import annotations

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

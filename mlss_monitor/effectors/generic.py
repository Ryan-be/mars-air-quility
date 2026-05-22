"""Manual-only controllers (no algorithmic rule).

Both classes here vote False unconditionally so the evaluator loop
never tries to drive them. The operator drives them via the v2
``POST /api/effectors/<id>/state`` endpoint with explicit
``"on"`` / ``"off"`` calls (which set ``auto_mode=0`` so the evaluator
backs off entirely until the operator returns to ``"auto"``).

* :class:`Generic` — a catch-all for hardware that doesn't fit any of
  the typed controllers. Available at both hub and grow scope so
  operators can pre-create a plug row before a typed controller for
  their hardware exists.
* :class:`CO2Injector` — reserved for a future canopy-mounted CO₂
  enrichment rig. v1 has no algorithmic rule because CO₂ enrichment
  requires sealed-room conditions + a flowmeter we don't yet support;
  manual-only until the operator-facing dosing model is designed.
  Listed in :data:`database.effectors_schema._EFFECTOR_TYPES` so admins
  can create the row today and bind real rules later.
"""
from __future__ import annotations

from mlss_monitor.effectors.base import EffectorController, Scope


class Generic(EffectorController):
    """Catch-all controller — manual-only."""

    effector_type = "generic"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        # No algorithmic rule. Operator drives the plug directly.
        return False

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB, Scope.GROW_UNIT}


class CO2Injector(EffectorController):
    """CO₂ injector — manual-only for v1.

    TODO(v2): once sealed-room CO₂ enrichment is in scope, replace
    with a target-driven rule (``ON when eco2 < target_ppm AND
    light is on AND room is sealed``) plus a max-on guard so a
    miscalibrated sensor can't dose unbounded CO₂.
    """

    effector_type = "co2_injector"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return False

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}

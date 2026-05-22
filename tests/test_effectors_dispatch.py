"""Tests for ``mlss_monitor.effectors`` per-type controllers + registry.

Covers Phase 3 of the MLSS topology feature
(``docs/superpowers/plans/2026-05-22-mlss-topology.md``):

* :class:`EffectorController` ABC enforcement (Task 3.1)
* Per-type ``should_be_on()`` rule logic for every type registered in
  ``database.effectors_schema._EFFECTOR_TYPES`` (Tasks 3.2 + 3.3)
* Registry lookup parity with the canonical type list (Task 3.4 prereq)

Pure unit tests — no DB, no Flask, no asyncio. The evaluator-loop tests
that DO need the rest of the stack live in ``test_effector_evaluator.py``.
"""
from __future__ import annotations

import pytest


# ── Task 3.1: EffectorController ABC ───────────────────────────────────────


class TestEffectorControllerABC:
    def test_abc_cannot_be_instantiated_directly(self):
        from mlss_monitor.effectors.base import EffectorController
        with pytest.raises(TypeError):
            EffectorController()  # pylint: disable=abstract-class-instantiated

    def test_subclass_missing_should_be_on_cannot_instantiate(self):
        from mlss_monitor.effectors.base import EffectorController, Scope

        class BrokenCtrl(EffectorController):
            effector_type = "broken"

            @classmethod
            def compatible_scopes(cls):
                return {Scope.HUB}

        with pytest.raises(TypeError):
            BrokenCtrl()  # pylint: disable=abstract-class-instantiated

    def test_concrete_subclass_with_both_methods_instantiates(self):
        from mlss_monitor.effectors.base import EffectorController, Scope

        class GoodCtrl(EffectorController):
            effector_type = "good"

            def should_be_on(self, reading, rules):
                return False

            @classmethod
            def compatible_scopes(cls):
                return {Scope.HUB}

        instance = GoodCtrl()
        assert instance.should_be_on({}, {}) is False
        assert Scope.HUB in GoodCtrl.compatible_scopes()

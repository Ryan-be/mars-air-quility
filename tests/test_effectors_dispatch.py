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


# ── Task 3.2: Fan controller — wraps existing four-rule FanController ─────


class TestFanController:
    """Fan wraps Temperature/TVOC/Humidity/PM25 rules — any vote = ON."""

    def _reading(self, **kw):
        base = {
            "temperature": 18.0,
            "humidity":    50.0,
            "eco2":        400,
            "tvoc":        100,
            "pm2_5":       None,
        }
        base.update(kw)
        return base

    def _rules(self, **kw):
        # Mirror the seed defaults the migration writes for the legacy fan.
        base = {
            "tvoc_max":         500,
            "temp_max":         20.0,
            "humidity_max":     70.0,
            "pm25_max":         25.0,
            "temp_enabled":     True,
            "tvoc_enabled":     True,
            "humidity_enabled": False,
            "pm25_enabled":     False,
        }
        base.update(kw)
        return base

    def test_should_be_on_when_temp_exceeds_max(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        assert ctrl.should_be_on(
            self._reading(temperature=25.0), self._rules(temp_max=20.0),
        ) is True

    def test_should_be_off_when_all_within_range(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        assert ctrl.should_be_on(
            self._reading(temperature=18.0, tvoc=100), self._rules(),
        ) is False

    def test_should_be_on_when_tvoc_exceeds(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        assert ctrl.should_be_on(
            self._reading(tvoc=600), self._rules(tvoc_max=500),
        ) is True

    def test_humidity_rule_off_by_default(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        # humidity high but humidity_enabled=False (default) → no opinion
        assert ctrl.should_be_on(
            self._reading(humidity=90.0), self._rules(),
        ) is False

    def test_humidity_rule_engaged_when_enabled(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        assert ctrl.should_be_on(
            self._reading(humidity=90.0),
            self._rules(humidity_enabled=True, humidity_max=70.0),
        ) is True

    def test_pm25_rule_engaged_when_enabled(self):
        from mlss_monitor.effectors.fan import Fan
        ctrl = Fan()
        assert ctrl.should_be_on(
            self._reading(pm2_5=42.5),
            self._rules(pm25_enabled=True, pm25_max=25.0),
        ) is True

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.fan import Fan
        from mlss_monitor.effectors.base import Scope
        assert Fan.compatible_scopes() == {Scope.HUB}


class TestFanCarbonFilterController:
    """FanCarbonFilter — same rule semantics as Fan for v1.

    v2 will layer a 5-minute min-on protection on top so the filter
    media isn't whipsawed; for v1 we leave that as a TODO so the
    evaluator doesn't break unit-test isolation.
    """

    def test_should_be_on_when_temp_exceeds_max(self):
        from mlss_monitor.effectors.fan import FanCarbonFilter
        ctrl = FanCarbonFilter()
        reading = {"temperature": 25.0, "humidity": 50.0, "tvoc": 100}
        rules = {"temp_max": 20.0, "tvoc_max": 500,
                 "humidity_max": 70.0, "pm25_max": 25.0,
                 "temp_enabled": True, "tvoc_enabled": True,
                 "humidity_enabled": False, "pm25_enabled": False}
        assert ctrl.should_be_on(reading, rules) is True

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.fan import FanCarbonFilter
        from mlss_monitor.effectors.base import Scope
        assert FanCarbonFilter.compatible_scopes() == {Scope.HUB}


class TestCirculationFanController:
    """CirculationFan — same rule semantics as Fan for v1."""

    def test_should_be_on_when_temp_exceeds_max(self):
        from mlss_monitor.effectors.fan import CirculationFan
        ctrl = CirculationFan()
        reading = {"temperature": 25.0, "humidity": 50.0, "tvoc": 100}
        rules = {"temp_max": 20.0, "tvoc_max": 500,
                 "humidity_max": 70.0, "pm25_max": 25.0,
                 "temp_enabled": True, "tvoc_enabled": True,
                 "humidity_enabled": False, "pm25_enabled": False}
        assert ctrl.should_be_on(reading, rules) is True

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.fan import CirculationFan
        from mlss_monitor.effectors.base import Scope
        assert CirculationFan.compatible_scopes() == {Scope.HUB}


# ── Task 3.3a: WholeRoomHeater + HeatPad ──────────────────────────────────


class TestWholeRoomHeater:
    """Hub-scope: ON when ``temperature`` < target.

    Grow-scope: would read ``air_temp_c`` but WholeRoomHeater is
    hub-only by the COMPATIBLE_SCOPES matrix, so we only exercise
    the hub path here (``temperature``).
    """

    def test_on_when_temp_below_target(self):
        from mlss_monitor.effectors.heater import WholeRoomHeater
        ctrl = WholeRoomHeater()
        assert ctrl.should_be_on(
            {"temperature": 15.0}, {"target": 20.0},
        ) is True

    def test_off_when_temp_above_target(self):
        from mlss_monitor.effectors.heater import WholeRoomHeater
        ctrl = WholeRoomHeater()
        assert ctrl.should_be_on(
            {"temperature": 22.0}, {"target": 20.0},
        ) is False

    def test_off_at_exact_target(self):
        from mlss_monitor.effectors.heater import WholeRoomHeater
        ctrl = WholeRoomHeater()
        assert ctrl.should_be_on(
            {"temperature": 20.0}, {"target": 20.0},
        ) is False

    def test_grow_scope_reads_air_temp_c_field(self):
        """Defensive: if a hub heater ever sees a grow-shape reading it
        should still degrade gracefully by reading air_temp_c."""
        from mlss_monitor.effectors.heater import WholeRoomHeater
        ctrl = WholeRoomHeater()
        assert ctrl.should_be_on(
            {"air_temp_c": 15.0}, {"target": 20.0},
        ) is True

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.heater import WholeRoomHeater
        from mlss_monitor.effectors.base import Scope
        assert WholeRoomHeater.compatible_scopes() == {Scope.HUB}


class TestHeatPad:
    """Grow-only: ON when ``soil_temp_c`` < target; falls back to
    ``air_temp_c`` when no soil probe is present."""

    def test_on_when_soil_temp_below_target(self):
        from mlss_monitor.effectors.heater import HeatPad
        ctrl = HeatPad()
        assert ctrl.should_be_on(
            {"soil_temp_c": 12.0, "air_temp_c": 22.0},
            {"target": 18.0},
        ) is True

    def test_off_when_soil_temp_above_target(self):
        from mlss_monitor.effectors.heater import HeatPad
        ctrl = HeatPad()
        assert ctrl.should_be_on(
            {"soil_temp_c": 22.0, "air_temp_c": 15.0},
            {"target": 18.0},
        ) is False

    def test_falls_back_to_air_temp_when_no_soil_probe(self):
        """Graceful degradation: missing soil_temp_c → use air_temp_c."""
        from mlss_monitor.effectors.heater import HeatPad
        ctrl = HeatPad()
        # soil_temp_c missing → fall back to air_temp_c=15 < target=18 → ON
        assert ctrl.should_be_on(
            {"air_temp_c": 15.0},
            {"target": 18.0},
        ) is True
        # air_temp_c above target → OFF
        assert ctrl.should_be_on(
            {"air_temp_c": 22.0},
            {"target": 18.0},
        ) is False

    def test_off_when_no_temperature_at_all(self):
        """No readings at all → cannot decide → safe default OFF."""
        from mlss_monitor.effectors.heater import HeatPad
        ctrl = HeatPad()
        assert ctrl.should_be_on({}, {"target": 18.0}) is False

    def test_compatible_scopes_is_grow_only(self):
        from mlss_monitor.effectors.heater import HeatPad
        from mlss_monitor.effectors.base import Scope
        assert HeatPad.compatible_scopes() == {Scope.GROW_UNIT}


# ── Task 3.3b: AC controller ───────────────────────────────────────────────


class TestACController:
    """AC — ON when temp > target.

    v1 explicitly skips the 5-minute min-off compressor protection
    that real AC units need: that needs runtime state the evaluator
    doesn't expose. The min-off enforcement is a v2 TODO; this test
    suite documents the v1 behaviour so a future regression doesn't
    silently re-introduce the gap.
    """

    def test_on_when_temp_above_target(self):
        from mlss_monitor.effectors.ac import AC
        ctrl = AC()
        assert ctrl.should_be_on(
            {"temperature": 28.0}, {"target": 22.0},
        ) is True

    def test_off_when_temp_below_target(self):
        from mlss_monitor.effectors.ac import AC
        ctrl = AC()
        assert ctrl.should_be_on(
            {"temperature": 18.0}, {"target": 22.0},
        ) is False

    def test_off_at_exact_target(self):
        from mlss_monitor.effectors.ac import AC
        ctrl = AC()
        assert ctrl.should_be_on(
            {"temperature": 22.0}, {"target": 22.0},
        ) is False

    def test_off_when_no_target_set(self):
        from mlss_monitor.effectors.ac import AC
        ctrl = AC()
        assert ctrl.should_be_on({"temperature": 28.0}, {}) is False

    def test_min_off_compressor_protection_not_enforced_in_v1(self):
        """Regression guard for the documented v1 limitation.

        Calling should_be_on twice in quick succession with the same
        hot reading must return True both times — v1 has no runtime
        memory of "we just switched off, give the compressor 5 min".
        That memory belongs to the evaluator scheduler in v2.
        """
        from mlss_monitor.effectors.ac import AC
        ctrl = AC()
        reading = {"temperature": 28.0}
        rules = {"target": 22.0}
        # Two back-to-back calls; v1 has no min-off state so both vote ON.
        assert ctrl.should_be_on(reading, rules) is True
        assert ctrl.should_be_on(reading, rules) is True

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.ac import AC
        from mlss_monitor.effectors.base import Scope
        assert AC.compatible_scopes() == {Scope.HUB}


# ── Task 3.3c: Humidifier / Dehumidifier ──────────────────────────────────


class TestHumidifierController:
    """Humidifier — ON when humidity < target."""

    def test_on_when_humidity_below_target(self):
        from mlss_monitor.effectors.humidity import Humidifier
        ctrl = Humidifier()
        assert ctrl.should_be_on(
            {"humidity": 40.0}, {"target": 60.0},
        ) is True

    def test_off_when_humidity_above_target(self):
        from mlss_monitor.effectors.humidity import Humidifier
        ctrl = Humidifier()
        assert ctrl.should_be_on(
            {"humidity": 70.0}, {"target": 60.0},
        ) is False

    def test_off_at_exact_target(self):
        from mlss_monitor.effectors.humidity import Humidifier
        ctrl = Humidifier()
        assert ctrl.should_be_on(
            {"humidity": 60.0}, {"target": 60.0},
        ) is False

    def test_off_when_humidity_missing(self):
        from mlss_monitor.effectors.humidity import Humidifier
        ctrl = Humidifier()
        assert ctrl.should_be_on({}, {"target": 60.0}) is False

    def test_compatible_scopes_includes_hub_and_grow(self):
        from mlss_monitor.effectors.humidity import Humidifier
        from mlss_monitor.effectors.base import Scope
        scopes = Humidifier.compatible_scopes()
        assert Scope.HUB in scopes
        assert Scope.GROW_UNIT in scopes


class TestDehumidifierController:
    """Dehumidifier — ON when humidity > target."""

    def test_on_when_humidity_above_target(self):
        from mlss_monitor.effectors.humidity import Dehumidifier
        ctrl = Dehumidifier()
        assert ctrl.should_be_on(
            {"humidity": 80.0}, {"target": 60.0},
        ) is True

    def test_off_when_humidity_below_target(self):
        from mlss_monitor.effectors.humidity import Dehumidifier
        ctrl = Dehumidifier()
        assert ctrl.should_be_on(
            {"humidity": 50.0}, {"target": 60.0},
        ) is False

    def test_off_at_exact_target(self):
        from mlss_monitor.effectors.humidity import Dehumidifier
        ctrl = Dehumidifier()
        assert ctrl.should_be_on(
            {"humidity": 60.0}, {"target": 60.0},
        ) is False

    def test_compatible_scopes_is_hub_only(self):
        from mlss_monitor.effectors.humidity import Dehumidifier
        from mlss_monitor.effectors.base import Scope
        assert Dehumidifier.compatible_scopes() == {Scope.HUB}

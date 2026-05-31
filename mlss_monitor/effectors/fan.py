"""Fan-family controllers (hub-room scope).

Wraps the rule machinery (Temperature/TVOC/Humidity/PM2.5) inlined
into :mod:`mlss_monitor.effectors._fan_rules` behind the v2
:class:`mlss_monitor.effectors.base.EffectorController` interface so the
Phase 3 evaluator loop can route per-type rule decisions through a
single uniform API.

Three controllers live here because they share rule semantics for v1:

* :class:`Fan` — the original whole-room exhaust fan.
* :class:`FanCarbonFilter` — same rules, plus a planned v2 min-on guard
  so the filter media doesn't get whipsawed by transient TVOC spikes.
* :class:`CirculationFan` — same rules; reserved for canopy-mixing
  use cases the topology UI's "Add effector" picker exposes.

Decision policy (inherited from the legacy ``FanController``): OR-logic
across rules — any rule voting ON turns the fan on. The four rule
toggles + thresholds live inside the per-row ``rules_json`` blob that
the v2 API persists; the migration in :func:`database.effectors_schema`
seeded sensible defaults for the legacy fan row.
"""
from __future__ import annotations

from datetime import datetime

from mlss_monitor.effectors._fan_rules import (
    FanAction,
    HumidityRule,
    PM25Rule,
    SensorReading,
    TemperatureRule,
    TVOCRule,
)
from mlss_monitor.effectors.base import EffectorController, Scope

# Shared rule instances — stateless, so a single instance is reused
# across every evaluator tick to avoid per-call allocation churn.
_RULES = (TemperatureRule(), TVOCRule(), HumidityRule(), PM25Rule())


def _reading_from_dict(reading: dict) -> SensorReading:
    """Coerce the evaluator's reading dict into a :class:`SensorReading`.

    The dict is shaped like ``dataclasses.asdict(NormalisedReading(...))``
    because :func:`mlss_monitor.effectors.evaluator._read_for_plug`
    coerces :class:`mlss_monitor.data_sources.base.NormalisedReading`
    (from :meth:`mlss_monitor.hot_tier.HotTier.snapshot`) to a dict via
    :func:`dataclasses.asdict`. That surfaces the canonical column
    names — ``temperature_c``, ``humidity_pct``, ``tvoc_ppb``,
    ``eco2_ppm``, ``pm25_ug_m3`` — NOT the short legacy names. The
    initial Phase 3 implementation read the legacy names and saw None
    for every sensor → all rules defaulted to 0 → fan stayed off at
    26°C in production (incident 2026-05-31). The regression guard
    lives in
    ``tests/test_effectors_dispatch.py::TestHubControllersReadCanonicalFieldNames``.
    """
    return SensorReading(
        temperature=float(reading.get("temperature_c") or 0.0),
        humidity=float(reading.get("humidity_pct") or 0.0),
        eco2=int(reading.get("eco2_ppm") or 0),
        tvoc=int(reading.get("tvoc_ppb") or 0),
        pm2_5=reading.get("pm25_ug_m3"),
    )


def _any_rule_says_on(reading: dict, rules: dict) -> bool:
    """OR-logic helper: True iff any enabled rule votes ON for *reading*."""
    sensor_reading = _reading_from_dict(reading)
    for rule in _RULES:
        result = rule.evaluate(sensor_reading, rules)
        if result.action == FanAction.ON:
            return True
    return False


def _fan_family_evaluate(reading: dict, rules: dict) -> dict:
    """Shared evaluate() implementation for the three fan controllers.

    Walks every rule in :data:`_RULES` once, collecting one entry per
    rule for the side-panel's "Why?" surface. Each entry surfaces the
    rule class name (for colour-coding), whether that rule's vote is ON
    (``fired``), and the human-readable detail string the legacy
    ``/api/fan/auto-status`` endpoint already produced — so the
    operator sees the same explanation copy they did before this branch.
    """
    sensor_reading = _reading_from_dict(reading)
    reasons: list[dict] = []
    decision_on = False
    for rule in _RULES:
        result = rule.evaluate(sensor_reading, rules)
        fired = result.action == FanAction.ON
        if fired:
            decision_on = True
        reasons.append({
            "rule":   type(rule).__name__,
            "fired":  fired,
            "detail": result.reason,
        })
    return {
        "decision":     "on" if decision_on else "off",
        "evaluated_at": datetime.utcnow().isoformat(),
        "reasons":      reasons,
    }


class Fan(EffectorController):
    """Whole-room exhaust fan — the legacy single Kasa plug."""

    effector_type = "fan"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return _any_rule_says_on(reading, rules)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        return _fan_family_evaluate(reading, rules)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}


class FanCarbonFilter(EffectorController):
    """Carbon-filter fan — same v1 rule logic as :class:`Fan`.

    TODO(v2): enforce a 5-minute minimum on-time so the filter media
    isn't switched off seconds after a transient TVOC spike subsides.
    Tracked in the topology backlog; runtime state for the timer would
    have to live on the evaluator (not in ``smart_plugs.rules_json``)
    so the v1 evaluator deliberately skips that protection.
    """

    effector_type = "fan_carbon_filter"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return _any_rule_says_on(reading, rules)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        return _fan_family_evaluate(reading, rules)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}


class CirculationFan(EffectorController):
    """Mixing/circulation fan — same v1 rule logic as :class:`Fan`."""

    effector_type = "circulation_fan"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return _any_rule_says_on(reading, rules)

    def evaluate(self, reading: dict, rules: dict) -> dict:
        return _fan_family_evaluate(reading, rules)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}

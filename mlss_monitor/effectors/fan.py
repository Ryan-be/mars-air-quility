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

    The dict shape varies slightly between hub-scope readings (which come
    from :meth:`mlss_monitor.hot_tier.HotTier.snapshot` and use the
    canonical sensor field names) and grow-scope readings (which come
    from ``grow_telemetry`` and use ``air_temp_c`` / similar). Hub-scope
    is the common case and what every fan-family controller expects
    today, so we read the hub field names and tolerate missing values.
    """
    return SensorReading(
        temperature=float(reading.get("temperature") or 0.0),
        humidity=float(reading.get("humidity") or 0.0),
        eco2=int(reading.get("eco2") or 0),
        tvoc=int(reading.get("tvoc") or 0),
        pm2_5=reading.get("pm2_5"),
    )


def _any_rule_says_on(reading: dict, rules: dict) -> bool:
    """OR-logic helper: True iff any enabled rule votes ON for *reading*."""
    sensor_reading = _reading_from_dict(reading)
    for rule in _RULES:
        result = rule.evaluate(sensor_reading, rules)
        if result.action == FanAction.ON:
            return True
    return False


class Fan(EffectorController):
    """Whole-room exhaust fan — the legacy single Kasa plug."""

    effector_type = "fan"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return _any_rule_says_on(reading, rules)

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

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}


class CirculationFan(EffectorController):
    """Mixing/circulation fan — same v1 rule logic as :class:`Fan`."""

    effector_type = "circulation_fan"

    def should_be_on(self, reading: dict, rules: dict) -> bool:
        return _any_rule_says_on(reading, rules)

    @classmethod
    def compatible_scopes(cls) -> set[Scope]:
        return {Scope.HUB}

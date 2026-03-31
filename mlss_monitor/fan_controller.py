"""Extensible rule-based fan controller.

Uses the Strategy pattern: each ``FanRule`` subclass encapsulates one
decision criterion.  ``FanController`` aggregates the registered rules and
produces a single fan action together with a human-readable explanation of
*why* each rule voted the way it did.

Adding a new rule is a two-step process:
    1. Create a subclass of ``FanRule``.
    2. Register an instance via ``FanController.register()``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


# ── Value objects ────────────────────────────────────────────────────────────


class FanAction(Enum):
    ON = "on"
    OFF = "off"
    NO_OPINION = "no_opinion"


@dataclass(frozen=True)
class SensorReading:
    temperature: float
    humidity: float
    eco2: int
    tvoc: int
    vpd_kpa: float | None = None
    pm2_5: float | None = None


@dataclass(frozen=True)
class RuleResult:
    rule_name: str
    action: FanAction
    reason: str


# ── Rule interface ───────────────────────────────────────────────────────────


class FanRule(ABC):
    """Interface that every auto-control rule must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short machine-readable identifier (e.g. ``'temperature'``)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line human-readable summary shown in the UI."""

    @abstractmethod
    def evaluate(self, reading: SensorReading, settings: dict) -> RuleResult:
        """Return the rule's recommendation for the current sensor state."""


# ── Built-in rules ──────────────────────────────────────────────────────────


class TemperatureRule(FanRule):
    @property
    def name(self) -> str:
        return "temperature"

    @property
    def description(self) -> str:
        return "Activates the fan when temperature exceeds the configured maximum."

    def evaluate(self, reading: SensorReading, settings: dict) -> RuleResult:
        if not settings.get("temp_enabled", True):
            return RuleResult(self.name, FanAction.NO_OPINION, "Temperature rule disabled")

        temp_max = settings.get("temp_max", 20.0)
        if reading.temperature > temp_max:
            return RuleResult(
                self.name,
                FanAction.ON,
                f"Temperature {reading.temperature:.1f}°C exceeds max {temp_max}°C",
            )
        return RuleResult(
            self.name,
            FanAction.NO_OPINION,
            f"Temperature {reading.temperature:.1f}°C within range (max {temp_max}°C)",
        )


class TVOCRule(FanRule):
    @property
    def name(self) -> str:
        return "tvoc"

    @property
    def description(self) -> str:
        return "Activates the fan when TVOC exceeds the configured maximum."

    def evaluate(self, reading: SensorReading, settings: dict) -> RuleResult:
        if not settings.get("tvoc_enabled", True):
            return RuleResult(self.name, FanAction.NO_OPINION, "TVOC rule disabled")

        tvoc_max = settings.get("tvoc_max", 500)
        if reading.tvoc > tvoc_max:
            return RuleResult(
                self.name,
                FanAction.ON,
                f"TVOC {reading.tvoc} ppb exceeds max {tvoc_max} ppb",
            )
        return RuleResult(
            self.name,
            FanAction.NO_OPINION,
            f"TVOC {reading.tvoc} ppb within range (max {tvoc_max} ppb)",
        )


class HumidityRule(FanRule):
    @property
    def name(self) -> str:
        return "humidity"

    @property
    def description(self) -> str:
        return "Activates the fan when humidity exceeds the configured maximum."

    def evaluate(self, reading: SensorReading, settings: dict) -> RuleResult:
        if not settings.get("humidity_enabled", False):
            return RuleResult(self.name, FanAction.NO_OPINION, "Humidity rule disabled")

        humidity_max = settings.get("humidity_max", 70.0)
        if reading.humidity > humidity_max:
            return RuleResult(
                self.name,
                FanAction.ON,
                f"Humidity {reading.humidity:.1f}% exceeds max {humidity_max}%",
            )
        return RuleResult(
            self.name,
            FanAction.NO_OPINION,
            f"Humidity {reading.humidity:.1f}% within range (max {humidity_max}%)",
        )


class PM25Rule(FanRule):
    @property
    def name(self) -> str:
        return "pm25"

    @property
    def description(self) -> str:
        return "Activates the fan when PM2.5 exceeds the configured maximum."

    def evaluate(self, reading: SensorReading, settings: dict) -> RuleResult:
        if not settings.get("pm25_enabled", False):
            return RuleResult(self.name, FanAction.NO_OPINION, "PM2.5 rule disabled")

        if reading.pm2_5 is None:
            return RuleResult(self.name, FanAction.NO_OPINION, "PM2.5 sensor not available")

        pm25_max = settings.get("pm25_max", 25.0)
        if reading.pm2_5 > pm25_max:
            return RuleResult(
                self.name,
                FanAction.ON,
                f"PM2.5 {reading.pm2_5:.1f} µg/m³ exceeds max {pm25_max} µg/m³",
            )
        return RuleResult(
            self.name,
            FanAction.NO_OPINION,
            f"PM2.5 {reading.pm2_5:.1f} µg/m³ within range (max {pm25_max} µg/m³)",
        )


# ── Controller ──────────────────────────────────────────────────────────────


class FanController:
    """Aggregates registered rules and decides the fan state.

    Decision policy: if **any** enabled rule votes ON the fan turns on
    (safety-first / OR-logic).  The controller also stores the most recent
    evaluation so the UI can display a live explanation.
    """

    def __init__(self) -> None:
        self._rules: list[FanRule] = []

    # -- Rule management -----------------------------------------------------

    def register(self, rule: FanRule) -> None:
        self._rules.append(rule)

    @property
    def rules(self) -> Sequence[FanRule]:
        return list(self._rules)

    # -- Evaluation ----------------------------------------------------------

    def evaluate(
        self, reading: SensorReading, settings: dict
    ) -> tuple[str, list[RuleResult]]:
        """Evaluate all rules and return ``(fan_action, results)``.

        ``fan_action`` is ``"on"`` or ``"off"``.
        ``results`` contains one :class:`RuleResult` per registered rule.
        """
        results = [rule.evaluate(reading, settings) for rule in self._rules]
        if any(r.action == FanAction.ON for r in results):
            return "on", results
        return "off", results


# ── Factory ─────────────────────────────────────────────────────────────────


def build_default_controller() -> FanController:
    """Create a controller pre-loaded with the standard rule set."""
    ctrl = FanController()
    ctrl.register(TemperatureRule())
    ctrl.register(TVOCRule())
    ctrl.register(HumidityRule())
    ctrl.register(PM25Rule())
    return ctrl

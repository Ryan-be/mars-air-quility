"""RuleEngine: load declarative YAML rules and evaluate against FeatureVector."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import rule_engine
import yaml

from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)


@dataclasses.dataclass
class RuleMatch:
    """A rule that fired during evaluation."""

    rule_id: str
    event_type: str
    severity: str
    confidence: float
    dedupe_hours: int
    title: str
    description: str
    action: str


class _FormatCtx(dict):
    """dict subclass for str.format_map() that converts None to 0.

    Prevents TypeError when a FeatureVector field is None and the template
    contains a format spec like {tvoc_current:.0f}.
    """

    def __getitem__(self, key: str) -> Any:
        val = super().__getitem__(key) if key in self else None
        return val if val is not None else 0

    def __missing__(self, key: str) -> Any:
        return 0


class RuleEngine:
    """Evaluates declarative YAML rules against a FeatureVector.

    Rules are stored in config/rules.yaml. Each rule has a rule-engine
    boolean expression evaluated against the FeatureVector as a flat dict.
    Comparisons against None fields return False (rule does not fire).
    """

    def __init__(self, rules_path: str | Path) -> None:
        self._rules_path = Path(rules_path)
        self._rules: list[dict] = []
        self._compiled: list[tuple[dict, rule_engine.Rule]] = []
        self.load()

    def load(self) -> None:
        """Load (or reload) rules from the YAML file. Safe to call at runtime."""
        with open(self._rules_path) as f:
            data = yaml.safe_load(f)
        self._rules = data.get("rules", [])
        self._compiled = []
        for rule_def in self._rules:
            try:
                compiled = rule_engine.Rule(rule_def["expression"])
                self._compiled.append((rule_def, compiled))
            except Exception as exc:
                log.error(
                    "RuleEngine: failed to compile rule %r: %s",
                    rule_def.get("id", "<unknown>"),
                    exc,
                )

    def reload(self) -> None:
        """Thread-safe hot-reload: re-read YAML and recompile rules.

        Acquires the shared yaml_lock before reading so an in-progress
        atomic_write from the API handler cannot race with this read.
        If the YAML is malformed the error propagates to the caller;
        the caller (API route) should catch and return HTTP 500.
        """
        from mlss_monitor.yaml_io import yaml_lock
        with yaml_lock:
            self.load()
        log.info("RuleEngine: reloaded %d rules from %s", len(self._rules), self._rules_path.name)

    def evaluate(self, fv: FeatureVector) -> list[RuleMatch]:
        """Evaluate all loaded rules against the FeatureVector.

        Returns one RuleMatch per rule that fires.
        Rules referencing None FeatureVector fields will not fire
        (rule-engine treats null comparisons as false).
        """
        fv_dict = dataclasses.asdict(fv)
        ctx = _FormatCtx(fv_dict)
        matches: list[RuleMatch] = []

        for rule_def, compiled in self._compiled:
            try:
                if not compiled.matches(fv_dict):
                    continue
                title = rule_def["title_template"].format_map(ctx)
                description = rule_def["description_template"].format_map(ctx)
                matches.append(
                    RuleMatch(
                        rule_id=rule_def["id"],
                        event_type=rule_def["event_type"],
                        severity=rule_def["severity"],
                        confidence=float(rule_def["confidence"]),
                        dedupe_hours=int(rule_def.get("dedupe_hours", 1)),
                        title=title.strip(),
                        description=description.strip(),
                        action=rule_def.get("action", "").strip(),
                    )
                )
            except Exception as exc:
                log.debug(
                    "RuleEngine: rule %r evaluation error: %s",
                    rule_def.get("id", "<unknown>"),
                    exc,
                )

        return matches

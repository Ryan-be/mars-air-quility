"""Tests for RuleEngine.reload()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_rules(path: Path, rules: list[dict]) -> None:
    path.write_text(yaml.dump({"rules": rules}))


def test_reload_picks_up_new_rule(tmp_path):
    """reload() re-reads YAML so a newly added rule becomes active."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "rule_a",
            "expression": "tvoc_current > 100",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.8,
            "title_template": "TVOC high",
            "description_template": "TVOC is {tvoc_current:.0f}",
            "action": "Ventilate",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    # Add a second rule to the YAML file
    _write_rules(rules_path, [
        {
            "id": "rule_a",
            "expression": "tvoc_current > 100",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.8,
            "title_template": "TVOC high",
            "description_template": "TVOC is {tvoc_current:.0f}",
            "action": "Ventilate",
        },
        {
            "id": "rule_b",
            "expression": "eco2_current > 1000",
            "event_type": "eco2_elevated",
            "severity": "warning",
            "confidence": 0.9,
            "title_template": "CO2 elevated",
            "description_template": "CO2 is {eco2_current:.0f}",
            "action": "Ventilate",
        },
    ])
    engine.reload()
    assert len(engine._rules) == 2
    assert engine._rules[1]["id"] == "rule_b"


def test_reload_removes_deleted_rule(tmp_path):
    """reload() reflects deletions: rules removed from YAML stop firing."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "to_delete",
            "expression": "tvoc_current > 50",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.7,
            "title_template": "T",
            "description_template": "D",
            "action": "A",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    _write_rules(rules_path, [])
    engine.reload()
    assert len(engine._rules) == 0
    assert len(engine._compiled) == 0


def test_reload_bad_yaml_leaves_previous_rules_intact(tmp_path):
    """reload() on a corrupt YAML file logs the error and keeps old rules."""
    from mlss_monitor.threshold_engine import RuleEngine

    rules_path = tmp_path / "rules.yaml"
    _write_rules(rules_path, [
        {
            "id": "stable",
            "expression": "tvoc_current > 50",
            "event_type": "tvoc_spike",
            "severity": "warning",
            "confidence": 0.7,
            "title_template": "T",
            "description_template": "D",
            "action": "A",
        }
    ])
    engine = RuleEngine(rules_path)
    assert len(engine._rules) == 1

    rules_path.write_text("{{{{ invalid yaml ::::")
    try:
        engine.reload()
    except Exception:
        pass  # acceptable — important thing is rules not silently zeroed
    # Old rules still loaded from before the corrupt write
    assert len(engine._rules) == 1

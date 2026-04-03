"""Tests for RuleEngine: YAML loading, rule evaluation against FeatureVector."""
from __future__ import annotations

import dataclasses
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.rule_engine import RuleEngine, RuleMatch


def _make_fv(**kwargs) -> FeatureVector:
    """Build a minimal FeatureVector with the given field values."""
    return FeatureVector(timestamp=datetime.now(timezone.utc), **kwargs)


def _write_rules_yaml(tmp_path: Path, rules_yaml: str) -> Path:
    p = tmp_path / "rules.yaml"
    p.write_text(rules_yaml)
    return p


# ── RuleEngine loading ────────────────────────────────────────────────────────

def test_rule_engine_loads_rules(tmp_path):
    yaml_text = """
rules:
  - id: test_rule
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "TVOC high ({tvoc_current:.0f} ppb)"
    description_template: "TVOC is {tvoc_current:.0f} ppb."
    action: "Ventilate."
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    assert len(engine._compiled) == 1


def test_rule_engine_skips_malformed_expression(tmp_path):
    yaml_text = """
rules:
  - id: bad_rule
    expression: "%%% invalid %%%"
    event_type: bad
    severity: warning
    dedupe_hours: 1
    confidence: 0.5
    title_template: "Bad"
    description_template: "Bad rule."
    action: "None."
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)  # should not raise
    assert len(engine._compiled) == 0  # skipped


def test_rule_engine_reload(tmp_path):
    yaml_text = """
rules:
  - id: rule_one
    expression: "tvoc_current > 50"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.7
    title_template: "T"
    description_template: "D"
    action: "A"
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    assert len(engine._compiled) == 1

    # Overwrite with two rules
    path.write_text(yaml_text + """
  - id: rule_two
    expression: "eco2_current > 1000"
    event_type: eco2_elevated
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "E"
    description_template: "E desc"
    action: "A"
""")
    engine.load()
    assert len(engine._compiled) == 2


# ── evaluate() ───────────────────────────────────────────────────────────────

def test_evaluate_fires_when_condition_met(tmp_path):
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "TVOC {tvoc_current:.0f} ppb"
    description_template: "TVOC is {tvoc_current:.0f} ppb above baseline."
    action: "Ventilate."
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv(tvoc_current=200.0, tvoc_baseline=100.0)
    matches = engine.evaluate(fv)
    assert len(matches) == 1
    assert matches[0].event_type == "tvoc_spike"
    assert matches[0].severity == "warning"
    assert matches[0].confidence == pytest.approx(0.8)


def test_evaluate_does_not_fire_when_condition_not_met(tmp_path):
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "TVOC {tvoc_current:.0f}"
    description_template: "TVOC {tvoc_current:.0f}"
    action: "A"
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv(tvoc_current=50.0)
    assert engine.evaluate(fv) == []


def test_evaluate_does_not_fire_when_field_is_none(tmp_path):
    """Rules referencing None fields must not fire (sensor has no data)."""
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "T"
    description_template: "D"
    action: "A"
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv()  # all fields None
    assert engine.evaluate(fv) == []


def test_evaluate_renders_title_and_description(tmp_path):
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "TVOC spike ({tvoc_current:.0f} ppb)"
    description_template: "TVOC is {tvoc_current:.0f} ppb, {tvoc_peak_ratio:.1f}x baseline."
    action: "Ventilate."
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv(tvoc_current=300.0, tvoc_peak_ratio=1.5)
    matches = engine.evaluate(fv)
    assert len(matches) == 1
    assert "300" in matches[0].title
    assert "1.5" in matches[0].description


def test_evaluate_multiple_rules_both_fire(tmp_path):
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "T"
    description_template: "D"
    action: "A"
  - id: eco2_test
    expression: "eco2_current > 1000"
    event_type: eco2_elevated
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "E"
    description_template: "E"
    action: "A"
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv(tvoc_current=200.0, eco2_current=1200.0)
    matches = engine.evaluate(fv)
    assert len(matches) == 2
    event_types = {m.event_type for m in matches}
    assert event_types == {"tvoc_spike", "eco2_elevated"}


def test_evaluate_returns_ruleMatch_dataclass(tmp_path):
    yaml_text = """
rules:
  - id: tvoc_test
    expression: "tvoc_current > 100"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 2
    confidence: 0.9
    title_template: "T"
    description_template: "D"
    action: "Act now."
"""
    path = _write_rules_yaml(tmp_path, yaml_text)
    engine = RuleEngine(path)
    fv = _make_fv(tvoc_current=200.0)
    matches = engine.evaluate(fv)
    m = matches[0]
    assert isinstance(m, RuleMatch)
    assert m.rule_id == "tvoc_test"
    assert m.dedupe_hours == 2
    assert m.action == "Act now."

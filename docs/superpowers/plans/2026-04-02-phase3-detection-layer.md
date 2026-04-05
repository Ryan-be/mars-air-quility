# Phase 3: Detection Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the if/else threshold logic in `inference_engine.py` with declarative YAML rules (rule-engine) + streaming anomaly detection (river), running in shadow/dry-run mode alongside the existing engine.

**Architecture:** `DetectionEngine` wraps a `RuleEngine` (evaluates `config/rules.yaml` expressions against the `FeatureVector`) and an `AnomalyDetector` (per-channel river `HalfSpaceTrees`, pickled to disk). Both consume `state.feature_vector` every 60s. In Phase 3 the engine runs with `dry_run=True` — it evaluates, logs what it would fire, but does **not** call `save_inference`. The old `run_analysis()` continues unchanged for comparison. Summary functions (_hourly_summary, _daily_summary, _detect_daily_patterns, _overnight_buildup) are moved into `DetectionEngine` and refactored to accept a `FeatureVector` for current-state values.

**Tech Stack:** Python 3.11, `rule-engine` (pip), `river` (pip), `PyYAML` (pip), SQLite via existing `database/db_logger.py`, `pytest`.

---

## Context (read before touching any file)

**Branch:** `claude/zealous-hugle`

**Phase 2 deliverables (complete, do not modify):**
- `mlss_monitor/feature_vector.py` — `FeatureVector` dataclass (85 fields + timestamp)
- `mlss_monitor/feature_extractor.py` — `FeatureExtractor.extract(hot_readings, baselines) → FeatureVector`
- `mlss_monitor/state.py` — `state.feature_vector` is set every 60s in `_background_log`
- `database/db_logger.py` — `get_24h_baselines()`, `get_recent_inference_by_type()`, `save_inference()`

**Existing inference engine (do NOT modify or remove in Phase 3):**
- `mlss_monitor/inference_engine.py` — `run_analysis()` calls all threshold detectors; `_hourly_summary`, `_daily_summary`, `_detect_daily_patterns`, `_overnight_buildup` are long-term summary functions.
- `app.py` calls `run_analysis()` at `_CYCLE_60S`, hourly summaries at `_CYCLE_1H`, daily summaries at `_CYCLE_24H`.
- **Keep the existing engine running.** Phase 3 adds the new engine in shadow mode only.

**FeatureVector field names (CRITICAL — not DB column names):**
```
tvoc_current, tvoc_baseline, tvoc_slope_1m, tvoc_slope_5m, tvoc_slope_30m,
tvoc_elevated_minutes, tvoc_peak_ratio, tvoc_is_declining, tvoc_decay_rate, tvoc_pulse_detected
(same 10-field pattern for: eco2, temperature, humidity, pm25, co, no2, nh3)
Cross-sensor: nh3_lag_behind_tvoc_seconds, pm25_correlated_with_tvoc, co_correlated_with_tvoc
Derived: vpd_kpa
Required: timestamp
```

⚠️ **The spec shows `temperature_c` in rule examples — this is a typo. Use `temperature_current` everywhere.**

⚠️ **PM10 is not in `SENSOR_FIELDS` and has no FeatureVector fields. The `pm10_elevated` detector cannot be expressed as a YAML rule in Phase 3 — it stays in the old engine only.**

**`save_inference` signature (from db_logger.py):**
```python
save_inference(event_type, severity, title, description, action,
               evidence, confidence, start_id=None, end_id=None, annotation=None)
```
`start_id`, `end_id`, `annotation` are optional — the new engine omits them.

**app.py cycle cadences (already defined):**
```python
_CYCLE_60S  = max(1, 60 // LOG_INTERVAL)    # short-term detectors
_CYCLE_1H   = max(1, 3600 // LOG_INTERVAL)  # hourly summary
_CYCLE_24H  = max(1, 86400 // LOG_INTERVAL) # daily summary + patterns
```

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `requirements.txt` (if exists) or note pip installs | Add rule-engine, river, pyyaml |
| Create | `config/rules.yaml` | 15 declarative threshold rules (all event types except pm10_elevated and summaries) |
| Create | `config/anomaly.yaml` | River HalfSpaceTrees config: threshold, cold_start, channels |
| Create | `mlss_monitor/rule_engine.py` | `RuleMatch` dataclass + `RuleEngine` class |
| Create | `mlss_monitor/anomaly_detector.py` | `AnomalyDetector`: per-channel HST, pickle persistence |
| Create | `mlss_monitor/detection_engine.py` | `DetectionEngine`: orchestrate rule + anomaly; summary functions |
| Modify | `mlss_monitor/app.py` | Wire `DetectionEngine` in shadow mode at all three cycle cadences |
| Create | `scripts/migrate_categories.py` | Update `event_category()` mapping in inference_engine.py + idempotent script |
| Create | `tests/test_rule_engine.py` | Unit tests for `RuleEngine` |
| Create | `tests/test_anomaly_detector.py` | Unit tests for `AnomalyDetector` |
| Create | `tests/test_detection_engine.py` | Integration tests for `DetectionEngine` |

---

## Task 1: YAML configs

**Files:**
- Create: `config/rules.yaml`
- Create: `config/anomaly.yaml`

### Step 1.1 — Check and update dependencies

- [ ] Check if `requirements.txt` exists in the project root. If it does, add `rule-engine`, `river`, and `pyyaml` if not already present. If no `requirements.txt` exists, note these three packages must be installed.

Run: `pip install rule-engine river pyyaml`
Expected: all three install successfully on the Raspberry Pi.

Run: `python -c "import rule_engine; import river; import yaml; print('OK')`
Expected: `OK`

### Step 1.2 — Create `config/` directory and `config/rules.yaml`

- [ ] Create the `config/` directory at the project root (same level as `mlss_monitor/`).

- [ ] Create `config/rules.yaml` with the following content exactly:

```yaml
# config/rules.yaml
# Declarative threshold rules evaluated against FeatureVector every 60s.
#
# expression: rule-engine boolean expression. All fields map to FeatureVector field names.
#   - Comparisons against None fields return False (rule does not fire).
#   - Use temperature_current NOT temperature_c (spec typo).
#
# dedupe_hours: suppress re-firing for this many hours after first fire.

rules:

  # ── TVOC ──────────────────────────────────────────────────────────────────────

  - id: tvoc_spike
    expression: "tvoc_peak_ratio > 2.0 and tvoc_current > 250"
    event_type: tvoc_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "TVOC spike detected ({tvoc_current:.0f} ppb)"
    description_template: >
      TVOC has risen to {tvoc_current:.0f} ppb, {tvoc_peak_ratio:.1f}×
      above the {tvoc_baseline:.0f} ppb 24-hour baseline. The sensor has been
      elevated for {tvoc_elevated_minutes:.1f} minutes.
    action: >
      Open a window or turn on ventilation. If you can identify the source
      (e.g. cleaning spray, cooking), remove or contain it. Levels should
      return to baseline within 15–60 minutes with adequate airflow.

  # ── eCO2 ──────────────────────────────────────────────────────────────────────

  - id: eco2_danger
    expression: "eco2_current >= 2000"
    event_type: eco2_danger
    severity: critical
    dedupe_hours: 1
    confidence: 0.95
    title_template: "Dangerous CO₂ level ({eco2_current:.0f} ppm)"
    description_template: >
      eCO₂ has reached {eco2_current:.0f} ppm, above the 2000 ppm danger
      threshold. Above this level, headaches, drowsiness, and significant
      cognitive impairment are likely. Baseline is {eco2_baseline:.0f} ppm.
    action: "Ventilate immediately — open windows and doors. Leave the room if symptoms appear."

  - id: eco2_elevated
    expression: "eco2_current >= 1000 and eco2_current < 2000"
    event_type: eco2_elevated
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "CO₂ elevated ({eco2_current:.0f} ppm)"
    description_template: >
      eCO₂ has reached {eco2_current:.0f} ppm. Above 1000 ppm, studies show
      measurable decline in decision-making and concentration. The room
      likely needs better ventilation.
    action: "Open a window or activate the fan. Consider taking a break in fresh air."

  # ── Temperature ───────────────────────────────────────────────────────────────

  - id: temp_high
    expression: "temperature_current > 28.0"
    event_type: temp_high
    severity: warning
    dedupe_hours: 2
    confidence: 0.85
    title_template: "Temperature high ({temperature_current:.1f}°C)"
    description_template: >
      Temperature is {temperature_current:.1f}°C, above the 28°C comfort
      threshold. This can stress plants and reduce cognitive performance.
    action: "Improve ventilation or use cooling. Check if heat sources (lights, equipment) can be reduced."

  - id: temp_low
    expression: "temperature_current < 15.0"
    event_type: temp_low
    severity: warning
    dedupe_hours: 2
    confidence: 0.85
    title_template: "Temperature low ({temperature_current:.1f}°C)"
    description_template: >
      Temperature is {temperature_current:.1f}°C, below the 15°C threshold.
      Low temperatures slow plant growth and can be uncomfortable for occupants.
    action: "Consider heating the space or reducing ventilation to retain warmth."

  # ── Humidity ──────────────────────────────────────────────────────────────────

  - id: humidity_high
    expression: "humidity_current > 70.0"
    event_type: humidity_high
    severity: warning
    dedupe_hours: 2
    confidence: 0.8
    title_template: "Humidity high ({humidity_current:.0f}% RH)"
    description_template: >
      Humidity is {humidity_current:.0f}%, above the 70% threshold. High
      humidity promotes mould growth and dust mites. Combined with warm
      temperatures this creates ideal conditions for fungal issues.
    action: "Increase ventilation or use a dehumidifier. Check for water leaks or standing water."

  - id: humidity_low
    expression: "humidity_current < 30.0"
    event_type: humidity_low
    severity: warning
    dedupe_hours: 2
    confidence: 0.8
    title_template: "Humidity low ({humidity_current:.0f}% RH)"
    description_template: >
      Humidity is {humidity_current:.0f}%, below the 30% threshold. Low
      humidity causes dry skin, irritated airways, and static electricity.
      Plants may show leaf curling and wilting.
    action: "Use a humidifier, place water trays near heat sources, or mist plants."

  # ── VPD ───────────────────────────────────────────────────────────────────────

  - id: vpd_low
    expression: "vpd_kpa < 0.4"
    event_type: vpd_low
    severity: warning
    dedupe_hours: 2
    confidence: 0.75
    title_template: "VPD too low ({vpd_kpa:.2f} kPa)"
    description_template: >
      VPD is {vpd_kpa:.2f} kPa. Below 0.4 kPa the air is nearly saturated,
      slowing transpiration and creating conditions for mould, powdery mildew,
      and root rot.
    action: "Increase temperature or decrease humidity. Improve air circulation around plants."

  - id: vpd_high
    expression: "vpd_kpa > 1.6"
    event_type: vpd_high
    severity: warning
    dedupe_hours: 2
    confidence: 0.75
    title_template: "VPD too high ({vpd_kpa:.2f} kPa)"
    description_template: >
      VPD is {vpd_kpa:.2f} kPa. Above 1.6 kPa plants close stomata to
      conserve water, halting photosynthesis and causing leaf tip burn
      and wilting.
    action: "Increase humidity (misting, humidifier) or reduce temperature. Avoid direct heat on plants."

  # ── Mould risk ────────────────────────────────────────────────────────────────

  - id: mould_risk
    expression: "humidity_elevated_minutes > 240 and humidity_current > 70.0 and temperature_current > 20.0"
    event_type: mould_risk
    severity: warning
    dedupe_hours: 6
    confidence: 0.75
    title_template: "Mould risk ({humidity_current:.0f}% RH for {humidity_elevated_minutes:.0f} min)"
    description_template: >
      Humidity has been above baseline for {humidity_elevated_minutes:.0f} minutes
      at {temperature_current:.1f}°C with current reading of {humidity_current:.0f}%.
      Sustained warm, humid conditions favour mould growth, particularly
      Aspergillus and Cladosporium species.
    action: >
      Reduce humidity urgently: run a dehumidifier, increase ventilation, and check
      for water sources (leaks, standing water, drying clothes). Inspect corners,
      window frames, and behind furniture for early mould. Target humidity below 60%.

  # ── Correlated pollution ──────────────────────────────────────────────────────

  - id: correlated_pollution
    expression: "tvoc_slope_5m > 0 and eco2_slope_5m > 0 and tvoc_current > 150 and eco2_current > 800"
    event_type: correlated_pollution
    severity: warning
    dedupe_hours: 2
    confidence: 0.7
    title_template: "TVOC and CO₂ rising together ({tvoc_current:.0f} ppb / {eco2_current:.0f} ppm)"
    description_template: >
      Both TVOC ({tvoc_current:.0f} ppb, slope {tvoc_slope_5m:.1f} ppb/min) and
      eCO₂ ({eco2_current:.0f} ppm, slope {eco2_slope_5m:.1f} ppm/min) are rising
      simultaneously. Correlated rises suggest a common source producing both volatile
      organics and carbon dioxide, such as combustion or heavy biological activity.
    action: >
      Identify and remove the source. Ventilate the space. If cooking, use an extractor
      hood. If people-related, increase ventilation rate.

  # ── Rapid changes ─────────────────────────────────────────────────────────────

  - id: rapid_temp_change
    expression: "temperature_slope_1m > 0.5 or temperature_slope_1m < -0.5"
    event_type: rapid_temp_change
    severity: warning
    dedupe_hours: 1
    confidence: 0.7
    title_template: "Rapid temperature change ({temperature_slope_1m:+.2f}°C/min)"
    description_template: >
      Temperature is changing at {temperature_slope_1m:+.2f}°C/min from a current
      reading of {temperature_current:.1f}°C. Rapid swings can stress plants and
      indicate drafts, intermittent heating, or a door/window being opened.
    action: "Check for drafts, open doors/windows, or thermostat cycling. Identify the source of the sudden change."

  - id: rapid_humidity_change
    expression: "humidity_slope_1m > 2.5 or humidity_slope_1m < -2.5"
    event_type: rapid_humidity_change
    severity: warning
    dedupe_hours: 1
    confidence: 0.65
    title_template: "Rapid humidity change ({humidity_slope_1m:+.1f}%/min)"
    description_template: >
      Humidity is changing at {humidity_slope_1m:+.1f}%/min from a current reading
      of {humidity_current:.0f}%. Rapid humidity swings can stress plants and indicate
      ventilation changes, shower/cooking steam, or humidifier cycling.
    action: "Check if a humidifier, shower, or cooking activity is the cause."

  # ── Sustained poor air ────────────────────────────────────────────────────────

  - id: sustained_poor_air
    expression: "tvoc_elevated_minutes > 10 and eco2_elevated_minutes > 10"
    event_type: sustained_poor_air
    severity: warning
    dedupe_hours: 3
    confidence: 0.85
    title_template: "Sustained poor air quality ({tvoc_elevated_minutes:.0f} min above baseline)"
    description_template: >
      Both TVOC ({tvoc_current:.0f} ppb) and eCO₂ ({eco2_current:.0f} ppm) have
      been continuously above their 24-hour baselines for over {tvoc_elevated_minutes:.0f}
      minutes. This is not a spike — it suggests a persistent source or insufficient
      ventilation.
    action: >
      This needs more than just opening a window briefly. Consider running the fan
      continuously, identifying a persistent VOC source (new furniture, paint, carpet),
      or increasing the room's base ventilation rate.

  # ── PM2.5 ─────────────────────────────────────────────────────────────────────

  - id: pm25_spike
    expression: "pm25_peak_ratio > 3.0 and pm25_current > 12.0"
    event_type: pm25_spike
    severity: warning
    dedupe_hours: 1
    confidence: 0.8
    title_template: "PM2.5 spike ({pm25_current:.0f} µg/m³)"
    description_template: >
      PM2.5 has reached {pm25_current:.0f} µg/m³, {pm25_peak_ratio:.1f}×
      above the {pm25_baseline:.0f} µg/m³ baseline. Common causes include
      cooking (especially frying), candles, incense, or a nearby dust disturbance.
    action: >
      Increase ventilation immediately. If cooking is the source, use the extractor
      fan and open a window. Levels should return to baseline within 15–30 minutes
      with adequate airflow.

  - id: pm25_elevated
    expression: "pm25_current > 12.0 and pm25_elevated_minutes > 20"
    event_type: pm25_elevated
    severity: warning
    dedupe_hours: 2
    confidence: 0.75
    title_template: "PM2.5 elevated ({pm25_current:.0f} µg/m³ for {pm25_elevated_minutes:.0f} min)"
    description_template: >
      PM2.5 has been continuously above baseline for {pm25_elevated_minutes:.0f} minutes,
      currently reading {pm25_current:.0f} µg/m³. Sustained elevated fine particles
      affect respiratory health over time.
    action: >
      Check for ongoing combustion sources (candles, incense, gas cooking). If outdoor
      air quality is poor, keep windows closed and run ventilation with a HEPA filter
      if available.
```

### Step 1.3 — Create `config/anomaly.yaml`

- [ ] Create `config/anomaly.yaml`:

```yaml
# config/anomaly.yaml
# River HalfSpaceTrees anomaly detection configuration.
# One model per channel, trained on cold-tier write cycles (every 60s).

anomaly:
  algorithm: half_space_trees
  score_threshold: 0.7          # score > threshold → anomaly event
  cold_start_readings: 1440     # suppress scores for first 24h (1440 × 60s)
  model_dir: data/anomaly_models  # relative to project root
  channels:
    - tvoc_ppb
    - eco2_ppm
    - temperature_c
    - humidity_pct
    - pm25_ug_m3
    - co_ppb       # sparse until MICS6814 has enough history
    - no2_ppb
    - nh3_ppb
```

### Step 1.4 — Verify configs are valid YAML

- [ ] Run:
```bash
python -c "import yaml; yaml.safe_load(open('config/rules.yaml')); print('rules.yaml OK')"
python -c "import yaml; yaml.safe_load(open('config/anomaly.yaml')); print('anomaly.yaml OK')"
```
Expected: both print `OK`.

### Step 1.5 — Commit

```bash
git add config/rules.yaml config/anomaly.yaml
git commit -m "feat: add rules.yaml (15 threshold rules) and anomaly.yaml for Phase 3"
```

---

## Task 2: `RuleEngine` module

**Files:**
- Create: `mlss_monitor/rule_engine.py`
- Create: `tests/test_rule_engine.py`

### Step 2.1 — Write failing tests

- [ ] Create `tests/test_rule_engine.py`:

```python
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
```

- [ ] Run: `python -m pytest tests/test_rule_engine.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_monitor.rule_engine'`

### Step 2.2 — Create `mlss_monitor/rule_engine.py`

- [ ] Create `mlss_monitor/rule_engine.py`:

```python
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
```

### Step 2.3 — Run tests

- [ ] Run: `python -m pytest tests/test_rule_engine.py -v`
  Expected: all tests PASS.

### Step 2.4 — Run full suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all existing tests still PASS, no regressions.

### Step 2.5 — Commit

```bash
git add mlss_monitor/rule_engine.py tests/test_rule_engine.py
git commit -m "feat: add RuleEngine — YAML rule loading and FeatureVector evaluation"
```

---

## Task 3: `AnomalyDetector` module

**Files:**
- Create: `mlss_monitor/anomaly_detector.py`
- Create: `tests/test_anomaly_detector.py`

### Step 3.1 — Write failing tests

- [ ] Create `tests/test_anomaly_detector.py`:

```python
"""Tests for AnomalyDetector: river HalfSpaceTrees, scoring, persistence."""
from __future__ import annotations

import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.anomaly_detector import AnomalyDetector


def _make_fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=datetime.now(timezone.utc), **kwargs)


def _write_config(tmp_path: Path) -> Path:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,  # small for tests
            "model_dir": str(tmp_path / "models"),
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(cfg))
    return p


# ── Initialisation ────────────────────────────────────────────────────────────

def test_anomaly_detector_creates_model_dir(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    AnomalyDetector(cfg_path, model_dir)
    assert model_dir.exists()


def test_anomaly_detector_initialises_models_for_channels(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)
    assert "tvoc_ppb" in det._models
    assert "eco2_ppm" in det._models


# ── learn_and_score ───────────────────────────────────────────────────────────

def test_learn_and_score_returns_none_during_cold_start(tmp_path):
    """Scores must be None until cold_start_readings threshold is reached."""
    cfg_path = _write_config(tmp_path)  # cold_start = 5
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    # Call 4 times (below cold_start=5)
    for _ in range(4):
        scores = det.learn_and_score(fv)
    assert scores["tvoc_ppb"] is None
    assert scores["eco2_ppm"] is None


def test_learn_and_score_returns_float_after_cold_start(tmp_path):
    """After cold_start_readings, scores are floats between 0 and 1."""
    cfg_path = _write_config(tmp_path)  # cold_start = 5
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    scores = None
    for _ in range(6):  # past cold_start=5
        scores = det.learn_and_score(fv)

    assert scores is not None
    assert isinstance(scores["tvoc_ppb"], float)
    assert 0.0 <= scores["tvoc_ppb"] <= 1.0


def test_learn_and_score_returns_none_for_none_field(tmp_path):
    """If a FeatureVector field is None, score for that channel is None."""
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=None, eco2_current=600.0)
    scores = det.learn_and_score(fv)
    assert scores["tvoc_ppb"] is None  # no value → no score


# ── anomalous_channels ────────────────────────────────────────────────────────

def test_anomalous_channels_filters_by_threshold(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    scores = {"tvoc_ppb": 0.8, "eco2_ppm": 0.3}  # threshold=0.7
    anomalous = det.anomalous_channels(scores)
    assert "tvoc_ppb" in anomalous
    assert "eco2_ppm" not in anomalous


def test_anomalous_channels_excludes_none_scores(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    scores = {"tvoc_ppb": None, "eco2_ppm": 0.9}
    anomalous = det.anomalous_channels(scores)
    assert "tvoc_ppb" not in anomalous
    assert "eco2_ppm" in anomalous


# ── Persistence ───────────────────────────────────────────────────────────────

def test_models_are_saved_and_reloaded(tmp_path):
    """After training, reloading AnomalyDetector restores n_seen."""
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    for _ in range(3):
        det.learn_and_score(fv)

    # Reload
    det2 = AnomalyDetector(cfg_path, model_dir)
    assert det2._n_seen["tvoc_ppb"] == 3
    assert det2._n_seen["eco2_ppm"] == 3
```

- [ ] Run: `python -m pytest tests/test_anomaly_detector.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_monitor.anomaly_detector'`

### Step 3.2 — Create `mlss_monitor/anomaly_detector.py`

- [ ] Create `mlss_monitor/anomaly_detector.py`:

```python
"""AnomalyDetector: per-channel river HalfSpaceTrees with pickle persistence."""
from __future__ import annotations

import logging
import pickle
from pathlib import Path

import yaml
from river.anomaly import HalfSpaceTrees

from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

# Maps anomaly config channel name → FeatureVector field name for current value.
_CHANNEL_TO_FV_FIELD: dict[str, str] = {
    "tvoc_ppb":      "tvoc_current",
    "eco2_ppm":      "eco2_current",
    "temperature_c": "temperature_current",
    "humidity_pct":  "humidity_current",
    "pm25_ug_m3":    "pm25_current",
    "co_ppb":        "co_current",
    "no2_ppb":       "no2_current",
    "nh3_ppb":       "nh3_current",
}


class AnomalyDetector:
    """Per-channel streaming anomaly detection using river HalfSpaceTrees.

    One model instance per channel. Models are persisted to disk as pickle
    files so they survive restarts and accumulate learning over time.
    Scores are suppressed (returned as None) during the cold-start period.
    """

    def __init__(self, config_path: str | Path, model_dir: str | Path) -> None:
        self._config_path = Path(config_path)
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._config: dict = {}
        self._models: dict[str, HalfSpaceTrees] = {}
        self._n_seen: dict[str, int] = {}
        self._load_config()
        self._load_models()

    def _load_config(self) -> None:
        with open(self._config_path) as f:
            self._config = yaml.safe_load(f).get("anomaly", {})

    def _channels(self) -> list[str]:
        return self._config.get("channels", list(_CHANNEL_TO_FV_FIELD.keys()))

    def _load_models(self) -> None:
        for ch in self._channels():
            model_path = self._model_dir / f"{ch}.pkl"
            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        state = pickle.load(f)
                    self._models[ch] = state["model"]
                    self._n_seen[ch] = state["n_seen"]
                    continue
                except Exception as exc:
                    log.warning("AnomalyDetector: could not load model %r: %s", ch, exc)
            self._models[ch] = HalfSpaceTrees(n_trees=25, height=15, window_size=250, seed=42)
            self._n_seen[ch] = 0

    def _save_models(self) -> None:
        for ch, model in self._models.items():
            model_path = self._model_dir / f"{ch}.pkl"
            try:
                with open(model_path, "wb") as f:
                    pickle.dump({"model": model, "n_seen": self._n_seen[ch]}, f)
            except Exception as exc:
                log.warning("AnomalyDetector: could not save model %r: %s", ch, exc)

    def learn_and_score(self, fv: FeatureVector) -> dict[str, float | None]:
        """Score then train all channel models with the current FeatureVector.

        Scores before learning so the model hasn't yet seen this point.
        Returns channel → score (0.0–1.0) or None if channel has no data
        or is in the cold-start period.
        """
        cold_start = self._config.get("cold_start_readings", 1440)
        scores: dict[str, float | None] = {}

        for ch in self._channels():
            fv_field = _CHANNEL_TO_FV_FIELD.get(ch)
            if fv_field is None:
                scores[ch] = None
                continue
            value = getattr(fv, fv_field, None)
            if value is None:
                scores[ch] = None
                continue

            x = {"value": float(value)}
            model = self._models[ch]

            try:
                raw_score = model.score_one(x)
            except Exception:
                raw_score = 0.0
            model.learn_one(x)
            self._n_seen[ch] = self._n_seen.get(ch, 0) + 1

            # Suppress during cold start
            scores[ch] = None if self._n_seen[ch] < cold_start else raw_score

        self._save_models()
        return scores

    def anomalous_channels(self, scores: dict[str, float | None]) -> list[str]:
        """Return channel names whose score exceeds the configured threshold."""
        threshold = self._config.get("score_threshold", 0.7)
        return [ch for ch, s in scores.items() if s is not None and s > threshold]
```

### Step 3.3 — Run tests

- [ ] Run: `python -m pytest tests/test_anomaly_detector.py -v`
  Expected: all tests PASS.

### Step 3.4 — Run full suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all existing tests still PASS.

### Step 3.5 — Commit

```bash
git add mlss_monitor/anomaly_detector.py tests/test_anomaly_detector.py
git commit -m "feat: add AnomalyDetector — river HalfSpaceTrees with pickle persistence"
```

---

## Task 4: `DetectionEngine` orchestrator (shadow/dry-run mode)

**Files:**
- Create: `mlss_monitor/detection_engine.py`
- Create: `tests/test_detection_engine.py`

### Step 4.1 — Write failing tests

- [ ] Create `tests/test_detection_engine.py`:

```python
"""Tests for DetectionEngine: rule + anomaly orchestration, dry-run mode."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.detection_engine import DetectionEngine


def _make_fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=datetime.now(timezone.utc), **kwargs)


def _write_minimal_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (rules_path, anomaly_path, model_dir)."""
    rules = {
        "rules": [
            {
                "id": "tvoc_test",
                "expression": "tvoc_current > 100",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "dedupe_hours": 1,
                "confidence": 0.8,
                "title_template": "TVOC {tvoc_current:.0f} ppb",
                "description_template": "TVOC is {tvoc_current:.0f} ppb.",
                "action": "Ventilate.",
            }
        ]
    }
    anomaly = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,
            "model_dir": str(tmp_path / "models"),
            "channels": ["tvoc_ppb"],
        }
    }
    rules_path = tmp_path / "rules.yaml"
    anomaly_path = tmp_path / "anomaly.yaml"
    model_dir = tmp_path / "models"
    rules_path.write_text(yaml.dump(rules))
    anomaly_path.write_text(yaml.dump(anomaly))
    return rules_path, anomaly_path, model_dir


# ── dry_run=True (shadow mode) ────────────────────────────────────────────────

def test_run_dry_run_does_not_call_save_inference(tmp_path):
    """In dry_run=True mode, save_inference must never be called."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=300.0)  # triggers tvoc_test rule
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=None):
        engine.run(fv)
        mock_save.assert_not_called()


def test_run_dry_run_returns_fired_event_types(tmp_path):
    """dry_run=True mode returns the list of event types that would fire."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=None):
        fired = engine.run(fv)
    assert "tvoc_spike" in fired


def test_run_dry_run_returns_empty_when_no_rules_fire(tmp_path):
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=50.0)  # below threshold
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=None):
        fired = engine.run(fv)
    assert "tvoc_spike" not in fired


# ── dry_run=False (live mode) ─────────────────────────────────────────────────

def test_run_live_calls_save_inference_when_rule_fires(tmp_path):
    """In dry_run=False mode, save_inference is called for each matched rule."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=False)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=None):
        engine.run(fv)
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["event_type"] == "tvoc_spike"


def test_run_live_skips_event_within_dedupe_window(tmp_path):
    """If get_recent_inference_by_type returns a result, rule must not re-fire."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=False)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type",
               return_value=[{"id": 1}]):  # already fired recently
        engine.run(fv)
        mock_save.assert_not_called()


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_run_with_none_feature_vector_does_not_raise(tmp_path):
    """Passing an empty FeatureVector (all None) must not raise."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv()  # all fields None
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=None):
        fired = engine.run(fv)
    assert fired == []
```

- [ ] Run: `python -m pytest tests/test_detection_engine.py -v`
  Expected: FAIL with `ModuleNotFoundError: No module named 'mlss_monitor.detection_engine'`

### Step 4.2 — Create `mlss_monitor/detection_engine.py`

- [ ] Create `mlss_monitor/detection_engine.py`:

```python
"""DetectionEngine: orchestrates RuleEngine + AnomalyDetector → inferences.

In dry_run=True (shadow) mode: evaluates rules and logs what would fire,
but never calls save_inference. Used during parallel validation against
the old inference_engine.

In dry_run=False (live) mode: calls save_inference for each event.
Switch mode by changing the dry_run flag in app.py once parity is confirmed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from database.db_logger import (
    DB_FILE,
    get_recent_inference_by_type,
    save_inference,
)
from mlss_monitor.anomaly_detector import AnomalyDetector
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.rule_engine import RuleEngine

log = logging.getLogger(__name__)


class DetectionEngine:
    """Orchestrates RuleEngine + AnomalyDetector.

    dry_run=True: shadow/validation mode — logs what would fire, no DB writes.
    dry_run=False: live mode — calls save_inference for each event.
    """

    def __init__(
        self,
        rules_path: str | Path,
        anomaly_config_path: str | Path,
        model_dir: str | Path,
        dry_run: bool = True,
    ) -> None:
        self._dry_run = dry_run
        self._rule_engine = RuleEngine(rules_path)
        self._anomaly_detector = AnomalyDetector(anomaly_config_path, model_dir)

    # ── Short-term detection (call at _CYCLE_60S) ─────────────────────────────

    def run(self, fv: FeatureVector) -> list[str]:
        """Evaluate threshold rules + anomaly detector against the FeatureVector.

        Returns a list of event_type strings that fired (for shadow-mode logging).
        In dry_run=True mode, never calls save_inference.
        In dry_run=False mode, calls save_inference for each new event (respects
        dedupe window via get_recent_inference_by_type).
        """
        fired: list[str] = []

        # 1. Threshold rule events
        matches = self._rule_engine.evaluate(fv)
        for match in matches:
            if get_recent_inference_by_type(match.event_type, hours=match.dedupe_hours):
                continue  # within dedupe window
            fired.append(match.event_type)
            if not self._dry_run:
                try:
                    save_inference(
                        event_type=match.event_type,
                        severity=match.severity,
                        title=match.title,
                        description=match.description,
                        action=match.action,
                        evidence={"fv_timestamp": fv.timestamp.isoformat()},
                        confidence=match.confidence,
                    )
                except Exception as exc:
                    log.error(
                        "DetectionEngine: save_inference failed for %r: %s",
                        match.event_type,
                        exc,
                    )

        # 2. Anomaly detection
        scores = self._anomaly_detector.learn_and_score(fv)
        anomalous = self._anomaly_detector.anomalous_channels(scores)
        for ch in anomalous:
            event_type = f"anomaly_{ch}"
            if get_recent_inference_by_type(event_type, hours=1):
                continue
            score = scores[ch]
            fired.append(event_type)
            if not self._dry_run:
                try:
                    save_inference(
                        event_type=event_type,
                        severity="warning",
                        title=f"Statistical anomaly: {ch.replace('_', ' ')} (score {score:.2f})",
                        description=(
                            f"A statistical anomaly was detected in {ch} with a score of "
                            f"{score:.2f} (threshold 0.7). This reading is unusual compared "
                            f"to the learned historical pattern for this sensor channel."
                        ),
                        action=(
                            "Monitor the sensor. If readings persist unusually, "
                            "investigate the cause or reset the anomaly model for this channel."
                        ),
                        evidence={"channel": ch, "anomaly_score": round(score, 4)},
                        confidence=round(score, 2),
                    )
                except Exception as exc:
                    log.error(
                        "DetectionEngine: save_inference failed for anomaly %r: %s",
                        ch,
                        exc,
                    )

        if fired:
            mode = "DRY-RUN" if self._dry_run else "LIVE"
            log.info("[DetectionEngine][%s] fired: %s", mode, fired)

        return fired

    # ── Long-term summaries (call at _CYCLE_1H / _CYCLE_24H) ─────────────────

    def run_hourly(self, fv: FeatureVector) -> None:
        """Run hourly summary — delegates to _hourly_summary. See Task 5."""
        pass  # populated in Task 5

    def run_daily(self, fv: FeatureVector) -> None:
        """Run daily summaries — delegates to _daily_summary and pattern/overnight detectors. See Task 5."""
        pass  # populated in Task 5
```

### Step 4.3 — Run tests

- [ ] Run: `python -m pytest tests/test_detection_engine.py -v`
  Expected: all tests PASS.

### Step 4.4 — Run full suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all tests PASS.

### Step 4.5 — Commit

```bash
git add mlss_monitor/detection_engine.py tests/test_detection_engine.py
git commit -m "feat: add DetectionEngine with shadow dry-run mode"
```

---

## Task 5: Summary functions in `DetectionEngine`

**Files:**
- Modify: `mlss_monitor/detection_engine.py`

These four functions currently live in `inference_engine.py`. They are moved into `DetectionEngine` as private methods, accepting `fv: FeatureVector` for current-state values. They still query the DB for historical aggregations.

**Do not modify `inference_engine.py`.** The functions remain there unchanged for the parallel run. You are adding equivalent versions to `DetectionEngine`.

### Step 5.1 — Add `_fetch_recent` helper to `detection_engine.py`

- [ ] Read `mlss_monitor/inference_engine.py` lines 119–131 for the `_fetch_recent` implementation.

- [ ] Add this private helper to `DetectionEngine` (inside the class body, after `run_daily`):

```python
    def _fetch_recent(self, minutes: int = 30) -> list[dict]:
        """Fetch sensor_data rows from the last N minutes, oldest first."""
        conn = None
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
            rows = conn.execute(
                "SELECT * FROM sensor_data WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            if conn:
                conn.close()
```

### Step 5.2 — Add `_hourly_summary` to `DetectionEngine`

- [ ] Read `mlss_monitor/inference_engine.py` lines 926–1031 for the full `_hourly_summary` implementation.

- [ ] Add `_hourly_summary` as a private method of `DetectionEngine`. The method accepts `fv: FeatureVector` and uses it for current readings where the old function used `rows[-1]`. The statistical aggregations still fetch 60 minutes of DB rows.

```python
    def _hourly_summary(self, fv: FeatureVector) -> None:
        """Analyse the last hour of data and produce a summary inference.

        Uses fv for current-state values; queries DB for historical stats.
        """
        if get_recent_inference_by_type("hourly_summary", hours=1):
            return

        rows = self._fetch_recent(minutes=60)
        if len(rows) < 20:
            return

        temps = [r["temperature"] for r in rows if r["temperature"] is not None]
        hums  = [r["humidity"]    for r in rows if r["humidity"] is not None]
        tvocs = [r["tvoc"]        for r in rows if r["tvoc"] is not None]
        eco2s = [r["eco2"]        for r in rows if r["eco2"] is not None]

        if not temps or not hums or not tvocs or not eco2s:
            return

        def _mean(vals): return sum(vals) / len(vals) if vals else 0
        def _std(vals):
            if len(vals) < 2:
                return 0
            m = _mean(vals)
            import math
            return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))

        temp_mean, temp_std = _mean(temps), _std(temps)
        hum_mean,  hum_std  = _mean(hums),  _std(hums)
        tvoc_mean            = _mean(tvocs)
        eco2_mean            = _mean(eco2s)
        tvoc_peak            = max(tvocs)
        eco2_peak            = max(eco2s)

        # Use FeatureVector for slopes (more accurate, from hot tier)
        temp_slope = fv.temperature_slope_1m or 0
        hum_slope  = fv.humidity_slope_1m  or 0
        tvoc_slope = fv.tvoc_slope_1m      or 0
        eco2_slope = fv.eco2_slope_1m      or 0

        def _trend_word(slope, threshold=0.05):
            if slope > threshold:   return "rising"
            if slope < -threshold:  return "falling"
            return "stable"

        temp_trend = _trend_word(temp_slope, 0.02)
        hum_trend  = _trend_word(hum_slope, 0.1)
        tvoc_trend = _trend_word(tvoc_slope, 0.5)
        eco2_trend = _trend_word(eco2_slope, 1.0)

        issues = []
        if tvoc_mean > 250:
            issues.append(f"avg TVOC {int(tvoc_mean)} ppb (above 250)")
        if eco2_mean > 800:
            issues.append(f"avg eCO₂ {int(eco2_mean)} ppm (above 800)")
        if temp_mean > 28.0 or temp_mean < 15.0:
            issues.append(f"avg temp {temp_mean:.1f}°C (outside 15–28°C)")
        if hum_mean > 70.0 or hum_mean < 30.0:
            issues.append(f"avg humidity {hum_mean:.0f}% (outside 30–70%)")

        sev = "warning" if len(issues) >= 2 else "info"
        quality = "Poor" if len(issues) >= 2 else "Fair" if issues else "Good"

        stability_issues = []
        if temp_std > 2.0:
            stability_issues.append(f"temperature varied ±{temp_std:.1f}°C")
        if hum_std > 8.0:
            stability_issues.append(f"humidity varied ±{hum_std:.0f}%")
        stability = ("Unstable — " + ", ".join(stability_issues)) if stability_issues else "Stable"

        desc_parts = [
            f"Over the past hour ({len(rows)} readings):",
            f"Temperature: {temp_mean:.1f}°C (±{temp_std:.1f}), {temp_trend}.",
            f"Humidity: {hum_mean:.0f}% (±{hum_std:.0f}), {hum_trend}.",
            f"TVOC: avg {int(tvoc_mean)} ppb, peak {int(tvoc_peak)} ppb, {tvoc_trend}.",
            f"eCO₂: avg {int(eco2_mean)} ppm, peak {int(eco2_peak)} ppm, {eco2_trend}.",
        ]
        if issues:
            desc_parts.append(f"Issues: {'; '.join(issues)}.")
        if stability_issues:
            desc_parts.append(f"Stability: {stability}.")

        action_str = (
            "Address the issues noted above. " + ("; ".join(issues) + "." if issues else "")
            if issues else
            "No action needed — environment is within normal ranges."
        )

        if self._dry_run:
            log.info("[DetectionEngine][DRY-RUN] Would fire: hourly_summary")
            return

        save_inference(
            event_type="hourly_summary",
            severity=sev,
            title=f"Hourly summary — {quality} air quality",
            description=" ".join(desc_parts),
            action=action_str,
            evidence={
                "period": "1 hour",
                "readings": str(len(rows)),
                "temp_avg": f"{temp_mean:.1f}°C",
                "temp_trend": temp_trend,
                "humidity_avg": f"{hum_mean:.0f}%",
                "humidity_trend": hum_trend,
                "tvoc_avg": f"{int(tvoc_mean)} ppb",
                "tvoc_peak": f"{int(tvoc_peak)} ppb",
                "tvoc_trend": tvoc_trend,
                "eco2_avg": f"{int(eco2_mean)} ppm",
                "eco2_peak": f"{int(eco2_peak)} ppm",
                "eco2_trend": eco2_trend,
                "stability": stability,
                "overall": quality,
            },
            confidence=0.9,
        )
```

### Step 5.3 — Add `_daily_summary`, `_detect_daily_patterns`, `_overnight_buildup`

- [ ] Read `mlss_monitor/inference_engine.py` lines 1036–1302 for the three daily functions.

- [ ] Add `_daily_summary`, `_detect_daily_patterns`, and `_overnight_buildup` as private methods of `DetectionEngine`, following the same pattern as `_hourly_summary` above:
  - Accept `fv: FeatureVector` as first parameter
  - Use FeatureVector slope fields where the old code computed trends from raw rows
  - Keep all DB queries for historical data
  - Guard with `if self._dry_run: log.info(...); return` before any `save_inference` call
  - Remove all references to `_t(key)` (those DB-loaded thresholds) — use hardcoded values matching `_DEFAULTS` in inference_engine.py: `tvoc_moderate=250`, `eco2_cognitive=1000`, `temp_high=28.0`, `temp_low=15.0`, `hum_high=70.0`, `hum_low=30.0`, `vpd_low=0.4`, `vpd_high=1.6`

The `_vpd_kpa` helper is also needed — add it as a module-level private function at the top of `detection_engine.py`:

```python
import math

def _vpd_kpa(temp_c: float | None, rh: float | None) -> float | None:
    if temp_c is None or rh is None or rh <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return svp * (1 - rh / 100)
```

### Step 5.4 — Wire into `run_hourly` and `run_daily`

- [ ] Replace the `pass` stubs in `run_hourly` and `run_daily` with the method calls:

```python
    def run_hourly(self, fv: FeatureVector) -> None:
        """Run hourly summary detector."""
        try:
            self._hourly_summary(fv)
        except Exception as exc:
            log.error("DetectionEngine: hourly summary error: %s", exc)

    def run_daily(self, fv: FeatureVector) -> None:
        """Run daily summary, pattern, and overnight buildup detectors."""
        try:
            self._daily_summary(fv)
        except Exception as exc:
            log.error("DetectionEngine: daily summary error: %s", exc)
        try:
            self._detect_daily_patterns(fv)
        except Exception as exc:
            log.error("DetectionEngine: daily pattern error: %s", exc)
        try:
            self._overnight_buildup(fv)
        except Exception as exc:
            log.error("DetectionEngine: overnight buildup error: %s", exc)
```

### Step 5.5 — Run full test suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all tests PASS.

### Step 5.6 — Commit

```bash
git add mlss_monitor/detection_engine.py
git commit -m "feat: add refactored summary functions to DetectionEngine (hourly/daily/patterns)"
```

---

## Task 6: Wire into `app.py` (shadow mode)

**Files:**
- Modify: `mlss_monitor/app.py`

### Step 6.1 — Read current `app.py`

- [ ] Read `mlss_monitor/app.py` to find:
  - The imports section (around lines 22–44)
  - The `_data_sources` list and `_feature_extractor` instantiation (around lines 191–199)
  - The `_background_log()` function and its three cycle blocks (around lines 410–450)

### Step 6.2 — Add imports

- [ ] Add to the existing Phase 1/2 imports block in `app.py`:

```python
from mlss_monitor.detection_engine import DetectionEngine
```

### Step 6.3 — Add module-level `_detection_engine` instance

- [ ] Add immediately after `_feature_extractor = FeatureExtractor()` (around line 198):

```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_detection_engine = DetectionEngine(
    rules_path=_PROJECT_ROOT / "config" / "rules.yaml",
    anomaly_config_path=_PROJECT_ROOT / "config" / "anomaly.yaml",
    model_dir=_PROJECT_ROOT / "data" / "anomaly_models",
    dry_run=True,  # Shadow mode: log but do not save to DB.
                   # Set to False once parity with run_analysis() is confirmed.
)
```

- [ ] Also add `from pathlib import Path` to imports if not already present.

### Step 6.4 — Wire shadow calls into `_background_log`

- [ ] Read the three `if _log_cycle % _CYCLE_*` blocks in `_background_log`.

- [ ] Add a shadow detection block immediately after the existing `if _log_cycle % _CYCLE_60S == 0` block that calls `run_analysis()`:

```python
        # Shadow mode: new DetectionEngine runs alongside run_analysis() for parallel
        # validation. dry_run=True means no DB writes. Compare log output to verify
        # parity, then flip dry_run=False once satisfied.
        if _log_cycle % _CYCLE_60S == 0:
            try:
                if state.feature_vector is not None:
                    fired = _detection_engine.run(state.feature_vector)
                    if fired:
                        log.debug("[shadow] DetectionEngine would fire: %s", fired)
            except Exception as exc:
                log.error("[shadow] DetectionEngine short-term error: %s", exc)
```

- [ ] Add shadow hourly call after the existing `if _log_cycle % _CYCLE_1H == 0` block (read the file first to see if one exists):

```python
        if _log_cycle % _CYCLE_1H == 0:
            try:
                if state.feature_vector is not None:
                    _detection_engine.run_hourly(state.feature_vector)
            except Exception as exc:
                log.error("[shadow] DetectionEngine hourly error: %s", exc)
```

- [ ] Add shadow daily call after the existing `if _log_cycle % _CYCLE_24H == 0` block:

```python
        if _log_cycle % _CYCLE_24H == 0:
            try:
                if state.feature_vector is not None:
                    _detection_engine.run_daily(state.feature_vector)
            except Exception as exc:
                log.error("[shadow] DetectionEngine daily error: %s", exc)
```

### Step 6.5 — Verify `Path` import

- [ ] Confirm `from pathlib import Path` is in `app.py` imports. Add it if missing.

### Step 6.6 — Run full test suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all tests PASS.

- [ ] Verify the detection engine initialises correctly:
```bash
python -c "
from pathlib import Path
from mlss_monitor.detection_engine import DetectionEngine
e = DetectionEngine(
    rules_path=Path('config/rules.yaml'),
    anomaly_config_path=Path('config/anomaly.yaml'),
    model_dir=Path('data/anomaly_models'),
    dry_run=True,
)
print('DetectionEngine OK, rules loaded:', len(e._rule_engine._compiled))
"
```
Expected: `DetectionEngine OK, rules loaded: 15`

### Step 6.7 — Commit

```bash
git add mlss_monitor/app.py
git commit -m "feat: wire DetectionEngine shadow mode into _background_log at all cycle cadences"
```

---

## Task 7: Category mapping update + migration script

**Files:**
- Modify: `mlss_monitor/inference_engine.py` — update `EVENT_TYPES` and `CATEGORIES`
- Create: `scripts/migrate_categories.py`

### Step 7.1 — Update `EVENT_TYPES` in `inference_engine.py`

- [ ] Read `mlss_monitor/inference_engine.py` lines 31–74 for the current `EVENT_TYPES` and `CATEGORIES` dicts.

- [ ] The spec defines these category changes from the existing mapping:

| event_type | old category | new category |
|---|---|---|
| `mould_risk` | `alert` | `warning` |
| `pm25_spike` | `alert` | `alert` (unchanged) |
| `pm25_elevated` | `alert` | `alert` (unchanged) |
| `annotation_context_*` | `other` | `pattern` |

- [ ] Update `EVENT_TYPES` in `inference_engine.py` — change `"mould_risk": "alert"` to `"mould_risk": "warning"`:

```python
EVENT_TYPES = {
    # Alerts — immediate environmental concerns
    "tvoc_spike":            "alert",
    "eco2_danger":           "alert",
    "eco2_elevated":         "alert",
    "correlated_pollution":  "alert",
    "sustained_poor_air":    "alert",
    "pm25_spike":            "alert",
    "pm25_elevated":         "alert",
    "pm10_elevated":         "warning",
    # Warnings — conditions worth addressing
    "mould_risk":            "warning",  # changed from "alert"
    "temp_high":             "warning",
    "temp_low":              "warning",
    "humidity_high":         "warning",
    "humidity_low":          "warning",
    "vpd_low":               "warning",
    "vpd_high":              "warning",
    "rapid_temp_change":     "warning",
    "rapid_humidity_change": "warning",
    # Summaries — periodic reports
    "hourly_summary":        "summary",
    "daily_summary":         "summary",
    # Patterns — detected trends and recurring behaviours
    "daily_pattern":         "pattern",
    "overnight_buildup":     "pattern",
}
```

- [ ] Add `"anomaly"` to the `CATEGORIES` dict:

```python
CATEGORIES = {
    "alert":   "Alerts",
    "warning": "Warnings",
    "anomaly": "Anomalies",   # new — for river anomaly events
    "summary": "Summaries",
    "pattern": "Patterns",
    "other":   "Other",       # retained for backwards compatibility
}
```

- [ ] Update `event_category()` to reclassify `annotation_context_*` events to `"pattern"` instead of `"other"`:

```python
def event_category(event_type: str) -> str:
    """Return the category for an event type."""
    if event_type.startswith(_ANNOTATION_PREFIX):
        return "pattern"  # was "other" — reclassified per spec
    if event_type.startswith("anomaly_"):
        return "anomaly"  # river anomaly events
    return EVENT_TYPES.get(event_type, "other")
```

### Step 7.2 — Check inference table schema

- [ ] Run:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('database/mlss.db')  # adjust path if different
print(conn.execute('PRAGMA table_info(inferences)').fetchall())
conn.close()
"
```
Look for a `category` column. If one exists, proceed to Step 7.3. If no `category` column exists, the category is computed at read time from `event_type` — skip Step 7.3 and note it in the commit message.

### Step 7.3 — Create `scripts/migrate_categories.py`

- [ ] Create `scripts/` directory if it doesn't exist.

- [ ] Create `scripts/migrate_categories.py`:

```python
#!/usr/bin/env python3
"""One-time migration: update category values in the inferences table.

Run once before Phase 3 goes live. Idempotent — safe to run multiple times.

Only needed if the inferences table has a 'category' column.
If the column does not exist, categories are computed at runtime from
event_type — this script is a no-op in that case.
"""
import sqlite3
import sys
from pathlib import Path

# Adjust this if DB_FILE is in a different location
_DEFAULT_DB = Path(__file__).parent.parent / "database" / "mlss.db"


# Mapping from event_type → new category (per spec reclassification)
RECLASSIFY = {
    "tvoc_spike":            "alert",
    "eco2_danger":           "alert",
    "eco2_elevated":         "alert",
    "correlated_pollution":  "alert",
    "sustained_poor_air":    "alert",
    "mould_risk":            "warning",   # was "alert"
    "pm25_spike":            "alert",
    "pm25_elevated":         "alert",
    "pm10_elevated":         "warning",
    "temp_high":             "warning",
    "temp_low":              "warning",
    "humidity_high":         "warning",
    "humidity_low":          "warning",
    "vpd_low":               "warning",
    "vpd_high":              "warning",
    "rapid_temp_change":     "warning",
    "rapid_humidity_change": "warning",
    "daily_pattern":         "pattern",
    "overnight_buildup":     "pattern",
    "hourly_summary":        "summary",
    "daily_summary":         "summary",
}


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path}. Nothing to migrate.")
        return

    conn = sqlite3.connect(db_path)
    try:
        # Check if 'category' column exists
        columns = [row[1] for row in conn.execute("PRAGMA table_info(inferences)").fetchall()]
        if "category" not in columns:
            print("No 'category' column in inferences table — categories are computed at runtime.")
            print("No SQL migration needed. The event_category() function update is sufficient.")
            return

        total_updated = 0
        for event_type, new_category in RECLASSIFY.items():
            result = conn.execute(
                "UPDATE inferences SET category = ? WHERE event_type = ? AND category != ?",
                (new_category, event_type, new_category),
            )
            if result.rowcount > 0:
                print(f"  {event_type}: {result.rowcount} rows → {new_category}")
                total_updated += result.rowcount

        # Reclassify annotation_context_* events to 'pattern'
        result = conn.execute(
            "UPDATE inferences SET category = 'pattern' "
            "WHERE event_type LIKE 'annotation_context_%' AND category != 'pattern'"
        )
        if result.rowcount > 0:
            print(f"  annotation_context_*: {result.rowcount} rows → pattern")
            total_updated += result.rowcount

        conn.commit()
        print(f"\nMigration complete. {total_updated} rows updated.")
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_DB
    print(f"Migrating {db_path} ...")
    migrate(db_path)
```

### Step 7.4 — Run migration script (dry run check)

- [ ] Run: `python scripts/migrate_categories.py`
  Expected: either "No 'category' column — no SQL migration needed" (if runtime-computed) or a count of updated rows.

### Step 7.5 — Run full test suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all tests PASS.

### Step 7.6 — Commit

```bash
git add mlss_monitor/inference_engine.py scripts/migrate_categories.py
git commit -m "feat: update event category mapping and add migration script for Phase 3"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Transcribe all threshold detectors to YAML | Task 1 (15 rules — pm10 excluded, no FeatureVector field) |
| rule-engine library for expression evaluation | Task 2 |
| None fields do not cause rules to fire | Task 2 (rule-engine null semantics + test) |
| river HalfSpaceTrees per channel | Task 3 |
| Model persistence (pickle, survive restarts) | Task 3 |
| Cold-start suppression (1440 readings = 24h) | Task 3 |
| DetectionEngine orchestrates both | Task 4 |
| Shadow/dry-run mode (no DB writes during parallel validation) | Task 4 |
| Dedupe window per event type | Task 4 (get_recent_inference_by_type) |
| Summary functions retain Python, accept FeatureVector | Task 5 |
| Wire into _background_log at correct cadences | Task 6 |
| DB category reclassification (mould_risk: alert→warning) | Task 7 |
| "anomaly" category for river events | Task 7 |
| annotation_context_* reclassified to "pattern" | Task 7 |
| Parallel run: old engine continues unchanged | All tasks (inference_engine.py not modified except Task 7's event_category update) |

**pm10_elevated gap:** The `pm10_elevated` detector cannot be expressed as a YAML rule — PM10 is not in `SENSOR_FIELDS` and has no `FeatureVector` field. It remains exclusively in the old `inference_engine.py`. This is documented and acceptable for Phase 3. Adding PM10 support requires a future `SENSOR_FIELDS` extension.

**Type consistency across tasks:**
- `temperature_current` used throughout rules.yaml and detection_engine.py ✓
- `FeatureVector` fields referenced in rules.yaml all exist in Phase 2 FeatureVector ✓
- `RuleMatch.dedupe_hours: int` consistent in rule_engine.py and detection_engine.py ✓
- `AnomalyDetector.learn_and_score()` → `dict[str, float | None]` used consistently ✓
- `DetectionEngine.run()` → `list[str]` (fired event types) consistent in tests ✓

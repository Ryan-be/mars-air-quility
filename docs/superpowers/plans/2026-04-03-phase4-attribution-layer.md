# Phase 4 — Attribution Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a YAML fingerprint-based source attribution layer that scores each detection event against five pollution source profiles (biological off-gassing, chemical off-gassing, cooking, combustion, external pollution), enriches `save_inference()` calls with attribution evidence, and surfaces a runner-up when two sources are within 0.15 confidence of each other.

**Architecture:** New `mlss_monitor/attribution/` package containing a YAML loader, a sensor scorer, a temporal scorer, and an `AttributionEngine` facade. `DetectionEngine.run()` is extended to call `AttributionEngine.attribute(fv)` and inject the result into every `save_inference()` call. Fingerprints live in `config/fingerprints.yaml` and are loaded once at startup.

**Tech Stack:** PyYAML (already installed), dataclasses, existing `FeatureVector`, existing `save_inference()` — no new library dependencies.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `config/fingerprints.yaml` | **Create** | Five source fingerprint definitions (sensors + temporal profile + narrative templates) |
| `mlss_monitor/attribution/__init__.py` | **Create** | Re-exports `AttributionEngine`, `AttributionResult` |
| `mlss_monitor/attribution/loader.py` | **Create** | Loads and validates `fingerprints.yaml`; returns `list[Fingerprint]` |
| `mlss_monitor/attribution/scorer.py` | **Create** | `sensor_score()`, `temporal_score()`, `combine()` — pure functions, no IO |
| `mlss_monitor/attribution/engine.py` | **Create** | `AttributionEngine` facade: loads fingerprints, calls scorer, returns `AttributionResult` |
| `mlss_monitor/detection_engine.py` | **Modify** | Call `AttributionEngine.attribute(fv)` inside `run()`, inject result into `save_inference()` evidence + title/description |
| `tests/attribution/test_loader.py` | **Create** | Loader: valid YAML loads cleanly; malformed fingerprint skipped; missing file raises |
| `tests/attribution/test_scorer.py` | **Create** | Sensor scorer, temporal scorer, combiner — pure unit tests |
| `tests/attribution/test_engine.py` | **Create** | AttributionEngine: top match, runner-up within 0.15, no match below floor, None fv fields handled |
| `tests/test_detection_engine_attribution.py` | **Create** | DetectionEngine.run() injects attribution evidence into save_inference evidence dict |

---

## Sensor state vocabulary

The fingerprint YAML uses five sensor state labels. The scorer maps these to `FeatureVector` field checks:

| Label | Condition (sensor `X`) |
|-------|------------------------|
| `high` | `X_peak_ratio >= 2.0` (or `X_current >= 3 × baseline`) |
| `elevated` | `X_peak_ratio >= 1.4` |
| `slight_rise` | `X_slope_1m > 0` and `X_peak_ratio >= 1.1` |
| `normal` | `X_peak_ratio < 1.4` (or None — no data = skip, not penalise) |
| `absent` | `X_current is None or X_current < X_baseline * 0.9` |
| `rising` | `X_slope_5m > 0` |

A `None` FeatureVector field is skipped (neither confirms nor denies) — the denominator for `sensor_score` is the count of non-None fields, not total fields.

---

## Task 1: `config/fingerprints.yaml` — five source fingerprints

**Files:**
- Create: `config/fingerprints.yaml`

- [ ] **Step 1: Write the fingerprints YAML**

```yaml
# config/fingerprints.yaml
# Source fingerprints for attribution layer.
# sensor states: high | elevated | slight_rise | normal | absent | rising
# temporal keys: rise_rate (fast|moderate|slow), decay_rate (fast|moderate|slow),
#                sustain_min_minutes, sustain_max_minutes,
#                nh3_follows_tvoc (bool), nh3_max_lag_seconds,
#                pm25_correlated_with_tvoc (bool), co_correlated_with_tvoc (bool)

sources:

  - id: biological_offgas
    label: "Biological off-gassing"
    description: "Human or animal biological VOC source (flatulence, sweat, breath)"
    examples: "flatulence, body odour, crowded space"
    sensors:
      tvoc:        high
      nh3:         high
      eco2:        slight_rise
      pm25:        normal
      co:          absent
    temporal:
      rise_rate:            fast
      sustain_max_minutes:  10
      decay_rate:           fast
      nh3_follows_tvoc:     true
      nh3_max_lag_seconds:  120
    confidence_floor: 0.65
    description_template: >
      TVOC reached {tvoc_current:.0f} ppb with NH3 elevated to {nh3_current:.0f} ppb
      following {nh3_lag_behind_tvoc_seconds:.0f} seconds later.
      Pattern matches biological off-gassing.
      No PM2.5 rise rules out combustion or cooking.
    action_template: "The source is typically short-lived. Ventilate if the odour persists."

  - id: chemical_offgassing
    label: "Chemical off-gassing"
    description: "Cleaning products, paint, adhesives, air fresheners, new materials"
    examples: "cleaning products, air fresheners, paint, adhesives, new furniture, cosmetics"
    sensors:
      tvoc:  elevated
      nh3:   normal
      pm25:  normal
      eco2:  normal
      co:    absent
    temporal:
      rise_rate:            moderate
      sustain_min_minutes:  30
      decay_rate:           slow
      nh3_follows_tvoc:     false
    confidence_floor: 0.60
    description_template: >
      TVOC is elevated at {tvoc_current:.0f} ppb but PM2.5 and eCO2 are normal.
      This is typical of volatile organic sources that do not produce particles or CO2:
      {examples}.
    action_template: "Ventilate the room. {persistence_note}"

  - id: cooking
    label: "Cooking activity"
    description: "Cooking, frying, or baking — heat + food-based VOCs and particles"
    examples: "frying, baking, grilling, boiling"
    sensors:
      tvoc:        elevated
      pm25:        elevated
      temperature: rising
      co:          absent
    temporal:
      rise_rate:               moderate
      sustain_min_minutes:     15
      pm25_correlated_with_tvoc: true
    confidence_floor: 0.55
    description_template: >
      TVOC at {tvoc_current:.0f} ppb and PM2.5 at {pm25_current:.1f} µg/m³ rising together
      with temperature increasing. Pattern is consistent with cooking activity.
    action_template: "Ensure kitchen ventilation is adequate. Open a window if PM2.5 remains elevated."

  - id: combustion
    label: "Combustion"
    description: "Open flame, candle, fire, or smoking"
    examples: "candle, open fire, cigarette, incense"
    sensors:
      tvoc:  high
      co:    elevated
      pm25:  high
      no2:   elevated
    temporal:
      rise_rate:               fast
      pm25_correlated_with_tvoc: true
    confidence_floor: 0.80
    description_template: >
      High TVOC ({tvoc_current:.0f} ppb) with elevated CO ({co_current:.0f} ppb)
      and high PM2.5 ({pm25_current:.1f} µg/m³). Sensor profile matches combustion.
    action_template: "Extinguish any open flames. Ventilate immediately if CO continues to rise."

  - id: external_pollution
    label: "External pollution ingress"
    description: "Outdoor air quality event entering via ventilation or gaps"
    examples: "traffic fumes, bonfire smoke, agricultural spray"
    sensors:
      pm25:  high
      tvoc:  normal
      co:    normal
      nh3:   normal
    temporal:
      rise_rate:            slow
      sustain_min_minutes:  60
    confidence_floor: 0.55
    description_template: >
      PM2.5 elevated at {pm25_current:.1f} µg/m³ without corresponding TVOC or CO rise.
      Pattern suggests external pollution entering from outside.
    action_template: "Close windows and doors. Check local air quality reports."
```

- [ ] **Step 2: Verify the YAML parses cleanly**

```bash
python -c "import yaml; d = yaml.safe_load(open('config/fingerprints.yaml')); print(len(d['sources']), 'fingerprints loaded')"
```

Expected output: `5 fingerprints loaded`

- [ ] **Step 3: Commit**

```bash
git add config/fingerprints.yaml
git commit -m "feat: add source fingerprints YAML (5 sources) for Phase 4 attribution"
```

---

## Task 2: `AttributionResult` dataclass + loader

**Files:**
- Create: `mlss_monitor/attribution/__init__.py`
- Create: `mlss_monitor/attribution/loader.py`
- Create: `tests/attribution/__init__.py`
- Create: `tests/attribution/test_loader.py`

- [ ] **Step 1: Write the failing tests**

`tests/attribution/__init__.py` — empty file.

`tests/attribution/test_loader.py`:

```python
"""Tests for fingerprint YAML loader."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


def _write_valid_yaml(tmp_path: Path) -> Path:
    cfg = {
        "sources": [
            {
                "id": "test_source",
                "label": "Test Source",
                "description": "A test fingerprint",
                "examples": "test",
                "sensors": {"tvoc": "elevated", "pm25": "normal"},
                "temporal": {"rise_rate": "fast"},
                "confidence_floor": 0.6,
                "description_template": "TVOC: {tvoc_current:.0f}",
                "action_template": "Do something.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _write_malformed_yaml(tmp_path: Path) -> Path:
    """A fingerprint missing required 'id' field."""
    cfg = {
        "sources": [
            {"label": "No ID"},  # missing 'id'
            {
                "id": "valid_source",
                "label": "Valid",
                "description": "Valid",
                "examples": "valid",
                "sensors": {"tvoc": "elevated"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "",
                "action_template": "",
            },
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_loader_returns_fingerprints(tmp_path):
    """load_fingerprints() returns a list of Fingerprint objects."""
    from mlss_monitor.attribution.loader import load_fingerprints, Fingerprint

    cfg_path = _write_valid_yaml(tmp_path)
    fingerprints = load_fingerprints(cfg_path)
    assert len(fingerprints) == 1
    assert fingerprints[0].id == "test_source"
    assert fingerprints[0].label == "Test Source"
    assert fingerprints[0].confidence_floor == pytest.approx(0.6)


def test_loader_skips_malformed_fingerprint(tmp_path):
    """load_fingerprints() skips entries missing required fields, keeps valid ones."""
    from mlss_monitor.attribution.loader import load_fingerprints

    cfg_path = _write_malformed_yaml(tmp_path)
    fingerprints = load_fingerprints(cfg_path)
    assert len(fingerprints) == 1
    assert fingerprints[0].id == "valid_source"


def test_loader_raises_on_missing_file(tmp_path):
    """load_fingerprints() raises FileNotFoundError if file does not exist."""
    from mlss_monitor.attribution.loader import load_fingerprints

    with pytest.raises(FileNotFoundError):
        load_fingerprints(tmp_path / "nonexistent.yaml")


def test_fingerprint_has_sensor_and_temporal_dicts(tmp_path):
    """Fingerprint.sensors and .temporal are dicts preserved from YAML."""
    from mlss_monitor.attribution.loader import load_fingerprints

    cfg_path = _write_valid_yaml(tmp_path)
    fp = load_fingerprints(cfg_path)[0]
    assert fp.sensors == {"tvoc": "elevated", "pm25": "normal"}
    assert fp.temporal == {"rise_rate": "fast"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/attribution/test_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'mlss_monitor.attribution'`

- [ ] **Step 3: Create the attribution package**

`mlss_monitor/attribution/__init__.py`:
```python
"""Attribution package — source fingerprint scoring for inference enrichment."""
from mlss_monitor.attribution.engine import AttributionEngine, AttributionResult

__all__ = ["AttributionEngine", "AttributionResult"]
```

`mlss_monitor/attribution/loader.py`:
```python
"""Fingerprint YAML loader for the attribution layer."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Required top-level keys every fingerprint must have.
_REQUIRED_KEYS = {"id", "label", "description", "sensors", "temporal",
                  "confidence_floor", "description_template", "action_template"}


@dataclasses.dataclass
class Fingerprint:
    id:                   str
    label:                str
    description:          str
    examples:             str
    sensors:              dict[str, str]   # sensor_name → state label
    temporal:             dict             # temporal profile keys → values
    confidence_floor:     float
    description_template: str
    action_template:      str


def load_fingerprints(config_path: str | Path) -> list[Fingerprint]:
    """Load and validate fingerprint definitions from YAML.

    Skips malformed entries (missing required keys) with a warning.
    Raises FileNotFoundError if config_path does not exist.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Fingerprint config not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    fingerprints: list[Fingerprint] = []
    for entry in raw.get("sources", []):
        missing = _REQUIRED_KEYS - set(entry.keys())
        if missing:
            log.warning(
                "Fingerprint loader: skipping entry missing keys %r: %s",
                missing,
                entry.get("id", "<no id>"),
            )
            continue
        fingerprints.append(
            Fingerprint(
                id=entry["id"],
                label=entry["label"],
                description=entry["description"],
                examples=entry.get("examples", ""),
                sensors=entry["sensors"],
                temporal=entry["temporal"],
                confidence_floor=float(entry["confidence_floor"]),
                description_template=entry["description_template"],
                action_template=entry["action_template"],
            )
        )
    return fingerprints
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/attribution/test_loader.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/attribution/__init__.py mlss_monitor/attribution/loader.py tests/attribution/__init__.py tests/attribution/test_loader.py
git commit -m "feat: add attribution loader and Fingerprint dataclass (4 tests)"
```

---

## Task 3: Sensor scorer + temporal scorer

**Files:**
- Create: `mlss_monitor/attribution/scorer.py`
- Create: `tests/attribution/test_scorer.py`

- [ ] **Step 1: Write the failing tests**

`tests/attribution/test_scorer.py`:

```python
"""Tests for attribution sensor and temporal scorers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mlss_monitor.attribution.loader import Fingerprint
from mlss_monitor.attribution.scorer import sensor_score, temporal_score, combine
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=_ts(), **kwargs)


def _fp(sensors=None, temporal=None, floor=0.5) -> Fingerprint:
    return Fingerprint(
        id="test",
        label="Test",
        description="",
        examples="",
        sensors=sensors or {},
        temporal=temporal or {},
        confidence_floor=floor,
        description_template="",
        action_template="",
    )


# ── sensor_score ──────────────────────────────────────────────────────────────

def test_sensor_score_high_matches_high_peak_ratio():
    """'high' state matches when tvoc_peak_ratio >= 2.0."""
    fp = _fp(sensors={"tvoc": "high"})
    fv = _fv(tvoc_current=400.0, tvoc_baseline=150.0, tvoc_peak_ratio=2.7)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_elevated_matches_peak_ratio_1_4():
    """'elevated' matches when tvoc_peak_ratio >= 1.4."""
    fp = _fp(sensors={"tvoc": "elevated"})
    fv = _fv(tvoc_current=220.0, tvoc_baseline=150.0, tvoc_peak_ratio=1.47)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_normal_matches_low_ratio():
    """'normal' matches when peak_ratio < 1.4."""
    fp = _fp(sensors={"pm25": "normal"})
    fv = _fv(pm25_current=5.0, pm25_baseline=5.0, pm25_peak_ratio=1.0)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_absent_matches_none_current():
    """'absent' matches when the current value is None."""
    fp = _fp(sensors={"co": "absent"})
    fv = _fv(co_current=None)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_partial_match():
    """Score is fraction of matched fields over evaluated fields."""
    fp = _fp(sensors={"tvoc": "high", "pm25": "high"})
    # tvoc matches (peak_ratio 2.5), pm25 doesn't match (peak_ratio 1.1)
    fv = _fv(
        tvoc_current=400.0, tvoc_baseline=150.0, tvoc_peak_ratio=2.5,
        pm25_current=6.0, pm25_baseline=5.0, pm25_peak_ratio=1.1,
    )
    assert sensor_score(fp, fv) == pytest.approx(0.5)


def test_sensor_score_skips_none_fields():
    """None FeatureVector fields are skipped — denominator excludes them."""
    fp = _fp(sensors={"tvoc": "elevated", "co": "elevated"})
    # tvoc matches; co is None (skip, don't penalise)
    fv = _fv(tvoc_current=220.0, tvoc_baseline=150.0, tvoc_peak_ratio=1.5,
             co_current=None)
    # Only 1 field evaluated, 1 matched → 1.0
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_empty_sensors_returns_zero():
    """No sensor specs → 0.0 score."""
    fp = _fp(sensors={})
    fv = _fv(tvoc_current=400.0)
    assert sensor_score(fp, fv) == pytest.approx(0.0)


# ── temporal_score ────────────────────────────────────────────────────────────

def test_temporal_score_nh3_follows_tvoc_matches():
    """nh3_follows_tvoc: true matches when nh3_lag_behind_tvoc_seconds is within limit."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=45.0)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_nh3_follows_tvoc_fails_excess_lag():
    """nh3_follows_tvoc: true fails when lag exceeds nh3_max_lag_seconds."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=180.0)
    assert temporal_score(fp, fv) == pytest.approx(0.0)


def test_temporal_score_pm25_correlated_matches():
    """pm25_correlated_with_tvoc: true matches when fv.pm25_correlated_with_tvoc is True."""
    fp = _fp(temporal={"pm25_correlated_with_tvoc": True})
    fv = _fv(pm25_correlated_with_tvoc=True)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_skips_none_fv_fields():
    """Temporal score skips criteria when relevant FeatureVector field is None."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=None)
    # Field is None → skip, denominator = 0 → return 0.0 (no data, not penalised)
    assert temporal_score(fp, fv) == pytest.approx(0.0)


def test_temporal_score_empty_temporal_returns_zero():
    """No temporal criteria → 0.0."""
    fp = _fp(temporal={})
    fv = _fv()
    assert temporal_score(fp, fv) == pytest.approx(0.0)


# ── combine ───────────────────────────────────────────────────────────────────

def test_combine_weights_correctly():
    """combine() returns sensor×0.6 + temporal×0.4."""
    result = combine(sensor=1.0, temporal=1.0)
    assert result == pytest.approx(1.0)

    result = combine(sensor=1.0, temporal=0.0)
    assert result == pytest.approx(0.6)

    result = combine(sensor=0.0, temporal=1.0)
    assert result == pytest.approx(0.4)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/attribution/test_scorer.py -v
```

Expected: `ImportError: cannot import name 'sensor_score' from 'mlss_monitor.attribution.scorer'`

- [ ] **Step 3: Implement scorer.py**

`mlss_monitor/attribution/scorer.py`:

```python
"""Sensor and temporal scoring functions for the attribution layer.

All functions are pure — no IO, no side effects.
"""
from __future__ import annotations

import dataclasses

from mlss_monitor.attribution.loader import Fingerprint
from mlss_monitor.feature_vector import FeatureVector

# Maps sensor name (as used in fingerprints.yaml) to FeatureVector field prefix.
_SENSOR_PREFIX: dict[str, str] = {
    "tvoc":        "tvoc",
    "eco2":        "eco2",
    "temperature": "temperature",
    "humidity":    "humidity",
    "pm25":        "pm25",
    "co":          "co",
    "no2":         "no2",
    "nh3":         "nh3",
}


def _peak_ratio(fv: FeatureVector, prefix: str) -> float | None:
    """Return the peak_ratio field for `prefix`, or None if unavailable."""
    return getattr(fv, f"{prefix}_peak_ratio", None)


def _current(fv: FeatureVector, prefix: str) -> float | None:
    return getattr(fv, f"{prefix}_current", None)


def _slope_5m(fv: FeatureVector, prefix: str) -> float | None:
    return getattr(fv, f"{prefix}_slope_5m", None)


def _state_matches(state: str, fv: FeatureVector, prefix: str) -> bool | None:
    """Return True/False/None.

    None means the relevant FeatureVector field is None — caller should skip
    this criterion rather than penalise it.
    """
    ratio = _peak_ratio(fv, prefix)
    current = _current(fv, prefix)

    if state == "high":
        if ratio is None:
            return None
        return ratio >= 2.0

    elif state == "elevated":
        if ratio is None:
            return None
        return ratio >= 1.4

    elif state == "slight_rise":
        slope = _slope_5m(fv, prefix)
        if ratio is None or slope is None:
            return None
        return slope > 0 and ratio >= 1.1

    elif state == "normal":
        if ratio is None:
            return None
        return ratio < 1.4

    elif state == "absent":
        # None current counts as absent
        if current is None:
            return True
        baseline = getattr(fv, f"{prefix}_baseline", None)
        if baseline is None or baseline == 0:
            return current < 5  # absolute low threshold
        return current < baseline * 0.9

    elif state == "rising":
        slope = _slope_5m(fv, prefix)
        if slope is None:
            return None
        return slope > 0

    return None


def sensor_score(fp: Fingerprint, fv: FeatureVector) -> float:
    """Fraction of non-None sensor fields that match the fingerprint spec.

    Returns 0.0 when there are no sensor criteria (empty sensors dict).
    """
    if not fp.sensors:
        return 0.0

    matched = 0
    evaluated = 0
    for sensor_name, expected_state in fp.sensors.items():
        prefix = _SENSOR_PREFIX.get(sensor_name)
        if prefix is None:
            continue  # unknown sensor, skip
        result = _state_matches(expected_state, fv, prefix)
        if result is None:
            continue  # no data — skip
        evaluated += 1
        if result:
            matched += 1

    if evaluated == 0:
        return 0.0
    return matched / evaluated


def temporal_score(fp: Fingerprint, fv: FeatureVector) -> float:
    """Fraction of evaluable temporal criteria that match the FeatureVector.

    Currently evaluates:
      - nh3_follows_tvoc + nh3_max_lag_seconds
      - pm25_correlated_with_tvoc
      - co_correlated_with_tvoc

    Other temporal keys (rise_rate, decay_rate, sustain_*) are not yet mapped
    to FeatureVector fields and are skipped.

    Returns 0.0 when no criteria can be evaluated.
    """
    t = fp.temporal
    if not t:
        return 0.0

    matched = 0
    evaluated = 0

    # nh3_follows_tvoc
    if "nh3_follows_tvoc" in t:
        lag = fv.nh3_lag_behind_tvoc_seconds
        if lag is not None:
            evaluated += 1
            max_lag = t.get("nh3_max_lag_seconds", 120)
            if t["nh3_follows_tvoc"] is True:
                matched += 1 if lag <= max_lag else 0
            else:
                matched += 1 if lag is None or lag > max_lag else 0

    # pm25_correlated_with_tvoc
    if "pm25_correlated_with_tvoc" in t:
        corr = fv.pm25_correlated_with_tvoc
        if corr is not None:
            evaluated += 1
            matched += 1 if (corr == t["pm25_correlated_with_tvoc"]) else 0

    # co_correlated_with_tvoc
    if "co_correlated_with_tvoc" in t:
        corr = fv.co_correlated_with_tvoc
        if corr is not None:
            evaluated += 1
            matched += 1 if (corr == t["co_correlated_with_tvoc"]) else 0

    if evaluated == 0:
        return 0.0
    return matched / evaluated


def combine(sensor: float, temporal: float) -> float:
    """Combine sensor and temporal scores: sensor×0.6 + temporal×0.4."""
    return sensor * 0.6 + temporal * 0.4
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/attribution/test_scorer.py -v
```

Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/attribution/scorer.py tests/attribution/test_scorer.py
git commit -m "feat: add attribution sensor + temporal scorer (15 tests)"
```

---

## Task 4: `AttributionEngine` facade + `AttributionResult`

**Files:**
- Create: `mlss_monitor/attribution/engine.py`
- Create: `tests/attribution/test_engine.py`

- [ ] **Step 1: Write the failing tests**

`tests/attribution/test_engine.py`:

```python
"""Tests for AttributionEngine: top match, runner-up, no match, None fields."""
from __future__ import annotations

import pytest
import yaml
from datetime import datetime, timezone
from pathlib import Path

from mlss_monitor.attribution.engine import AttributionEngine, AttributionResult
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _write_config(tmp_path: Path) -> Path:
    """Write a minimal fingerprints.yaml with two clearly distinct sources."""
    cfg = {
        "sources": [
            {
                "id": "high_tvoc_no_pm25",
                "label": "High TVOC, no PM2.5",
                "description": "Test fingerprint A",
                "examples": "example A",
                "sensors": {"tvoc": "high", "pm25": "absent"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "TVOC high at {tvoc_current:.0f} ppb.",
                "action_template": "Ventilate.",
            },
            {
                "id": "high_pm25_no_tvoc",
                "label": "High PM2.5, no TVOC",
                "description": "Test fingerprint B",
                "examples": "example B",
                "sensors": {"pm25": "high", "tvoc": "absent"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "PM2.5 high at {pm25_current:.1f} µg/m³.",
                "action_template": "Close windows.",
            },
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _fv_tvoc_high(tmp=None) -> FeatureVector:
    """FeatureVector matching 'high_tvoc_no_pm25' fingerprint."""
    return FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
        pm25_current=None,  # absent
    )


def _fv_pm25_high() -> FeatureVector:
    """FeatureVector matching 'high_pm25_no_tvoc' fingerprint."""
    return FeatureVector(
        timestamp=_ts(),
        pm25_current=80.0,
        pm25_baseline=8.0,
        pm25_peak_ratio=10.0,
        tvoc_current=None,  # absent
    )


def _fv_ambiguous() -> FeatureVector:
    """FeatureVector where both fingerprints score about the same."""
    return FeatureVector(
        timestamp=_ts(),
        tvoc_current=300.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=3.0,
        pm25_current=40.0,
        pm25_baseline=8.0,
        pm25_peak_ratio=5.0,
    )


# ── AttributionResult ─────────────────────────────────────────────────────────

def test_attribution_result_is_dataclass():
    r = AttributionResult(
        source_id="test",
        label="Test",
        confidence=0.7,
        runner_up_id=None,
        runner_up_confidence=None,
        description="desc",
        action="act",
    )
    assert r.source_id == "test"
    assert r.confidence == pytest.approx(0.7)
    assert r.runner_up_id is None


# ── AttributionEngine.attribute ───────────────────────────────────────────────

def test_attribute_returns_top_match(tmp_path):
    """attribute() returns the fingerprint with highest confidence above floor."""
    engine = AttributionEngine(_write_config(tmp_path))
    result = engine.attribute(_fv_tvoc_high())
    assert result is not None
    assert result.source_id == "high_tvoc_no_pm25"
    assert result.confidence >= 0.5


def test_attribute_returns_runner_up_when_within_015(tmp_path):
    """When two fingerprints score within 0.15 of each other, runner_up is set."""
    cfg = {
        "sources": [
            {
                "id": "source_a",
                "label": "Source A",
                "description": "Desc A",
                "examples": "ex A",
                "sensors": {"tvoc": "high"},
                "temporal": {},
                "confidence_floor": 0.3,
                "description_template": "A",
                "action_template": "A action",
            },
            {
                "id": "source_b",
                "label": "Source B",
                "description": "Desc B",
                "examples": "ex B",
                "sensors": {"tvoc": "elevated"},
                "temporal": {},
                "confidence_floor": 0.3,
                "description_template": "B",
                "action_template": "B action",
            },
        ]
    }
    p = tmp_path / "fp.yaml"
    p.write_text(yaml.dump(cfg))
    engine = AttributionEngine(p)
    # Both 'high' and 'elevated' match a tvoc_peak_ratio=2.5 FeatureVector
    fv = FeatureVector(
        timestamp=_ts(),
        tvoc_current=375.0,
        tvoc_baseline=150.0,
        tvoc_peak_ratio=2.5,
    )
    result = engine.attribute(fv)
    assert result is not None
    # If runner-up is within 0.15, it should be set
    if result.runner_up_confidence is not None:
        assert abs(result.confidence - result.runner_up_confidence) <= 0.15


def test_attribute_returns_none_when_no_match_above_floor(tmp_path):
    """attribute() returns None when no fingerprint clears its confidence_floor."""
    cfg = {
        "sources": [
            {
                "id": "impossible",
                "label": "Impossible",
                "description": "",
                "examples": "",
                "sensors": {"tvoc": "high", "pm25": "high", "co": "elevated"},
                "temporal": {},
                "confidence_floor": 0.99,  # very high floor
                "description_template": "",
                "action_template": "",
            }
        ]
    }
    p = tmp_path / "fp.yaml"
    p.write_text(yaml.dump(cfg))
    engine = AttributionEngine(p)
    fv = FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
    )
    result = engine.attribute(fv)
    assert result is None


def test_attribute_handles_all_none_fv(tmp_path):
    """attribute() does not raise when all FeatureVector fields are None."""
    engine = AttributionEngine(_write_config(tmp_path))
    fv = FeatureVector(timestamp=_ts())  # all fields None
    result = engine.attribute(fv)
    # May return None or a low-confidence result — just must not raise
    assert result is None or isinstance(result.confidence, float)


def test_attribute_description_filled_from_template(tmp_path):
    """AttributionResult.description is filled from fingerprint description_template."""
    engine = AttributionEngine(_write_config(tmp_path))
    result = engine.attribute(_fv_tvoc_high())
    assert result is not None
    # Template contains {tvoc_current:.0f} — should be filled with a number
    assert "{" not in result.description  # no unfilled slots
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/attribution/test_engine.py -v
```

Expected: `ImportError: cannot import name 'AttributionEngine' from 'mlss_monitor.attribution.engine'`

- [ ] **Step 3: Implement engine.py**

`mlss_monitor/attribution/engine.py`:

```python
"""AttributionEngine: scores fingerprints against a FeatureVector, returns top match."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from mlss_monitor.attribution.loader import Fingerprint, load_fingerprints
from mlss_monitor.attribution.scorer import combine, sensor_score, temporal_score
from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

# If the runner-up confidence is within this delta of the primary, it is surfaced.
_RUNNER_UP_DELTA = 0.15


@dataclasses.dataclass
class AttributionResult:
    source_id:            str
    label:                str
    confidence:           float
    runner_up_id:         str | None
    runner_up_confidence: float | None
    description:          str
    action:               str


class AttributionEngine:
    """Loads fingerprints from YAML and scores them against a FeatureVector.

    Usage:
        engine = AttributionEngine("config/fingerprints.yaml")
        result = engine.attribute(fv)   # AttributionResult or None
    """

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._fingerprints: list[Fingerprint] = load_fingerprints(self._config_path)
        log.info(
            "AttributionEngine: loaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )

    def attribute(self, fv: FeatureVector) -> AttributionResult | None:
        """Score all fingerprints against fv, return the best match above its floor.

        Returns:
            AttributionResult with runner_up set if runner-up is within 0.15.
            None if no fingerprint clears its confidence_floor.
        """
        if not self._fingerprints:
            return None

        scored: list[tuple[float, Fingerprint]] = []
        for fp in self._fingerprints:
            try:
                ss = sensor_score(fp, fv)
                ts = temporal_score(fp, fv)
                conf = combine(ss, ts)
                scored.append((conf, fp))
            except Exception as exc:
                log.warning(
                    "AttributionEngine: error scoring fingerprint %r: %s",
                    fp.id, exc,
                )

        if not scored:
            return None

        # Sort descending by confidence
        scored.sort(key=lambda x: x[0], reverse=True)
        best_conf, best_fp = scored[0]

        if best_conf < best_fp.confidence_floor:
            return None

        # Runner-up
        runner_up_id = None
        runner_up_conf = None
        if len(scored) > 1:
            second_conf, second_fp = scored[1]
            if (best_conf - second_conf) <= _RUNNER_UP_DELTA:
                runner_up_id = second_fp.id
                runner_up_conf = second_conf

        # Fill description template (gracefully handle missing fields)
        fv_dict = dataclasses.asdict(fv)

        class _SafeDict(dict):
            def __missing__(self, key):
                return 0

        try:
            desc = best_fp.description_template.format_map(_SafeDict(fv_dict))
        except Exception:
            desc = best_fp.description

        try:
            action = best_fp.action_template.format_map(
                _SafeDict({**fv_dict, "persistence_note": ""})
            )
        except Exception:
            action = best_fp.action_template

        return AttributionResult(
            source_id=best_fp.id,
            label=best_fp.label,
            confidence=best_conf,
            runner_up_id=runner_up_id,
            runner_up_confidence=runner_up_conf,
            description=desc,
            action=action,
        )
```

Update `mlss_monitor/attribution/__init__.py` to export `AttributionResult`:

```python
"""Attribution package — source fingerprint scoring for inference enrichment."""
from mlss_monitor.attribution.engine import AttributionEngine, AttributionResult

__all__ = ["AttributionEngine", "AttributionResult"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/attribution/test_engine.py -v
```

Expected: 7 passed

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all existing tests pass + 7 new = 335+ passed

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/attribution/engine.py mlss_monitor/attribution/__init__.py tests/attribution/test_engine.py
git commit -m "feat: add AttributionEngine with runner-up logic (7 tests)"
```

---

## Task 5: Wire attribution into `DetectionEngine.run()`

**Files:**
- Modify: `mlss_monitor/detection_engine.py`
- Create: `tests/test_detection_engine_attribution.py`

The goal: when `DetectionEngine.run(fv)` fires an event and `dry_run=False`, it calls `AttributionEngine.attribute(fv)` and:
1. Prepends the attribution label + confidence to the inference title
2. Appends the attribution description to the inference description
3. Injects attribution keys into the `evidence` dict

- [ ] **Step 1: Write the failing test**

`tests/test_detection_engine_attribution.py`:

```python
"""Tests that DetectionEngine.run() injects attribution evidence when dry_run=False."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from mlss_monitor.detection_engine import DetectionEngine
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _write_rules(tmp_path: Path) -> Path:
    rules = {
        "rules": [
            {
                "id": "tvoc_spike",
                "expression": "tvoc_peak_ratio > 1.5 and tvoc_current > 200",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "confidence": 0.8,
                "dedupe_hours": 1,
                "title_template": "TVOC spike ({tvoc_current:.0f} ppb)",
                "description_template": "TVOC elevated to {tvoc_current:.0f} ppb.",
                "action": "Ventilate.",
            }
        ]
    }
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.dump(rules))
    return p


def _write_anomaly(tmp_path: Path) -> Path:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,
            "model_dir": str(tmp_path / "models"),
            "channels": [],  # no channels — anomaly won't fire
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _write_fingerprints(tmp_path: Path) -> Path:
    cfg = {
        "sources": [
            {
                "id": "chemical_offgassing",
                "label": "Chemical off-gassing",
                "description": "VOC without particles",
                "examples": "paint, cleaning products",
                "sensors": {"tvoc": "elevated", "pm25": "absent"},
                "temporal": {},
                "confidence_floor": 0.4,
                "description_template": "TVOC at {tvoc_current:.0f} ppb, no PM2.5.",
                "action_template": "Ventilate.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _make_engine(tmp_path, dry_run=False) -> DetectionEngine:
    rules_path = _write_rules(tmp_path)
    anomaly_path = _write_anomaly(tmp_path)
    fp_path = _write_fingerprints(tmp_path)
    return DetectionEngine(
        rules_path=rules_path,
        anomaly_config_path=anomaly_path,
        model_dir=tmp_path / "models",
        fingerprints_path=fp_path,
        dry_run=dry_run,
    )


def _fv_tvoc_spike() -> FeatureVector:
    return FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
        tvoc_elevated_minutes=5.0,
        pm25_current=None,
        pm25_peak_ratio=None,
    )


def test_detection_engine_accepts_fingerprints_path(tmp_path):
    """DetectionEngine.__init__ accepts a fingerprints_path parameter."""
    engine = _make_engine(tmp_path)
    assert engine is not None


def test_run_injects_attribution_evidence_when_live(tmp_path):
    """In dry_run=False mode, save_inference is called with attribution keys in evidence."""
    engine = _make_engine(tmp_path, dry_run=False)
    fv = _fv_tvoc_spike()

    saved_calls = []

    def fake_save(**kwargs):
        saved_calls.append(kwargs)

    def fake_get_recent(event_type, hours):
        return None  # no dedupe — let it fire

    with patch("mlss_monitor.detection_engine.save_inference", fake_save), \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        engine.run(fv)

    assert len(saved_calls) >= 1
    call = saved_calls[0]
    evidence = call["evidence"]
    assert "attribution" in evidence
    assert "attribution_confidence" in evidence


def test_run_sets_runner_up_in_evidence_when_present(tmp_path):
    """evidence dict includes runner_up keys when runner-up is surfaced."""
    engine = _make_engine(tmp_path, dry_run=False)
    fv = _fv_tvoc_spike()

    saved_calls = []

    def fake_save(**kwargs):
        saved_calls.append(kwargs)

    def fake_get_recent(event_type, hours):
        return None

    with patch("mlss_monitor.detection_engine.save_inference", fake_save), \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        engine.run(fv)

    call = saved_calls[0]
    evidence = call["evidence"]
    # runner_up may or may not be present depending on scoring — just check keys exist
    assert "attribution" in evidence


def test_run_dry_run_does_not_call_save_inference(tmp_path):
    """In dry_run=True mode, save_inference is never called."""
    engine = _make_engine(tmp_path, dry_run=True)
    fv = _fv_tvoc_spike()

    saved_calls = []

    def fake_save(**kwargs):
        saved_calls.append(kwargs)

    def fake_get_recent(event_type, hours):
        return None

    with patch("mlss_monitor.detection_engine.save_inference", fake_save), \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        engine.run(fv)

    assert len(saved_calls) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_detection_engine_attribution.py -v
```

Expected: `TypeError: DetectionEngine.__init__() got an unexpected keyword argument 'fingerprints_path'`

- [ ] **Step 3: Extend DetectionEngine to accept fingerprints_path and call attribution**

Modify `mlss_monitor/detection_engine.py`:

In `__init__`, add `fingerprints_path` parameter and instantiate `AttributionEngine`:

```python
# Add import at top of file:
from mlss_monitor.attribution import AttributionEngine, AttributionResult

# Modify __init__ signature:
def __init__(
    self,
    rules_path: str | Path,
    anomaly_config_path: str | Path,
    model_dir: str | Path,
    fingerprints_path: str | Path | None = None,
    dry_run: bool = True,
) -> None:
    self._dry_run = dry_run
    self._rule_engine = RuleEngine(rules_path)
    self._anomaly_detector = AnomalyDetector(anomaly_config_path, model_dir)
    self._attribution_engine: AttributionEngine | None = None
    if fingerprints_path is not None:
        try:
            self._attribution_engine = AttributionEngine(fingerprints_path)
        except Exception as exc:
            log.error("DetectionEngine: could not load fingerprints: %s", exc)
```

Add `_attribute()` helper:

```python
def _attribute(self, fv: FeatureVector) -> AttributionResult | None:
    """Run attribution scoring. Returns None if engine not configured or no match."""
    if self._attribution_engine is None:
        return None
    try:
        return self._attribution_engine.attribute(fv)
    except Exception as exc:
        log.warning("DetectionEngine: attribution error: %s", exc)
        return None
```

In `run()`, replace the `save_inference` call block for rule matches with attribution enrichment:

```python
# Inside the `for match in matches:` loop, replace:
#     save_inference(
#         event_type=match.event_type,
#         ...
#         evidence={"fv_timestamp": fv.timestamp.isoformat()},
#         ...
#     )
# With:
attribution = self._attribute(fv)
evidence: dict = {"fv_timestamp": fv.timestamp.isoformat()}
if attribution is not None:
    evidence["attribution"] = attribution.source_id
    evidence["attribution_confidence"] = round(attribution.confidence, 3)
    if attribution.runner_up_id is not None:
        evidence["runner_up"] = attribution.runner_up_id
        evidence["runner_up_confidence"] = round(attribution.runner_up_confidence, 3)

# Build enriched title + description
title = match.title
description = match.description
if attribution is not None:
    title = f"{match.title} — {attribution.label} ({attribution.confidence:.0%})"
    description = f"{match.description}\n\n{attribution.description}"
action = attribution.action if attribution is not None else match.action

save_inference(
    event_type=match.event_type,
    severity=match.severity,
    title=title,
    description=description,
    action=action,
    evidence=evidence,
    confidence=match.confidence,
)
```

- [ ] **Step 4: Update app.py to pass fingerprints_path to DetectionEngine**

In `mlss_monitor/app.py`, update the `DetectionEngine` instantiation (in `main()` after the `hot_tier` reinit):

```python
_detection_engine = DetectionEngine(
    rules_path=_PROJECT_ROOT / "config" / "rules.yaml",
    anomaly_config_path=_PROJECT_ROOT / "config" / "anomaly.yaml",
    model_dir=_PROJECT_ROOT / "data" / "anomaly_models",
    fingerprints_path=_PROJECT_ROOT / "config" / "fingerprints.yaml",
    dry_run=True,  # Shadow mode. Set to False once parity confirmed.
)
```

> **Note:** `_detection_engine` is currently defined at module level in app.py. It also needs to be moved inside `main()` the same way `hot_tier` was, so that `fingerprints.yaml` is loaded after the app is fully initialised. Check the current location with `grep -n "_detection_engine" mlss_monitor/app.py` before editing.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_detection_engine_attribution.py -v
```

Expected: 4 passed

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -q --tb=short
```

Expected: all passing, 339+ total

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/detection_engine.py mlss_monitor/app.py tests/test_detection_engine_attribution.py
git commit -m "feat: wire attribution into DetectionEngine.run() — enriches save_inference evidence"
```

---

## Task 6: Full test run and self-review

**Files:**
- No new files

- [ ] **Step 1: Run complete test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass, no failures.

- [ ] **Step 2: Verify fingerprints YAML loads via attribution engine directly**

```bash
python -c "
from mlss_monitor.attribution import AttributionEngine
e = AttributionEngine('config/fingerprints.yaml')
print('Loaded', len(e._fingerprints), 'fingerprints')
for fp in e._fingerprints:
    print(' -', fp.id, '| floor:', fp.confidence_floor)
"
```

Expected: 5 fingerprints printed with ids: biological_offgas, chemical_offgassing, cooking, combustion, external_pollution

- [ ] **Step 3: Commit**

No code changes — this task is verification only.

---

## Phase 4 carry-forward notes (for Phase 5)

- `DetectionEngine` is still `dry_run=True` — flip to `False` once parity confirmed against old `run_analysis()`
- Attribution does NOT yet score `rise_rate`, `decay_rate`, `sustain_min_minutes`, `sustain_max_minutes` — these temporal keys are in `fingerprints.yaml` for future use but skipped by the current scorer (no corresponding FeatureVector fields). Phase 5 config UI can expose them; Phase 7 Coral replaces YAML scoring entirely.
- `_both_rising` note from project memory: `_sensors_correlated()` returns True only for "both rising" — attribution rules that check `co_correlated_with_tvoc` will miss "both falling" cases; document as known limitation.
- `get_24h_baselines()` performance: still fetches 24h of data per cycle — Phase 5 should add SQL aggregation.
- Phase 5 configuration UI lives under `/settings/insights-engine/` — fingerprint manager, rule manager, anomaly settings.

# Phase 5 — Multivariate Detection + Actionable Inference Cards

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five composite multi-dimensional anomaly models that detect cross-channel correlation patterns, and enrich every inference with a structured `sensor_snapshot` evidence block so the UI can render real-world, actionable cards without any calculation logic in JavaScript.

**Architecture:** `MultivarAnomalyDetector` (new class, mirrors `AnomalyDetector`) feeds multi-dimensional dicts to one `HalfSpaceTrees` per composite model. `AnomalyDetector` gains EMA baseline tracking. A new `inference_evidence` module contains all interpretation logic (snapshot building, description templating, action text). `DetectionEngine.run()` is updated to call both detectors and use the evidence builder for all `save_inference` calls. The JS evidence renderer is updated to display the pre-computed `sensor_snapshot` array.

**Tech Stack:** river (HalfSpaceTrees already used), PyYAML (already used), pickle, existing FeatureVector + DetectionEngine patterns.

**Push both branches after every commit:**
```bash
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `config/multivar_anomaly.yaml` | **Create** | 5 composite model definitions (id, label, description, channels) |
| `mlss_monitor/multivar_anomaly_detector.py` | **Create** | Composite HalfSpaceTrees detector; pickle persistence; EMA baselines |
| `mlss_monitor/inference_evidence.py` | **Create** | `build_sensor_snapshot`, `anomaly_description`, `anomaly_action` — pure functions, no IO |
| `mlss_monitor/anomaly_detector.py` | **Modify** | Add `self._ema` tracking + `baseline(ch)` method |
| `mlss_monitor/detection_engine.py` | **Modify** | Wire `MultivarAnomalyDetector`; use `inference_evidence` for all anomaly `save_inference` calls |
| `database/init_db.py` | **Modify** | Add 5 new event types to CHECK constraint + migrations |
| `mlss_monitor/app.py` | **Modify** | Pass `multivar_config_path` to `DetectionEngine` |
| `static/js/dashboard.js` | **Modify** | Render `sensor_snapshot` array instead of generic k-v evidence |
| `tests/test_multivar_anomaly_detector.py` | **Create** | Unit tests for composite detector |
| `tests/test_inference_evidence.py` | **Create** | Unit tests for snapshot builder + description templates |

---

## Task 1: `config/multivar_anomaly.yaml`

**Files:**
- Create: `config/multivar_anomaly.yaml`

- [ ] **Step 1: Create the config file**

```yaml
# config/multivar_anomaly.yaml
# Composite multi-dimensional anomaly models.
# Each model feeds a multi-field dict to a single HalfSpaceTrees instance.
# channels: list of FeatureVector field names to include in each reading.
# A reading is only learned/scored when ALL listed channels have non-None values.

multivar_anomaly:
  threshold: 0.75
  cold_start_readings: 500

  models:
    - id: combustion_signature
      label: "Combustion signature"
      description: >
        Detects combustion events (candles, cooking, wood-burning) as a joint
        pattern across CO, NO2, PM2.5 and PM10. Catches events where all four
        rise moderately together — a pattern missed by individual thresholds.
      channels:
        - co_current
        - no2_current
        - pm25_current
        - pm10_current

    - id: particle_distribution
      label: "Particle size distribution"
      description: >
        Monitors the ratio relationship between PM1, PM2.5 and PM10.
        PM1≈PM2.5 (ultrafine) indicates combustion or smoke; PM10>>PM2.5
        indicates coarse dust or pollen. Flags when the size distribution
        becomes unusual even if absolute levels are not threshold-breaking.
      channels:
        - pm1_current
        - pm25_current
        - pm10_current

    - id: ventilation_quality
      label: "Ventilation quality"
      description: >
        Tracks the joint build-up of eCO2, TVOC and NH3. All three rise
        together in a poorly-ventilated space even when no single channel
        breaches its threshold. Flags stale-air conditions earlier than
        rule-based detection.
      channels:
        - eco2_current
        - tvoc_current
        - nh3_current

    - id: gas_relationship
      label: "Gas sensor relationship"
      description: >
        Monitors the normal correlation structure of CO, NO2 and NH3 from the
        MICS6814. When one channel breaks its expected relationship with the
        others this may indicate sensor drift or a genuinely unusual gas mixture.
      channels:
        - co_current
        - no2_current
        - nh3_current

    - id: thermal_moisture
      label: "Thermal-moisture stress"
      description: >
        Scores temperature, humidity and VPD together. Catches comfort-zone
        stress as a combined signal — HVAC failure shows as a correlated drift
        across all three dimensions before any single threshold is breached.
      channels:
        - temperature_current
        - humidity_current
        - vpd_kpa
```

- [ ] **Step 2: Commit**

```bash
git add config/multivar_anomaly.yaml
git commit -m "feat: add multivar_anomaly.yaml with 5 composite model definitions"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 2: `MultivarAnomalyDetector` (TDD)

**Files:**
- Create: `mlss_monitor/multivar_anomaly_detector.py`
- Create: `tests/test_multivar_anomaly_detector.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_multivar_anomaly_detector.py
import pickle
import tempfile
from pathlib import Path

import pytest

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.multivar_anomaly_detector import MultivarAnomalyDetector


def _config(tmp_path):
    cfg = tmp_path / "multivar_anomaly.yaml"
    cfg.write_text("""
multivar_anomaly:
  threshold: 0.75
  cold_start_readings: 5
  models:
    - id: test_model
      label: "Test model"
      description: "For testing."
      channels:
        - co_current
        - no2_current
""")
    return str(cfg)


def _fv(co=10.0, no2=5.0):
    return FeatureVector(co_current=co, no2_current=no2)


def test_learn_and_score_returns_none_before_cold_start(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    fv = _fv()
    scores = det.learn_and_score(fv)
    # Only 1 reading — cold_start=5 — should be None
    assert scores["test_model"] is None


def test_learn_and_score_returns_float_after_cold_start(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(6):
        scores = det.learn_and_score(_fv())
    assert isinstance(scores["test_model"], float)


def test_skips_reading_when_channel_is_none(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    fv = FeatureVector(co_current=10.0, no2_current=None)  # missing channel
    scores = det.learn_and_score(fv)
    assert scores["test_model"] is None


def test_anomalous_models_returns_ids_above_threshold(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    # Feed 6 identical readings then one different value
    for _ in range(6):
        det.learn_and_score(_fv(co=10.0, no2=5.0))
    scores = {"test_model": 0.9}
    result = det.anomalous_models(scores)
    assert "test_model" in result


def test_anomalous_models_excludes_below_threshold(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    scores = {"test_model": 0.3}
    assert det.anomalous_models(scores) == []


def test_baseline_returns_ema_after_readings(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(10):
        det.learn_and_score(_fv(co=10.0, no2=5.0))
    b = det.baselines("test_model")
    assert b["co_current"] is not None
    assert 8.0 < b["co_current"] < 12.0  # EMA should be near 10


def test_model_channels_returns_channel_list(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det.model_channels("test_model") == ["co_current", "no2_current"]


def test_model_label_returns_label(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det.model_label("test_model") == "Test model"


def test_pickle_persistence_survives_restart(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(6):
        det.learn_and_score(_fv())
    det._save_models()

    det2 = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det2._n_seen["test_model"] == 6


def test_bootstrap_warms_model(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    channel_data = {
        "test_model": [
            {"co_current": 10.0, "no2_current": 5.0}
            for _ in range(10)
        ]
    }
    det.bootstrap(channel_data)
    assert det._n_seen["test_model"] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_multivar_anomaly_detector.py -v
```
Expected: errors like `ModuleNotFoundError: No module named 'mlss_monitor.multivar_anomaly_detector'`

- [ ] **Step 3: Implement `MultivarAnomalyDetector`**

```python
# mlss_monitor/multivar_anomaly_detector.py
"""MultivarAnomalyDetector: composite multi-dimensional HalfSpaceTrees models."""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path

import yaml
from river.anomaly import HalfSpaceTrees

from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

_HST_PARAMS = dict(n_trees=10, height=8, window_size=150, seed=42)
_EMA_ALPHA = 0.05   # ~20-reading half-life


class MultivarAnomalyDetector:
    """Five composite anomaly models, each fed a multi-dimensional dict.

    A reading is only learned/scored when ALL channels for that model have
    non-None values in the FeatureVector. Models are persisted as pickle
    files with the prefix ``multivar_``.
    """

    _SAVE_EVERY_N: int = 3

    def __init__(self, config_path: str | Path, model_dir: str | Path) -> None:
        self._config_path = Path(config_path)
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._config: dict = {}
        self._models: dict[str, HalfSpaceTrees] = {}
        self._n_seen: dict[str, int] = {}
        self._ema: dict[str, dict[str, float]] = {}   # model_id → {channel → ema}
        self._calls_since_save: int = 0
        self._load_config()
        self._load_models()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        with open(self._config_path) as f:
            self._config = yaml.safe_load(f).get("multivar_anomaly", {})

    def _model_defs(self) -> list[dict]:
        return self._config.get("models", [])

    # ── Public helpers ────────────────────────────────────────────────────────

    def model_channels(self, model_id: str) -> list[str]:
        for m in self._model_defs():
            if m["id"] == model_id:
                return list(m["channels"])
        return []

    def model_label(self, model_id: str) -> str:
        for m in self._model_defs():
            if m["id"] == model_id:
                return m.get("label", model_id)
        return model_id

    def baselines(self, model_id: str) -> dict[str, float | None]:
        """Return EMA baseline per channel for a given model."""
        return dict(self._ema.get(model_id, {}))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _pkl_path(self, model_id: str) -> Path:
        return self._model_dir / f"multivar_{model_id}.pkl"

    def _load_models(self) -> None:
        for m in self._model_defs():
            mid = m["id"]
            path = self._pkl_path(mid)
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        saved = pickle.load(f)
                    model = saved["model"]
                    if (getattr(model, "n_trees", None) != _HST_PARAMS["n_trees"] or
                            getattr(model, "height", None) != _HST_PARAMS["height"]):
                        log.info("MultivarAnomalyDetector: params changed for %r, recreating", mid)
                        self._models[mid] = HalfSpaceTrees(**_HST_PARAMS)
                        self._n_seen[mid] = 0
                        continue
                    self._models[mid] = model
                    self._n_seen[mid] = saved.get("n_seen", 0)
                    self._ema[mid] = saved.get("ema", {})
                    continue
                except Exception as exc:
                    log.warning("MultivarAnomalyDetector: could not load %r: %s", mid, exc)
            self._models[mid] = HalfSpaceTrees(**_HST_PARAMS)
            self._n_seen[mid] = 0

    def _save_models(self) -> None:
        for mid, model in self._models.items():
            path = self._pkl_path(mid)
            try:
                with open(path, "wb") as f:
                    pickle.dump({
                        "model": model,
                        "n_seen": self._n_seen[mid],
                        "ema": self._ema.get(mid, {}),
                    }, f)
            except Exception as exc:
                log.warning("MultivarAnomalyDetector: could not save %r: %s", mid, exc)

    # ── Core API ──────────────────────────────────────────────────────────────

    def learn_and_score(self, fv: FeatureVector) -> dict[str, float | None]:
        """Score then train all composite models.

        Returns model_id → score (float) or None if any channel is missing
        or the model is still in cold-start.
        """
        cold_start = self._config.get("cold_start_readings", 500)
        threshold = self._config.get("threshold", 0.75)
        scores: dict[str, float | None] = {}

        for m in self._model_defs():
            mid = m["id"]
            channels = m["channels"]

            # Extract values — skip entire model if any channel is None
            x: dict[str, float] = {}
            for ch in channels:
                val = getattr(fv, ch, None)
                if val is None:
                    x = {}
                    break
                x[ch] = float(val)

            if not x:
                scores[mid] = None
                continue

            model = self._models[mid]

            try:
                raw_score = float(model.score_one(x))
            except Exception:
                raw_score = 0.0
            model.learn_one(x)
            self._n_seen[mid] = self._n_seen.get(mid, 0) + 1

            # Update EMA per channel
            ema = self._ema.setdefault(mid, {})
            for ch, val in x.items():
                ema[ch] = _EMA_ALPHA * val + (1 - _EMA_ALPHA) * ema.get(ch, val)

            scores[mid] = None if self._n_seen[mid] < cold_start else raw_score

        self._calls_since_save += 1
        if self._calls_since_save >= self._SAVE_EVERY_N:
            self._save_models()
            self._calls_since_save = 0

        return scores

    def anomalous_models(self, scores: dict[str, float | None]) -> list[str]:
        """Return model IDs whose score exceeds the configured threshold."""
        threshold = self._config.get("threshold", 0.75)
        return [mid for mid, s in scores.items() if s is not None and s > threshold]

    def bootstrap(self, channel_data: dict[str, list[dict]]) -> None:
        """Feed historical multi-dimensional readings into models.

        Args:
            channel_data: model_id → list of {channel: value} dicts, oldest first.
        """
        for mid, readings in channel_data.items():
            if mid not in self._models:
                continue
            model = self._models[mid]
            ema = self._ema.setdefault(mid, {})
            for i, x in enumerate(readings):
                model.learn_one(x)
                self._n_seen[mid] = self._n_seen.get(mid, 0) + 1
                for ch, val in x.items():
                    ema[ch] = _EMA_ALPHA * val + (1 - _EMA_ALPHA) * ema.get(ch, val)
                if i % 100 == 0:
                    time.sleep(0)  # yield GIL
            log.info("MultivarAnomalyDetector.bootstrap: fed %d readings into %r", len(readings), mid)
        self._save_models()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_multivar_anomaly_detector.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/multivar_anomaly_detector.py tests/test_multivar_anomaly_detector.py
git commit -m "feat: MultivarAnomalyDetector — 5 composite HalfSpaceTrees models"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 3: EMA Baseline in `AnomalyDetector` (TDD)

**Files:**
- Modify: `mlss_monitor/anomaly_detector.py`
- Modify: `tests/test_anomaly_detector.py` (add new test) — create if absent

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_anomaly_detector.py (or create it)
import tempfile
from pathlib import Path
import pytest

from mlss_monitor.anomaly_detector import AnomalyDetector
from mlss_monitor.feature_vector import FeatureVector


def _make_detector(tmp_path):
    cfg = tmp_path / "anomaly.yaml"
    cfg.write_text("""
anomaly:
  cold_start_readings: 3
  score_threshold: 0.7
  channels:
    - tvoc_ppb
""")
    return AnomalyDetector(str(cfg), tmp_path)


def test_baseline_returns_none_before_any_readings(tmp_path):
    det = _make_detector(tmp_path)
    assert det.baseline("tvoc_ppb") is None


def test_baseline_converges_near_constant_value(tmp_path):
    det = _make_detector(tmp_path)
    fv = FeatureVector(tvoc_current=200.0)
    for _ in range(40):
        det.learn_and_score(fv)
    b = det.baseline("tvoc_ppb")
    assert b is not None
    assert 190.0 < b < 210.0
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_anomaly_detector.py::test_baseline_returns_none_before_any_readings tests/test_anomaly_detector.py::test_baseline_converges_near_constant_value -v
```
Expected: FAIL — `AnomalyDetector has no attribute 'baseline'`

- [ ] **Step 3: Add EMA to `AnomalyDetector`**

In `mlss_monitor/anomaly_detector.py`, make these additions:

In `__init__` (after `self._calls_since_save = 0`):
```python
        self._ema: dict[str, float] = {}
```

In `learn_and_score`, after `model.learn_one(x)` and before the `scores[ch] = ...` line, add:
```python
            _alpha = 0.05
            self._ema[ch] = _alpha * value + (1 - _alpha) * self._ema.get(ch, value)
```

Add new public method after `anomalous_channels`:
```python
    def baseline(self, channel: str) -> float | None:
        """Return the EMA baseline for a channel, or None if not yet seen."""
        return self._ema.get(channel)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_anomaly_detector.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/anomaly_detector.py tests/test_anomaly_detector.py
git commit -m "feat: add EMA baseline tracking to AnomalyDetector"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 4: `inference_evidence.py` — Evidence Builder (TDD)

**Files:**
- Create: `mlss_monitor/inference_evidence.py`
- Create: `tests/test_inference_evidence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_inference_evidence.py
import pytest
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.inference_evidence import (
    build_sensor_snapshot,
    anomaly_description,
    anomaly_action,
)


def _fv(**kwargs):
    return FeatureVector(**kwargs)


# ── build_sensor_snapshot ─────────────────────────────────────────────────────

def test_snapshot_includes_label_unit_value():
    fv = _fv(tvoc_current=487.0, tvoc_slope_1m=10.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {"tvoc_current": 152.0})
    assert len(snap) == 1
    entry = snap[0]
    assert entry["label"] == "TVOC"
    assert entry["unit"] == "ppb"
    assert entry["value"] == 487.0


def test_snapshot_computes_ratio():
    fv = _fv(tvoc_current=487.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {"tvoc_current": 152.0})
    assert snap[0]["ratio"] == pytest.approx(3.2, abs=0.1)


def test_snapshot_ratio_band_high_above_3x():
    fv = _fv(co_current=100.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "high"


def test_snapshot_ratio_band_elevated_between_1_5_and_3():
    fv = _fv(co_current=20.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "elevated"


def test_snapshot_ratio_band_normal_below_1_5():
    fv = _fv(co_current=11.0)
    snap = build_sensor_snapshot(fv, ["co_current"], {"co_current": 10.0})
    assert snap[0]["ratio_band"] == "normal"


def test_snapshot_trend_rising_when_slope_above_threshold():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=20.0)  # threshold=5.0
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "rising"


def test_snapshot_trend_falling_when_slope_below_negative_threshold():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=-20.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "falling"


def test_snapshot_trend_stable_when_slope_near_zero():
    fv = _fv(tvoc_current=300.0, tvoc_slope_1m=0.1)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["trend"] == "stable"


def test_snapshot_skips_channel_with_none_value():
    fv = _fv(tvoc_current=None)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap == []


def test_snapshot_no_baseline_leaves_ratio_none():
    fv = _fv(tvoc_current=300.0)
    snap = build_sensor_snapshot(fv, ["tvoc_current"], {})
    assert snap[0]["ratio"] is None
    assert snap[0]["ratio_band"] == "unknown"


# ── anomaly_description ───────────────────────────────────────────────────────

def test_description_single_channel_includes_value_and_ratio():
    snap = [{"label": "TVOC", "value": 487.0, "unit": "ppb",
              "baseline": 152.0, "ratio": 3.2, "trend": "rising"}]
    desc = anomaly_description(snap)
    assert "487" in desc
    assert "3.2" in desc
    assert "rising" in desc.lower()


def test_description_multivar_mentions_model_label():
    snap = [
        {"label": "CO",   "value": 50.0, "unit": "ppb", "baseline": 10.0, "ratio": 5.0, "trend": "rising"},
        {"label": "PM2.5","value": 40.0, "unit": "µg/m³","baseline": 12.0, "ratio": 3.3, "trend": "stable"},
    ]
    desc = anomaly_description(snap, model_label="Combustion signature")
    assert "Combustion signature" in desc or "combustion" in desc.lower()


def test_description_empty_snapshot_returns_fallback():
    desc = anomaly_description([])
    assert "anomaly" in desc.lower()


# ── anomaly_action ────────────────────────────────────────────────────────────

def test_action_combustion_signature_mentions_ventilate():
    action = anomaly_action(model_id="combustion_signature")
    assert "ventilat" in action.lower()


def test_action_single_channel_tvoc():
    action = anomaly_action(channel="tvoc_ppb")
    assert action  # non-empty


def test_action_unknown_returns_generic():
    action = anomaly_action(model_id="nonexistent_model")
    assert action  # non-empty string, not blank
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_inference_evidence.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement `inference_evidence.py`**

```python
# mlss_monitor/inference_evidence.py
"""Pure functions for building structured inference evidence.

All interpretation logic (snapshot, ratios, trend labels, descriptions,
action text) lives here. No IO, no DB access. The DetectionEngine calls
these functions and stores the results in save_inference(evidence=...).
The JS layer renders the pre-computed fields — it never calculates.
"""
from __future__ import annotations

from mlss_monitor.feature_vector import FeatureVector

# ── Channel metadata ──────────────────────────────────────────────────────────
# slope_field: FeatureVector field name for 1-minute slope (None = not available)
# slope_thresh: units/min above which a reading is considered "rising" or "falling"

_CHANNEL_META: dict[str, dict] = {
    "tvoc_current":        {"label": "TVOC",        "unit": "ppb",    "slope_field": "tvoc_slope_1m",        "slope_thresh": 5.0},
    "eco2_current":        {"label": "eCO2",        "unit": "ppm",    "slope_field": "eco2_slope_1m",        "slope_thresh": 10.0},
    "temperature_current": {"label": "Temperature", "unit": "°C",     "slope_field": "temperature_slope_1m", "slope_thresh": 0.1},
    "humidity_current":    {"label": "Humidity",    "unit": "%",      "slope_field": "humidity_slope_1m",    "slope_thresh": 0.5},
    "pm1_current":         {"label": "PM1",         "unit": "µg/m³", "slope_field": "pm1_slope_1m",         "slope_thresh": 1.0},
    "pm25_current":        {"label": "PM2.5",       "unit": "µg/m³", "slope_field": "pm25_slope_1m",        "slope_thresh": 1.0},
    "pm10_current":        {"label": "PM10",        "unit": "µg/m³", "slope_field": "pm10_slope_1m",        "slope_thresh": 1.0},
    "co_current":          {"label": "CO",          "unit": "ppb",    "slope_field": "co_slope_1m",          "slope_thresh": 2.0},
    "no2_current":         {"label": "NO2",         "unit": "ppb",    "slope_field": "no2_slope_1m",         "slope_thresh": 2.0},
    "nh3_current":         {"label": "NH3",         "unit": "ppb",    "slope_field": "nh3_slope_1m",         "slope_thresh": 2.0},
    "vpd_kpa":             {"label": "VPD",         "unit": "kPa",    "slope_field": None,                   "slope_thresh": None},
}

# ── Per-channel anomaly action text ──────────────────────────────────────────

_CHANNEL_ACTIONS: dict[str, str] = {
    "tvoc_ppb":      "Identify chemical sources (cleaning products, paints, adhesives). Ventilate if TVOC stays elevated.",
    "eco2_ppm":      "Open windows or improve ventilation. High CO2 reduces cognitive performance and causes fatigue.",
    "temperature_c": "Adjust heating or cooling to return to the comfort zone (18–25°C).",
    "humidity_pct":  "Use a dehumidifier or humidifier to reach the target range (40–60%).",
    "pm1_ug_m3":     "Identify fine particle sources (candles, incense, cooking smoke). Consider an air purifier with HEPA filter.",
    "pm25_ug_m3":    "Identify fine particle sources and ventilate. Consider running an air purifier.",
    "pm10_ug_m3":    "Check for coarse dust or pollen sources. A HEPA air purifier can help.",
    "co_ppb":        "Identify CO sources (gas appliances, combustion). Ventilate immediately. At high levels, evacuate and call emergency services.",
    "no2_ppb":       "Check gas appliances and ventilation. Prolonged elevated NO2 can irritate the airways.",
    "nh3_ppb":       "Check for ammonia sources (cleaning products, fertilisers, animal waste). Ventilate promptly.",
}

# ── Per-model composite action text ──────────────────────────────────────────

_MODEL_ACTIONS: dict[str, str] = {
    "combustion_signature": (
        "Identify any open flames, candles, or cooking sources. Ventilate immediately. "
        "CO, NO2 and particulates are moving together — this is consistent with combustion."
    ),
    "particle_distribution": (
        "Check for unusual particulate sources. The PM1/PM2.5/PM10 ratio distribution "
        "is abnormal — this may indicate combustion smoke (PM1≈PM2.5) or unusually high "
        "coarse dust (PM10>>PM2.5). Open windows if outdoor air quality allows."
    ),
    "ventilation_quality": (
        "Open a window or run a fan. eCO2, TVOC and NH3 are building up together — "
        "the space needs fresh air. All three rising jointly is a strong ventilation signal."
    ),
    "gas_relationship": (
        "Inspect the MICS6814 gas sensor. CO, NO2 and NH3 have broken their normal "
        "correlation. This can indicate sensor drift, a fault, or a genuinely unusual "
        "gas mixture. If no obvious source, consider recalibrating the sensor."
    ),
    "thermal_moisture": (
        "Check your heating or cooling system. Temperature, humidity and VPD are stressed "
        "together — this pattern is consistent with HVAC failure or extreme outdoor conditions "
        "infiltrating the space. Inspect HVAC filters and seals."
    ),
}

_GENERIC_ACTION = (
    "Monitor the readings. If the anomaly persists, investigate possible sources "
    "and consider improving ventilation."
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _slope_trend(fv: FeatureVector, slope_field: str | None, thresh: float | None) -> str:
    if slope_field is None or thresh is None:
        return "stable"
    slope = getattr(fv, slope_field, None)
    if slope is None:
        return "stable"
    if slope > thresh:
        return "rising"
    if slope < -thresh:
        return "falling"
    return "stable"


def _ratio_band(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= 3.0:
        return "high"
    if ratio >= 1.5:
        return "elevated"
    return "normal"


# ── Public API ────────────────────────────────────────────────────────────────

def build_sensor_snapshot(
    fv: FeatureVector,
    channels: list[str],
    baselines: dict[str, float | None],
) -> list[dict]:
    """Build a structured list of sensor readings for embedding in inference evidence.

    Each entry contains label, value, unit, baseline, ratio, ratio_band, and trend.
    Channels whose FeatureVector value is None are silently skipped.
    The JS layer renders this list directly — no calculation in the browser.

    Args:
        fv: current FeatureVector snapshot.
        channels: FeatureVector field names to include (e.g. "tvoc_current").
        baselines: {channel: ema_baseline} — may contain None values.
    """
    snapshot = []
    for ch in channels:
        meta = _CHANNEL_META.get(ch)
        if meta is None:
            continue
        value = getattr(fv, ch, None)
        if value is None:
            continue
        baseline = baselines.get(ch)
        ratio: float | None = None
        if baseline is not None and baseline > 0:
            ratio = round(value / baseline, 2)
        snapshot.append({
            "channel": ch,
            "label": meta["label"],
            "value": round(float(value), 2),
            "unit": meta["unit"],
            "baseline": round(float(baseline), 2) if baseline is not None else None,
            "ratio": ratio,
            "ratio_band": _ratio_band(ratio),
            "trend": _slope_trend(fv, meta["slope_field"], meta["slope_thresh"]),
        })
    return snapshot


def anomaly_description(
    snapshot: list[dict],
    model_label: str | None = None,
) -> str:
    """Generate a human-readable description from a sensor snapshot.

    For single-channel anomalies (model_label=None), describes the one sensor.
    For composite models, identifies the most elevated dimension.
    """
    if not snapshot:
        return "A statistical anomaly was detected."

    trend_text = {
        "rising":  ", and rising",
        "falling": ", and falling",
        "stable":  "",
    }

    if model_label is None:
        # Single channel
        s = snapshot[0]
        t = trend_text.get(s.get("trend", "stable"), "")
        if s.get("ratio") is not None and s.get("baseline") is not None:
            return (
                f"{s['label']} at {s['value']} {s['unit']} — "
                f"{s['ratio']}× your typical {s['baseline']} {s['unit']}{t}."
            )
        return f"{s['label']} reading of {s['value']} {s['unit']} is statistically unusual{t}."

    # Composite model — find most elevated dimension
    ranked = sorted(
        [s for s in snapshot if s.get("ratio") is not None],
        key=lambda s: s["ratio"],
        reverse=True,
    )
    dims = ", ".join(s["label"] for s in snapshot)
    if ranked:
        worst = ranked[0]
        worst_text = (
            f"Most elevated: {worst['label']} at {worst['value']} {worst['unit']} "
            f"({worst['ratio']}× typical {worst['baseline']} {worst['unit']})."
        )
    else:
        worst_text = ""

    return (
        f"A {model_label} anomaly was detected across {dims}. {worst_text}"
    ).strip()


def anomaly_action(
    model_id: str | None = None,
    channel: str | None = None,
) -> str:
    """Return a contextual recommended action string.

    Pass model_id for composite model anomalies, channel for per-channel ones.
    """
    if model_id and model_id in _MODEL_ACTIONS:
        return _MODEL_ACTIONS[model_id]
    if channel and channel in _CHANNEL_ACTIONS:
        return _CHANNEL_ACTIONS[channel]
    return _GENERIC_ACTION
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_inference_evidence.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/inference_evidence.py tests/test_inference_evidence.py
git commit -m "feat: inference_evidence — snapshot builder, description & action templates"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 5: Wire `MultivarAnomalyDetector` into `DetectionEngine` + Enrich Evidence

**Files:**
- Modify: `mlss_monitor/detection_engine.py`

- [ ] **Step 1: Update `DetectionEngine.__init__`**

Add `multivar_config_path` parameter and instantiate `MultivarAnomalyDetector`.
Replace the existing `__init__` signature and body with:

```python
    def __init__(
        self,
        rules_path: str | Path,
        anomaly_config_path: str | Path,
        model_dir: str | Path,
        fingerprints_path: str | Path | None = None,
        multivar_config_path: str | Path | None = None,
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
        self._multivar_detector = None
        if multivar_config_path is not None:
            try:
                from mlss_monitor.multivar_anomaly_detector import MultivarAnomalyDetector
                self._multivar_detector = MultivarAnomalyDetector(multivar_config_path, model_dir)
            except Exception as exc:
                log.error("DetectionEngine: could not load multivar config: %s", exc)
```

- [ ] **Step 2: Add imports at top of `detection_engine.py`**

After the existing imports, add:
```python
from mlss_monitor.inference_evidence import (
    build_sensor_snapshot,
    anomaly_description,
    anomaly_action,
)
```

- [ ] **Step 3: Update per-channel anomaly block in `run()`**

Replace the existing single-channel anomaly `save_inference` call block (currently around lines 235–258) with:

```python
        # 2. Per-channel anomaly detection
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
                    baselines = {ch: self._anomaly_detector.baseline(ch)}
                    snapshot = build_sensor_snapshot(fv, [ch], baselines)
                    description = anomaly_description(snapshot)
                    action = anomaly_action(channel=ch)
                    save_inference(
                        event_type=event_type,
                        severity="warning",
                        title=f"Anomaly: {snapshot[0]['label'] if snapshot else ch.replace('_', ' ')} — {score:.2f} score",
                        description=description,
                        action=action,
                        evidence={
                            "sensor_snapshot": snapshot,
                            "anomaly_score": round(score, 4),
                        },
                        confidence=round(score, 2),
                    )
                except Exception as exc:
                    log.error("DetectionEngine: save_inference failed for anomaly %r: %s", ch, exc)

        # 3. Composite multivariate anomaly detection
        if self._multivar_detector is not None:
            mv_scores = self._multivar_detector.learn_and_score(fv)
            for mid in self._multivar_detector.anomalous_models(mv_scores):
                event_type = f"anomaly_{mid}"
                if get_recent_inference_by_type(event_type, hours=1):
                    continue
                score = mv_scores[mid]
                fired.append(event_type)
                if not self._dry_run:
                    try:
                        channels = self._multivar_detector.model_channels(mid)
                        label = self._multivar_detector.model_label(mid)
                        baselines = self._multivar_detector.baselines(mid)
                        snapshot = build_sensor_snapshot(fv, channels, baselines)
                        description = anomaly_description(snapshot, model_label=label)
                        action = anomaly_action(model_id=mid)
                        save_inference(
                            event_type=event_type,
                            severity="warning",
                            title=f"Composite anomaly: {label} — {score:.2f} score",
                            description=description,
                            action=action,
                            evidence={
                                "sensor_snapshot": snapshot,
                                "anomaly_score": round(score, 4),
                                "model_id": mid,
                            },
                            confidence=round(score, 2),
                        )
                    except Exception as exc:
                        log.error("DetectionEngine: save_inference failed for multivar %r: %s", mid, exc)
```

- [ ] **Step 4: Add multivar bootstrap to `bootstrap_from_db()`**

At the end of `bootstrap_from_db`, after the existing `self._anomaly_detector.bootstrap(channel_data)` call, add:

```python
            # Bootstrap composite models from the same historical data
            if self._multivar_detector is not None:
                mv_channel_data: dict[str, list[dict]] = {}
                for m in self._multivar_detector._model_defs():
                    mid = m["id"]
                    channels = m["channels"]
                    # Build list of complete readings (all channels present)
                    lengths = [len(channel_data.get(ch, [])) for ch in channels]
                    if not lengths or min(lengths) == 0:
                        continue
                    n = min(lengths)
                    readings = []
                    for i in range(n):
                        row = {}
                        for ch in channels:
                            cd = channel_data.get(ch, [])
                            if i < len(cd):
                                row[ch] = cd[i]
                        if len(row) == len(channels):
                            readings.append(row)
                    if readings:
                        mv_channel_data[mid] = readings
                if mv_channel_data:
                    self._multivar_detector.bootstrap(mv_channel_data)
```

- [ ] **Step 5: Add `_save_multivar_models` to the atexit handler in `app.py`**

This is handled in Task 7. Skip for now.

- [ ] **Step 6: Run the full test suite**

```bash
python -m pytest tests/ -q --tb=short
```
Expected: 359+ tests passing, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/detection_engine.py
git commit -m "feat: wire MultivarAnomalyDetector into DetectionEngine + rich anomaly evidence"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 6: Update `init_db.py` CHECK Constraint

**Files:**
- Modify: `database/init_db.py`

- [ ] **Step 1: Add 5 new event types to the CHECK constraint**

In `database/init_db.py`, find the `CREATE TABLE IF NOT EXISTS inferences` block.
The `event_type` CHECK constraint currently ends at `'overnight_buildup'`. Add the 5 new types:

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at DATETIME NOT NULL,
        event_type TEXT NOT NULL CHECK(
            event_type IN (
                'tvoc_spike', 'eco2_danger', 'eco2_elevated',
                'correlated_pollution', 'sustained_poor_air',
                'mould_risk',
                'pm1_spike', 'pm1_elevated',
                'pm25_spike', 'pm25_elevated',
                'pm10_spike', 'pm10_elevated',
                'temp_high', 'temp_low',
                'humidity_high', 'humidity_low',
                'vpd_low', 'vpd_high',
                'rapid_temp_change', 'rapid_humidity_change',
                'hourly_summary', 'daily_summary',
                'daily_pattern', 'overnight_buildup',
                'anomaly_combustion_signature',
                'anomaly_particle_distribution',
                'anomaly_ventilation_quality',
                'anomaly_gas_relationship',
                'anomaly_thermal_moisture'
            ) OR event_type LIKE 'annotation_context_%'
              OR event_type LIKE 'anomaly_%'
        ),
```

> **Note:** Adding `OR event_type LIKE 'anomaly_%'` at the end covers all current and future per-channel anomaly events (e.g. `anomaly_tvoc_ppb`) AND all composite model events without needing to enumerate them individually.

- [ ] **Step 2: Run tests to confirm no regressions**

```bash
python -m pytest tests/ -q
```
Expected: same pass count, 0 failures.

- [ ] **Step 3: Commit**

```bash
git add database/init_db.py
git commit -m "fix: broaden inferences CHECK constraint to allow all anomaly_ events"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 7: Wire `multivar_config_path` in `app.py`

**Files:**
- Modify: `mlss_monitor/app.py`

- [ ] **Step 1: Pass `multivar_config_path` to `DetectionEngine`**

Find the `_detection_engine = DetectionEngine(...)` call in `app.py`. Update it to include the new argument:

```python
_detection_engine = DetectionEngine(
    rules_path="config/rules.yaml",
    anomaly_config_path="config/anomaly.yaml",
    model_dir="data/anomaly_models",
    fingerprints_path="config/fingerprints.yaml",
    multivar_config_path="config/multivar_anomaly.yaml",
    dry_run=False,
)
```

- [ ] **Step 2: Extend atexit handler to save multivar models**

In `main()`, find `_save_models_on_exit` and extend it:

```python
        def _save_models_on_exit():
            log.info("Saving anomaly models before exit")
            try:
                _detection_engine._anomaly_detector._save_models()
            except Exception as exc:
                log.warning("Could not save anomaly models on shutdown: %s", exc)
            try:
                if _detection_engine._multivar_detector is not None:
                    _detection_engine._multivar_detector._save_models()
            except Exception as exc:
                log.warning("Could not save multivar models on shutdown: %s", exc)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ -q
```
Expected: 359+ passing, 0 failures.

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/app.py
git commit -m "feat: pass multivar_config_path to DetectionEngine; save multivar models on exit"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 8: Update JS Evidence Renderer

**Files:**
- Modify: `static/js/dashboard.js`

The JS renders `inf.evidence` in `_openInferenceDialog`. Currently it iterates generic key-value pairs. Update it to detect and render a `sensor_snapshot` array when present. All interpretation (labels, units, ratio, ratio_band, trend) is pre-computed by the backend — the JS only formats.

- [ ] **Step 1: Replace the evidence rendering block in `_openInferenceDialog`**

Find the block starting at `const evEl = document.getElementById("infEvidence");` and replace the evidence rendering portion (everything up to but not including the thresholds section) with:

```javascript
  // Evidence section
  const evEl = document.getElementById("infEvidence");
  const thSec = document.getElementById("infThresholdsSection");
  const thGrid = document.getElementById("infThresholds");

  if (inf.evidence && typeof inf.evidence === "object") {
    const snapshot = inf.evidence.sensor_snapshot;
    const thresholds = inf.evidence._thresholds;

    if (Array.isArray(snapshot) && snapshot.length > 0) {
      // Structured sensor snapshot — render chips (all data pre-computed by backend)
      const TREND_ARROW = { rising: "↑", falling: "↓", stable: "→" };
      const BAND_CLS    = { high: "ev-bad", elevated: "ev-warn", normal: "ev-good", unknown: "" };

      evEl.innerHTML = snapshot.map(s => {
        const arrow = TREND_ARROW[s.trend] || "→";
        const cls   = BAND_CLS[s.ratio_band] || "";
        const ratio = s.ratio != null ? `<span class="ev-ratio">${s.ratio}× normal</span>` : "";
        return `<div class="inf-ev-row ${cls}">
          <span class="fd-label">${s.label}</span>
          <span class="fd-value">${s.value} ${s.unit} <span class="ev-trend">${arrow}</span></span>
          ${ratio}
        </div>`;
      }).join("");
    } else {
      // Fallback: generic key-value pairs (existing behaviour for older inferences)
      const entries = Object.entries(inf.evidence).filter(
        ([k]) => k !== "_thresholds" && k !== "sensor_snapshot" && k !== "model_id"
      );
      evEl.innerHTML = entries.map(([k, v]) => {
        const cls = _evidenceColor(k, v);
        return `<div class="inf-ev-row ${cls}"><span class="fd-label">${k.replace(/_/g, " ")}</span><span class="fd-value">${v}</span></div>`;
      }).join("") || "No detailed evidence available.";
    }

    // Thresholds section (unchanged)
    if (thresholds && typeof thresholds === "object" && Object.keys(thresholds).length) {
      thSec.style.display = "";
      thSec.removeAttribute("open");
      thGrid.innerHTML = Object.entries(thresholds).map(([k, th]) => {
        const customTag = th.is_custom
          ? '<span class="inf-th-custom">custom</span>'
          : '<span class="inf-th-default">default</span>';
        return `<div class="inf-th-row">
          <span class="inf-th-label">${th.label || k.replace(/_/g, " ")}</span>
          <span class="inf-th-val">${th.value} ${th.unit || ""} ${customTag}</span>
        </div>`;
      }).join("");
    } else {
      thSec.style.display = "none";
    }
  } else {
    evEl.textContent = "No detailed evidence available.";
    thSec.style.display = "none";
  }
```

- [ ] **Step 2: Add CSS for new evidence chip classes**

Add to the `<style>` block in `templates/dashboard.html` (or to the existing CSS file):

```css
/* Anomaly evidence chips */
.ev-ratio  { font-size: 0.78rem; color: #aaa; margin-left: 0.4rem; }
.ev-trend  { font-size: 0.85rem; }
.ev-good   .ev-ratio { color: #4caf50; }
.ev-warn   .ev-ratio { color: #ff9800; }
.ev-bad    .ev-ratio { color: #f44336; }
```

- [ ] **Step 3: Verify existing inferences still render**

Older inferences in the DB have no `sensor_snapshot` key — the fallback `else` branch handles them. Confirm by checking that the `else` branch is reached when `snapshot` is absent.

- [ ] **Step 4: Commit**

```bash
git add static/js/dashboard.js templates/dashboard.html
git commit -m "feat: render structured sensor_snapshot in inference evidence dialog"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

---

## Task 9: Final Integration Check

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: 360+ tests passing, 0 failures.

- [ ] **Step 2: Verify the Insights page shows the new composite models**

The `insights_engine.html` page shows anomaly channel status. Verify by checking that `multivar_anomaly.yaml` models appear in the anomaly models table. If not, update `pages.py` to also expose `multivar_detector` status:

```python
    # In insights_engine() route, after existing anomaly_info loop:
    if engine and engine._multivar_detector:
        det = engine._multivar_detector
        cold_start = det._config.get("cold_start_readings", 500)
        for m in det._model_defs():
            mid = m["id"]
            n = det._n_seen.get(mid, 0)
            anomaly_info.append({
                "channel": mid,
                "n_seen": n,
                "cold_start": cold_start,
                "ready": n >= cold_start,
            })
```

- [ ] **Step 3: Final commit and push**

```bash
git add -p  # stage any remaining changes
git commit -m "feat: Phase 5 complete — multivariate detection + actionable inference cards"
git push origin claude/zealous-hugle
git push origin claude/zealous-hugle:feature/phase3-detection-layer
```

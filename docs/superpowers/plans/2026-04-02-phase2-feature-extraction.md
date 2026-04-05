# Phase 2: Feature Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `FeatureVector` dataclass and `FeatureExtractor` that reads the hot tier + cold tier baselines to produce per-sensor and cross-sensor features consumed by the detection layer in Phase 3.

**Architecture:** `FeatureExtractor.extract(hot_readings, baselines)` → `FeatureVector`. Hot tier provides the last 60 minutes at 1s resolution. Cold tier provides 24h median baselines. The FeatureVector is stored on `state.feature_vector` after each cold-tier write cycle for Phase 3 consumers. Zero changes to existing inference output.

**Tech Stack:** Python 3.11, `statistics.linear_regression` (stdlib), SQLite via existing `database/db_logger.py`, `pytest`.

**Note on spec inconsistency:** The spec's rule example shows `temperature_c` but the FeatureVector uses `temperature_current`. Phase 3 `rules.yaml` must use `temperature_current` — `temperature_c` is a copy/paste error in the spec.

---

## Context (read before touching any file)

**Branch:** `claude/zealous-hugle`

**Phase 1 deliverables (complete, do not modify):**
- `mlss_monitor/data_sources/base.py` — `DataSource` ABC, `NormalisedReading` dataclass, `merge_readings()`
- `mlss_monitor/hot_tier.py` — `HotTier` ring buffer
- `mlss_monitor/state.py` — `state.hot_tier` set to `HotTier` instance on startup
- `tests/test_data_sources.py`, `tests/test_hot_tier.py` — 31 tests passing

**Key DB column names** (from `database/init_db.py`):
```
sensor_data table: timestamp, temperature, humidity, eco2, tvoc, pm2_5, gas_co, gas_no2, gas_nh3
```

**NormalisedReading field names** (from `mlss_monitor/data_sources/base.py`):
```
tvoc_ppb, eco2_ppm, temperature_c, humidity_pct, pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb
```

**Mapping — NormalisedReading field → FeatureVector prefix → DB column:**
```
tvoc_ppb       → tvoc        → tvoc
eco2_ppm       → eco2        → eco2
temperature_c  → temperature → temperature
humidity_pct   → humidity    → humidity
pm25_ug_m3     → pm25        → pm2_5
co_ppb         → co          → gas_co
no2_ppb        → no2         → gas_no2
nh3_ppb        → nh3         → gas_nh3
```

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `mlss_monitor/data_sources/base.py` | Hoist `_SENSOR_FIELDS` to module-level constant |
| Create | `mlss_monitor/feature_vector.py` | `FeatureVector` dataclass (85 fields) |
| Create | `mlss_monitor/feature_extractor.py` | `FeatureExtractor` class + private helpers |
| Modify | `database/db_logger.py` | Add `get_24h_baselines()` |
| Modify | `mlss_monitor/state.py` | Add `feature_vector = None` |
| Modify | `mlss_monitor/app.py` | Compute + store FeatureVector in cold-tier cycle |
| Create | `tests/test_feature_extractor.py` | Unit tests for FeatureExtractor |
| Modify | `tests/test_db_logger.py` or create it | Tests for `get_24h_baselines` |

---

## Task 1: Hoist `_SENSOR_FIELDS` + `FeatureVector` dataclass

**Files:**
- Modify: `mlss_monitor/data_sources/base.py`
- Create: `mlss_monitor/feature_vector.py`
- Create: `tests/test_feature_extractor.py` (stub)

### Step 1.1 — Hoist `_SENSOR_FIELDS` in `base.py`

- [ ] Read `mlss_monitor/data_sources/base.py`

- [ ] Move `_SENSOR_FIELDS` from inside `merge_readings()` to module level. Replace:

```python
def merge_readings(readings: list[NormalisedReading]) -> NormalisedReading:
    """Merge multiple NormalisedReadings into one.
    First non-None value wins per field. Timestamp is utcnow().
    """
    _SENSOR_FIELDS = (
        "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
        "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
    )
```

with:

```python
SENSOR_FIELDS: tuple[str, ...] = (
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
)


def merge_readings(readings: list[NormalisedReading]) -> NormalisedReading:
    """Merge multiple NormalisedReadings into one.
    First non-None value wins per field. Timestamp is utcnow().
    """
```

Inside `merge_readings`, change `_SENSOR_FIELDS` → `SENSOR_FIELDS`:
```python
    merged: dict = {f: None for f in SENSOR_FIELDS}
    for reading in readings:
        for field_name in SENSOR_FIELDS:
```

Also update `mlss_monitor/data_sources/__init__.py` to export `SENSOR_FIELDS`:
```python
from .base import DataSource, NormalisedReading, merge_readings, SENSOR_FIELDS

__all__ = [
    "DataSource", "NormalisedReading", "merge_readings", "SENSOR_FIELDS",
    "SGP30Source", "AHT20Source",
    "ParticulateSource", "MICS6814Source",
    "WeatherAPISource",
]
```

- [ ] Run: `python -m pytest tests/test_data_sources.py tests/test_hot_tier.py -v`
  Expected: all 31 tests PASS (no regressions)

### Step 1.2 — Write failing FeatureVector import test

- [ ] Create `tests/test_feature_extractor.py`:

```python
from datetime import datetime, timezone
from mlss_monitor.feature_vector import FeatureVector


def test_feature_vector_all_none():
    fv = FeatureVector(timestamp=datetime.now(timezone.utc))
    assert fv.tvoc_current is None
    assert fv.tvoc_baseline is None
    assert fv.tvoc_slope_1m is None
    assert fv.tvoc_slope_5m is None
    assert fv.tvoc_slope_30m is None
    assert fv.tvoc_elevated_minutes is None
    assert fv.tvoc_peak_ratio is None
    assert fv.tvoc_is_declining is None
    assert fv.tvoc_decay_rate is None
    assert fv.tvoc_pulse_detected is None
    assert fv.nh3_lag_behind_tvoc_seconds is None
    assert fv.pm25_correlated_with_tvoc is None
    assert fv.co_correlated_with_tvoc is None
    assert fv.vpd_kpa is None


def test_feature_vector_with_values():
    fv = FeatureVector(
        timestamp=datetime.now(timezone.utc),
        tvoc_current=450.0,
        tvoc_baseline=200.0,
        tvoc_slope_1m=5.2,
        tvoc_is_declining=False,
        vpd_kpa=0.8,
    )
    assert fv.tvoc_current == 450.0
    assert fv.tvoc_baseline == 200.0
    assert fv.tvoc_slope_1m == 5.2
    assert fv.tvoc_is_declining is False
    assert fv.vpd_kpa == 0.8
    assert fv.eco2_current is None  # unset fields default to None
```

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: FAIL with `ModuleNotFoundError`

### Step 1.3 — Create `mlss_monitor/feature_vector.py`

- [ ] Create `mlss_monitor/feature_vector.py` with the full dataclass:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FeatureVector:
    """Pre-computed temporal features for the detection and attribution layers.

    All fields default to None. None means insufficient data — detection rules
    and attribution scoring skip None fields gracefully.
    """
    timestamp: datetime

    # ── TVOC (ppb) ────────────────────────────────────────────────────────────
    tvoc_current:           float | None = None
    tvoc_baseline:          float | None = None
    tvoc_slope_1m:          float | None = None  # ppb/min
    tvoc_slope_5m:          float | None = None
    tvoc_slope_30m:         float | None = None
    tvoc_elevated_minutes:  float | None = None
    tvoc_peak_ratio:        float | None = None  # current / baseline
    tvoc_is_declining:      bool  | None = None
    tvoc_decay_rate:        float | None = None  # ppb/min, negative when declining
    tvoc_pulse_detected:    bool  | None = None

    # ── eCO2 (ppm) ───────────────────────────────────────────────────────────
    eco2_current:           float | None = None
    eco2_baseline:          float | None = None
    eco2_slope_1m:          float | None = None
    eco2_slope_5m:          float | None = None
    eco2_slope_30m:         float | None = None
    eco2_elevated_minutes:  float | None = None
    eco2_peak_ratio:        float | None = None
    eco2_is_declining:      bool  | None = None
    eco2_decay_rate:        float | None = None
    eco2_pulse_detected:    bool  | None = None

    # ── Temperature (°C) ─────────────────────────────────────────────────────
    temperature_current:          float | None = None
    temperature_baseline:         float | None = None
    temperature_slope_1m:         float | None = None
    temperature_slope_5m:         float | None = None
    temperature_slope_30m:        float | None = None
    temperature_elevated_minutes: float | None = None
    temperature_peak_ratio:       float | None = None
    temperature_is_declining:     bool  | None = None
    temperature_decay_rate:       float | None = None
    temperature_pulse_detected:   bool  | None = None

    # ── Humidity (%) ─────────────────────────────────────────────────────────
    humidity_current:           float | None = None
    humidity_baseline:          float | None = None
    humidity_slope_1m:          float | None = None
    humidity_slope_5m:          float | None = None
    humidity_slope_30m:         float | None = None
    humidity_elevated_minutes:  float | None = None
    humidity_peak_ratio:        float | None = None
    humidity_is_declining:      bool  | None = None
    humidity_decay_rate:        float | None = None
    humidity_pulse_detected:    bool  | None = None

    # ── PM2.5 (µg/m³) ────────────────────────────────────────────────────────
    pm25_current:           float | None = None
    pm25_baseline:          float | None = None
    pm25_slope_1m:          float | None = None
    pm25_slope_5m:          float | None = None
    pm25_slope_30m:         float | None = None
    pm25_elevated_minutes:  float | None = None
    pm25_peak_ratio:        float | None = None
    pm25_is_declining:      bool  | None = None
    pm25_decay_rate:        float | None = None
    pm25_pulse_detected:    bool  | None = None

    # ── CO (ppb) ─────────────────────────────────────────────────────────────
    co_current:           float | None = None
    co_baseline:          float | None = None
    co_slope_1m:          float | None = None
    co_slope_5m:          float | None = None
    co_slope_30m:         float | None = None
    co_elevated_minutes:  float | None = None
    co_peak_ratio:        float | None = None
    co_is_declining:      bool  | None = None
    co_decay_rate:        float | None = None
    co_pulse_detected:    bool  | None = None

    # ── NO2 (ppb) ────────────────────────────────────────────────────────────
    no2_current:           float | None = None
    no2_baseline:          float | None = None
    no2_slope_1m:          float | None = None
    no2_slope_5m:          float | None = None
    no2_slope_30m:         float | None = None
    no2_elevated_minutes:  float | None = None
    no2_peak_ratio:        float | None = None
    no2_is_declining:      bool  | None = None
    no2_decay_rate:        float | None = None
    no2_pulse_detected:    bool  | None = None

    # ── NH3 (ppb) ────────────────────────────────────────────────────────────
    nh3_current:           float | None = None
    nh3_baseline:          float | None = None
    nh3_slope_1m:          float | None = None
    nh3_slope_5m:          float | None = None
    nh3_slope_30m:         float | None = None
    nh3_elevated_minutes:  float | None = None
    nh3_peak_ratio:        float | None = None
    nh3_is_declining:      bool  | None = None
    nh3_decay_rate:        float | None = None
    nh3_pulse_detected:    bool  | None = None

    # ── Cross-sensor ─────────────────────────────────────────────────────────
    nh3_lag_behind_tvoc_seconds: float | None = None  # 0–120 s; None = no correlated spike
    pm25_correlated_with_tvoc:   bool  | None = None
    co_correlated_with_tvoc:     bool  | None = None

    # ── Derived ──────────────────────────────────────────────────────────────
    vpd_kpa: float | None = None
```

### Step 1.4 — Run tests

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: 2 tests PASS

### Step 1.5 — Commit

```bash
git add mlss_monitor/data_sources/base.py mlss_monitor/data_sources/__init__.py \
        mlss_monitor/feature_vector.py tests/test_feature_extractor.py
git commit -m "feat: add FeatureVector dataclass and hoist SENSOR_FIELDS to module level"
```

---

## Task 2: `FeatureExtractor` — per-sensor feature helpers + extraction

**Files:**
- Create: `mlss_monitor/feature_extractor.py`
- Modify: `tests/test_feature_extractor.py`

This task covers all 10 per-sensor features for all 8 sensors. The helpers are pure functions, tested in isolation. The `FeatureExtractor.extract()` call is tested end-to-end with synthetic `NormalisedReading` lists.

### Step 2.1 — Write failing tests for helpers

- [ ] Add to `tests/test_feature_extractor.py`:

```python
from datetime import timedelta
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.feature_extractor import (
    _slope, _elevated_minutes, _pulse_detected, _current, _peak_ratio,
)


def _make_tvoc_readings(values: list[float], seconds_between: int = 1) -> list[NormalisedReading]:
    """Build synthetic NormalisedReadings with tvoc_ppb set, oldest first."""
    now = datetime.now(timezone.utc)
    total = len(values)
    return [
        NormalisedReading(
            timestamp=now - timedelta(seconds=(total - 1 - i) * seconds_between),
            source="test",
            tvoc_ppb=v,
        )
        for i, v in enumerate(values)
    ]


# ── _current ─────────────────────────────────────────────────────────────────

def test_current_returns_latest_non_none():
    readings = _make_tvoc_readings([100.0, 150.0, 200.0])
    assert _current(readings, "tvoc_ppb") == 200.0


def test_current_skips_trailing_none():
    now = datetime.now(timezone.utc)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=2), source="t", tvoc_ppb=150.0),
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t", tvoc_ppb=None),
        NormalisedReading(timestamp=now, source="t", tvoc_ppb=None),
    ]
    assert _current(readings, "tvoc_ppb") == 150.0


def test_current_returns_none_when_all_none():
    readings = _make_tvoc_readings([None, None])  # type: ignore[list-item]
    # build manually since _make_tvoc_readings sets tvoc_ppb
    now = datetime.now(timezone.utc)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t"),
        NormalisedReading(timestamp=now, source="t"),
    ]
    assert _current(readings, "tvoc_ppb") is None


# ── _slope ───────────────────────────────────────────────────────────────────

def test_slope_rising():
    # 60 readings, 1 ppb/sec rise → 60 ppb/min
    readings = _make_tvoc_readings([float(i) for i in range(60)])
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s - 60.0) < 2.0  # within 2 ppb/min tolerance


def test_slope_flat():
    readings = _make_tvoc_readings([100.0] * 60)
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s) < 0.1


def test_slope_returns_none_too_few_points():
    readings = _make_tvoc_readings([100.0])
    assert _slope(readings, "tvoc_ppb", window_seconds=60) is None


def test_slope_only_uses_window():
    # first 60 readings rise, last 60 flat — 1m slope should be near 0
    rising = [float(i) for i in range(60)]
    flat = [60.0] * 60
    readings = _make_tvoc_readings(rising + flat)
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s) < 2.0


# ── _elevated_minutes ─────────────────────────────────────────────────────────

def test_elevated_minutes_all_above():
    # 120 readings all at 200 ppb, baseline 100 → 2.0 minutes
    readings = _make_tvoc_readings([200.0] * 120)
    assert _elevated_minutes(readings, "tvoc_ppb", baseline=100.0) == pytest.approx(2.0, abs=0.1)


def test_elevated_minutes_breaks_on_dip():
    # 30 readings at 200, then 1 at 50, then 60 more at 200 → counts only latest 30
    readings = _make_tvoc_readings([200.0] * 60 + [50.0] + [200.0] * 30)
    result = _elevated_minutes(readings, "tvoc_ppb", baseline=100.0)
    assert result == pytest.approx(30 / 60, abs=0.1)


def test_elevated_minutes_zero_when_all_below():
    readings = _make_tvoc_readings([50.0] * 60)
    assert _elevated_minutes(readings, "tvoc_ppb", baseline=100.0) == 0.0


# ── _pulse_detected ───────────────────────────────────────────────────────────

def test_pulse_detected_true():
    # spike to 300 (3× baseline 100), then decay back to 110
    readings = _make_tvoc_readings([100.0] * 20 + [300.0] + [110.0] * 10)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is True


def test_pulse_detected_false_no_spike():
    readings = _make_tvoc_readings([100.0] * 30)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is False


def test_pulse_detected_false_still_at_peak():
    # spike but hasn't decayed yet
    readings = _make_tvoc_readings([100.0] * 20 + [300.0] * 10)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is False


def test_pulse_detected_none_when_no_baseline():
    readings = _make_tvoc_readings([100.0] * 30)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=None) is None


# ── _peak_ratio ───────────────────────────────────────────────────────────────

def test_peak_ratio_calculation():
    assert _peak_ratio(300.0, 100.0) == pytest.approx(3.0)


def test_peak_ratio_none_when_baseline_zero():
    assert _peak_ratio(300.0, 0.0) is None


def test_peak_ratio_none_when_either_none():
    assert _peak_ratio(None, 100.0) is None
    assert _peak_ratio(300.0, None) is None
```

- [ ] Add `import pytest` to the top of `tests/test_feature_extractor.py` (it's needed for `pytest.approx`)

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: FAIL with `ImportError` (feature_extractor not yet created)

### Step 2.2 — Create `mlss_monitor/feature_extractor.py` with helpers

- [ ] Create `mlss_monitor/feature_extractor.py`:

```python
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import linear_regression, StatisticsError
from typing import TYPE_CHECKING

from mlss_monitor.data_sources.base import SENSOR_FIELDS, NormalisedReading
from mlss_monitor.feature_vector import FeatureVector

if TYPE_CHECKING:
    pass

# Maps NormalisedReading field name → FeatureVector field prefix
_SENSOR_MAP: tuple[tuple[str, str], ...] = (
    ("tvoc_ppb",      "tvoc"),
    ("eco2_ppm",      "eco2"),
    ("temperature_c", "temperature"),
    ("humidity_pct",  "humidity"),
    ("pm25_ug_m3",    "pm25"),
    ("co_ppb",        "co"),
    ("no2_ppb",       "no2"),
    ("nh3_ppb",       "nh3"),
)


# ── Private helpers (pure functions, tested directly) ────────────────────────

def _current(readings: list[NormalisedReading], field: str) -> float | None:
    """Return the most recent non-None value for field."""
    for r in reversed(readings):
        v = getattr(r, field)
        if v is not None:
            return float(v)
    return None


def _slope(
    readings: list[NormalisedReading], field: str, window_seconds: int
) -> float | None:
    """Linear slope in units/minute over the last window_seconds of data.

    Uses statistics.linear_regression (Python 3.10+).
    Returns None if fewer than 2 data points in the window.
    """
    if not readings:
        return None
    now_ts = readings[-1].timestamp
    cutoff = now_ts - timedelta(seconds=window_seconds)
    pairs = [
        ((r.timestamp - cutoff).total_seconds(), getattr(r, field))
        for r in readings
        if r.timestamp >= cutoff and getattr(r, field) is not None
    ]
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2:
        return None
    try:
        result = linear_regression(xs, ys)
    except StatisticsError:
        return None
    return round(result.slope * 60, 4)  # per second → per minute


def _elevated_minutes(
    readings: list[NormalisedReading], field: str, baseline: float
) -> float:
    """Count consecutive seconds (newest to oldest) where field > baseline.

    Stops at the first reading where value <= baseline.
    Returns minutes (seconds / 60).
    """
    count = 0
    for r in reversed(readings):
        v = getattr(r, field)
        if v is None or v <= baseline:
            break
        count += 1
    return count / 60.0


def _pulse_detected(
    readings: list[NormalisedReading], field: str, baseline: float | None
) -> bool | None:
    """True if a spike-and-decay pattern is visible in readings.

    Pattern: max value > 1.5 × baseline AND current value < 0.8 × max.
    Returns None if baseline is None or no non-None values.
    """
    if baseline is None:
        return None
    values = [getattr(r, field) for r in readings if getattr(r, field) is not None]
    if len(values) < 2:
        return None
    peak = max(values)
    current = values[-1]
    return peak > 1.5 * baseline and current < 0.8 * peak


def _peak_ratio(current: float | None, baseline: float | None) -> float | None:
    """current / baseline. None if either is None or baseline is zero."""
    if current is None or baseline is None or baseline == 0:
        return None
    return round(current / baseline, 4)


def _vpd_kpa(temp_c: float | None, humidity_pct: float | None) -> float | None:
    """Vapour pressure deficit in kPa."""
    if temp_c is None or humidity_pct is None or humidity_pct <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return round(svp * (1 - humidity_pct / 100), 4)


def _sensor_features(
    readings: list[NormalisedReading],
    field: str,
    prefix: str,
    baseline: float | None,
) -> dict:
    """Compute all 10 per-sensor features for a single sensor channel.

    Returns a dict keyed by FeatureVector field names.
    """
    current = _current(readings, field)
    slope_1m = _slope(readings, field, window_seconds=60)
    slope_5m = _slope(readings, field, window_seconds=300)
    slope_30m = _slope(readings, field, window_seconds=1800)
    elev_min = _elevated_minutes(readings, field, baseline) if baseline is not None else None
    peak_ratio = _peak_ratio(current, baseline)
    is_declining = (slope_1m < 0) if slope_1m is not None else None
    decay_rate = slope_1m if (slope_1m is not None and slope_1m < 0) else None
    pulse = _pulse_detected(readings, field, baseline)

    return {
        f"{prefix}_current":          current,
        f"{prefix}_baseline":         baseline,
        f"{prefix}_slope_1m":         slope_1m,
        f"{prefix}_slope_5m":         slope_5m,
        f"{prefix}_slope_30m":        slope_30m,
        f"{prefix}_elevated_minutes": elev_min,
        f"{prefix}_peak_ratio":       peak_ratio,
        f"{prefix}_is_declining":     is_declining,
        f"{prefix}_decay_rate":       decay_rate,
        f"{prefix}_pulse_detected":   pulse,
    }


# ── FeatureExtractor ─────────────────────────────────────────────────────────

class FeatureExtractor:
    """Converts a hot-tier snapshot + cold-tier baselines into a FeatureVector."""

    def extract(
        self,
        hot_readings: list[NormalisedReading],
        baselines: dict[str, float | None],
    ) -> FeatureVector:
        """
        Args:
            hot_readings: NormalisedReading list from hot tier, oldest first.
            baselines: dict keyed by NormalisedReading field names, e.g.
                       {"tvoc_ppb": 180.0, "eco2_ppm": 600.0, ...}
                       Values may be None (no baseline available yet).
        Returns:
            FeatureVector with all computable features populated; rest None.
        """
        fields: dict = {}

        # Per-sensor features
        for nr_field, fv_prefix in _SENSOR_MAP:
            baseline = baselines.get(nr_field)
            fields.update(_sensor_features(hot_readings, nr_field, fv_prefix, baseline))

        # Cross-sensor and derived (Task 3)
        fields["nh3_lag_behind_tvoc_seconds"] = None
        fields["pm25_correlated_with_tvoc"] = None
        fields["co_correlated_with_tvoc"] = None
        fields["vpd_kpa"] = None

        return FeatureVector(timestamp=datetime.now(timezone.utc), **fields)
```

### Step 2.3 — Run helper tests

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: all helper tests PASS

### Step 2.4 — Write end-to-end per-sensor extraction tests

- [ ] Add to `tests/test_feature_extractor.py`:

```python
from mlss_monitor.feature_extractor import FeatureExtractor


def test_extract_per_sensor_tvoc():
    """End-to-end: rising TVOC produces correct current, slope, and is_declining."""
    readings = _make_tvoc_readings([float(i * 2) for i in range(60)])  # 0 → 118 ppb
    baselines = {"tvoc_ppb": 50.0}
    fv = FeatureExtractor().extract(readings, baselines)

    assert fv.tvoc_current == pytest.approx(118.0)
    assert fv.tvoc_baseline == 50.0
    assert fv.tvoc_slope_1m is not None and fv.tvoc_slope_1m > 0
    assert fv.tvoc_is_declining is False
    assert fv.tvoc_decay_rate is None  # not declining


def test_extract_per_sensor_declining_tvoc():
    readings = _make_tvoc_readings([float(200 - i) for i in range(60)])  # 200 → 141 ppb
    baselines = {"tvoc_ppb": 100.0}
    fv = FeatureExtractor().extract(readings, baselines)

    assert fv.tvoc_is_declining is True
    assert fv.tvoc_decay_rate is not None and fv.tvoc_decay_rate < 0


def test_extract_all_none_when_no_readings():
    fv = FeatureExtractor().extract([], {})
    assert fv.tvoc_current is None
    assert fv.eco2_current is None
    assert fv.temperature_current is None


def test_extract_no_baseline_gives_none_for_ratio():
    readings = _make_tvoc_readings([200.0] * 30)
    fv = FeatureExtractor().extract(readings, {})  # no baselines
    assert fv.tvoc_baseline is None
    assert fv.tvoc_peak_ratio is None
    assert fv.tvoc_elevated_minutes is None
```

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: all tests PASS

### Step 2.5 — Commit

```bash
git add mlss_monitor/feature_extractor.py tests/test_feature_extractor.py
git commit -m "feat: add FeatureExtractor with per-sensor features and helpers"
```

---

## Task 3: `FeatureExtractor` — cross-sensor features + VPD

**Files:**
- Modify: `mlss_monitor/feature_extractor.py`
- Modify: `tests/test_feature_extractor.py`

### Step 3.1 — Write failing tests for cross-sensor features

- [ ] Add to `tests/test_feature_extractor.py`:

```python
def _make_readings_with_fields(
    field_values: dict[str, list[float | None]],
    seconds_between: int = 1,
) -> list[NormalisedReading]:
    """Build synthetic readings with multiple fields set.

    field_values: {field_name: [v0, v1, ..., vN]} — all lists must be same length.
    """
    now = datetime.now(timezone.utc)
    keys = list(field_values.keys())
    n = len(field_values[keys[0]])
    readings = []
    for i in range(n):
        ts = now - timedelta(seconds=(n - 1 - i) * seconds_between)
        kwargs = {k: field_values[k][i] for k in keys}
        readings.append(NormalisedReading(timestamp=ts, source="test", **kwargs))
    return readings


# ── NH3 lag ──────────────────────────────────────────────────────────────────

def test_nh3_lag_detected():
    """NH3 peaks 30 seconds after TVOC peak → lag = 30.0."""
    n = 120
    tvoc_vals = [100.0] * 50 + [500.0] + [100.0] * 69  # peak at index 50
    nh3_vals  = [10.0]  * 80 + [80.0]  + [10.0]  * 39  # peak at index 80

    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is not None
    assert 25.0 <= fv.nh3_lag_behind_tvoc_seconds <= 35.0


def test_nh3_lag_none_when_nh3_before_tvoc():
    """NH3 peaked before TVOC → no lag (None)."""
    tvoc_vals = [100.0] * 80 + [500.0] + [100.0] * 39  # peak at index 80
    nh3_vals  = [10.0]  * 50 + [80.0]  + [10.0]  * 69  # peak at index 50
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is None


def test_nh3_lag_none_when_lag_too_large():
    """NH3 peaked 200 seconds after TVOC → beyond 120s limit → None."""
    tvoc_vals = [500.0] + [100.0] * 119  # peak at index 0
    nh3_vals  = [10.0]  * 100 + [80.0] + [10.0] * 19  # peak at index 100
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is None


# ── PM2.5 correlation ────────────────────────────────────────────────────────

def test_pm25_correlated_with_tvoc_true():
    """Both TVOC and PM2.5 rising → correlated = True."""
    n = 300  # 5 minutes
    tvoc_vals = [float(100 + i) for i in range(n)]
    pm25_vals = [float(10 + i * 0.1) for i in range(n)]
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "pm25_ug_m3": pm25_vals})
    baselines = {"tvoc_ppb": 100.0, "pm25_ug_m3": 10.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.pm25_correlated_with_tvoc is True


def test_pm25_correlated_with_tvoc_false():
    """TVOC rising, PM2.5 flat → not correlated."""
    n = 300
    tvoc_vals = [float(100 + i) for i in range(n)]
    pm25_vals = [10.0] * n
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "pm25_ug_m3": pm25_vals})
    baselines = {"tvoc_ppb": 100.0, "pm25_ug_m3": 10.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.pm25_correlated_with_tvoc is False


def test_pm25_correlated_none_when_no_data():
    """No PM2.5 readings → None."""
    readings = _make_tvoc_readings([float(i) for i in range(60)])
    fv = FeatureExtractor().extract(readings, {})
    assert fv.pm25_correlated_with_tvoc is None


# ── VPD ──────────────────────────────────────────────────────────────────────

def test_vpd_computed_from_temp_and_humidity():
    now = datetime.now(timezone.utc)
    readings = [NormalisedReading(
        timestamp=now, source="test", temperature_c=21.0, humidity_pct=60.0
    )]
    fv = FeatureExtractor().extract(readings, {})
    # SVP at 21°C ≈ 2.487 kPa; VPD = 2.487 × 0.40 ≈ 0.995 kPa
    assert fv.vpd_kpa is not None
    assert 0.9 < fv.vpd_kpa < 1.1


def test_vpd_none_when_no_temperature():
    now = datetime.now(timezone.utc)
    readings = [NormalisedReading(timestamp=now, source="test", humidity_pct=60.0)]
    fv = FeatureExtractor().extract(readings, {})
    assert fv.vpd_kpa is None
```

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: new tests FAIL with `AssertionError` (cross-sensor features return None as placeholder)

### Step 3.2 — Implement cross-sensor helpers

- [ ] Add these private functions to `mlss_monitor/feature_extractor.py` (before `FeatureExtractor`):

```python
def _nh3_lag_behind_tvoc(
    readings: list[NormalisedReading], max_lag_seconds: float = 120.0
) -> float | None:
    """Return NH3 lag behind TVOC peak in seconds, or None.

    Looks for the peak of each sensor in the readings window.
    Returns the lag only if TVOC peaked before NH3 and lag <= max_lag_seconds.
    """
    tvoc_peak_ts: datetime | None = None
    tvoc_peak_val: float = 0.0
    nh3_peak_ts: datetime | None = None
    nh3_peak_val: float = 0.0

    for r in readings:
        if r.tvoc_ppb is not None and r.tvoc_ppb > tvoc_peak_val:
            tvoc_peak_val = r.tvoc_ppb
            tvoc_peak_ts = r.timestamp
        if r.nh3_ppb is not None and r.nh3_ppb > nh3_peak_val:
            nh3_peak_val = r.nh3_ppb
            nh3_peak_ts = r.timestamp

    if tvoc_peak_ts is None or nh3_peak_ts is None:
        return None

    lag = (nh3_peak_ts - tvoc_peak_ts).total_seconds()
    if 0 <= lag <= max_lag_seconds:
        return lag
    return None


def _sensors_correlated(
    readings: list[NormalisedReading],
    field_a: str,
    field_b: str,
    window_seconds: int = 300,
) -> bool | None:
    """True if both sensors have positive slope over window_seconds AND both above baseline.

    Returns None if either sensor has no data in the window.
    """
    slope_a = _slope(readings, field_a, window_seconds)
    slope_b = _slope(readings, field_b, window_seconds)
    if slope_a is None or slope_b is None:
        return None
    return slope_a > 0 and slope_b > 0
```

- [ ] Update `FeatureExtractor.extract()` — replace the cross-sensor placeholder block:

```python
        # Cross-sensor and derived
        fields["nh3_lag_behind_tvoc_seconds"] = _nh3_lag_behind_tvoc(hot_readings)
        fields["pm25_correlated_with_tvoc"] = _sensors_correlated(
            hot_readings, "tvoc_ppb", "pm25_ug_m3"
        )
        fields["co_correlated_with_tvoc"] = _sensors_correlated(
            hot_readings, "tvoc_ppb", "co_ppb"
        )
        fields["vpd_kpa"] = _vpd_kpa(
            _current(hot_readings, "temperature_c"),
            _current(hot_readings, "humidity_pct"),
        )
```

### Step 3.3 — Run all tests

- [ ] Run: `python -m pytest tests/test_feature_extractor.py -v`
  Expected: all tests PASS

### Step 3.4 — Commit

```bash
git add mlss_monitor/feature_extractor.py tests/test_feature_extractor.py
git commit -m "feat: add cross-sensor features and VPD to FeatureExtractor"
```

---

## Task 4: Cold tier baseline query

**Files:**
- Modify: `database/db_logger.py`
- Create or modify: `tests/test_db_logger.py`

The cold tier uses SQLite column names that differ from `NormalisedReading` field names. This function returns a dict keyed by NormalisedReading field names so `FeatureExtractor.extract()` receives the right keys.

**Column → NormalisedReading field mapping:**
```
tvoc       → tvoc_ppb
eco2       → eco2_ppm
temperature → temperature_c
humidity   → humidity_pct
pm2_5      → pm25_ug_m3
gas_co     → co_ppb
gas_no2    → no2_ppb
gas_nh3    → nh3_ppb
```

### Step 4.1 — Write failing test

- [ ] Check if `tests/test_db_logger.py` exists. If not, create it. Add:

```python
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch


def test_get_24h_baselines_returns_medians(tmp_path):
    """Median of known values is returned per sensor field."""
    from database.db_logger import get_24h_baselines

    db_path = str(tmp_path / "test.db")

    # Create minimal sensor_data table
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            temperature REAL, humidity REAL,
            eco2 INTEGER, tvoc INTEGER,
            pm2_5 REAL, gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )
    """)
    # Insert 3 rows within last 24h
    now = datetime.utcnow()
    for minutes_ago, (t, h, e, v, pm, co, no2, nh3) in enumerate([
        (21.0, 55.0, 600, 180, 8.0,  1.0, 0.05, 6.0),
        (22.0, 57.0, 620, 200, 10.0, 1.5, 0.07, 7.0),
        (23.0, 59.0, 640, 220, 12.0, 2.0, 0.09, 8.0),
    ]):
        conn.execute(
            "INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc, "
            "pm2_5, gas_co, gas_no2, gas_nh3) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ((now - timedelta(minutes=minutes_ago * 10)).isoformat(),
             t, h, e, v, pm, co, no2, nh3),
        )
    conn.commit()
    conn.close()

    with patch("database.db_logger.DB_FILE", db_path):
        result = get_24h_baselines()

    assert result["tvoc_ppb"] == pytest.approx(200.0)       # median of [180, 200, 220]
    assert result["eco2_ppm"] == pytest.approx(620.0)       # median of [600, 620, 640]
    assert result["temperature_c"] == pytest.approx(22.0)
    assert result["humidity_pct"] == pytest.approx(57.0)
    assert result["pm25_ug_m3"] == pytest.approx(10.0)
    assert result["co_ppb"] == pytest.approx(1.5)
    assert result["no2_ppb"] == pytest.approx(0.07)
    assert result["nh3_ppb"] == pytest.approx(6.99, abs=0.1)  # median of 6,7,8 = 7.0


def test_get_24h_baselines_returns_none_when_no_data(tmp_path):
    """None returned for channels with no readings."""
    from database.db_logger import get_24h_baselines

    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY, timestamp TEXT,
            temperature REAL, humidity REAL, eco2 INTEGER, tvoc INTEGER,
            pm2_5 REAL, gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )
    """)
    conn.commit()
    conn.close()

    with patch("database.db_logger.DB_FILE", db_path):
        result = get_24h_baselines()

    for v in result.values():
        assert v is None


def test_get_24h_baselines_ignores_old_rows(tmp_path):
    """Rows older than 24h are excluded from the baseline."""
    from database.db_logger import get_24h_baselines

    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE sensor_data (
            id INTEGER PRIMARY KEY, timestamp TEXT,
            temperature REAL, humidity REAL, eco2 INTEGER, tvoc INTEGER,
            pm2_5 REAL, gas_co REAL, gas_no2 REAL, gas_nh3 REAL
        )
    """)
    old_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
    conn.execute(
        "INSERT INTO sensor_data (timestamp, tvoc, eco2, temperature, humidity, "
        "pm2_5, gas_co, gas_no2, gas_nh3) VALUES (?, 999, 999, 99, 99, 99, 99, 99, 99)",
        (old_ts,),
    )
    conn.commit()
    conn.close()

    with patch("database.db_logger.DB_FILE", db_path):
        result = get_24h_baselines()

    assert result["tvoc_ppb"] is None  # old row excluded
```

Add `import pytest` to the top of the test file.

- [ ] Run: `python -m pytest tests/test_db_logger.py -v`
  Expected: FAIL with `ImportError` or `AttributeError` (function not yet defined)

### Step 4.2 — Implement `get_24h_baselines`

- [ ] Read `database/db_logger.py` to find a good place to insert the function (after existing query functions)

- [ ] Add to `database/db_logger.py`:

```python
def get_24h_baselines() -> dict[str, float | None]:
    """Return the median sensor value per channel over the last 24 hours.

    Returns a dict keyed by NormalisedReading field names:
        tvoc_ppb, eco2_ppm, temperature_c, humidity_pct,
        pm25_ug_m3, co_ppb, no2_ppb, nh3_ppb

    Values are None when no data exists for that channel in the window.
    """
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    column_map = [
        ("tvoc",        "tvoc_ppb"),
        ("eco2",        "eco2_ppm"),
        ("temperature", "temperature_c"),
        ("humidity",    "humidity_pct"),
        ("pm2_5",       "pm25_ug_m3"),
        ("gas_co",      "co_ppb"),
        ("gas_no2",     "no2_ppb"),
        ("gas_nh3",     "nh3_ppb"),
    ]
    result: dict[str, float | None] = {nr_field: None for _, nr_field in column_map}

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    for db_col, nr_field in column_map:
        cur.execute(
            f"SELECT {db_col} FROM sensor_data "
            f"WHERE timestamp >= ? AND {db_col} IS NOT NULL "
            f"ORDER BY {db_col}",
            (since,),
        )
        rows = cur.fetchall()
        if rows:
            vals = [r[0] for r in rows]
            n = len(vals)
            mid = n // 2
            result[nr_field] = float(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2)
    conn.close()
    return result
```

Note: `datetime` and `timedelta` are already imported at the top of `db_logger.py`. If not, add `from datetime import datetime, timedelta`.

### Step 4.3 — Run tests

- [ ] Run: `python -m pytest tests/test_db_logger.py -v`
  Expected: all 3 tests PASS

### Step 4.4 — Commit

```bash
git add database/db_logger.py tests/test_db_logger.py
git commit -m "feat: add get_24h_baselines() cold tier baseline query"
```

---

## Task 5: Wire into inference cycle + state

**Files:**
- Modify: `mlss_monitor/state.py`
- Modify: `mlss_monitor/app.py`

This task stores a fresh `FeatureVector` on `state.feature_vector` after each cold-tier write cycle. Phase 3 will consume it. No inference output changes.

### Step 5.1 — Add `feature_vector` to state

- [ ] Read `mlss_monitor/state.py`

- [ ] Add `feature_vector = None` alongside the other `None`-initialised attributes. Place it near `hot_tier`:

```python
feature_vector = None   # Set to FeatureVector after each cold-tier cycle in app.py
```

### Step 5.2 — Add import to `app.py`

- [ ] Read `mlss_monitor/app.py` lines 36–45 (the new imports block added in Phase 1)

- [ ] Add after the existing Phase 1 imports:

```python
from database.db_logger import get_24h_baselines
from mlss_monitor.feature_extractor import FeatureExtractor
```

### Step 5.3 — Add module-level extractor instance

- [ ] Add immediately after the `_data_sources` list (lines ~190 in app.py):

```python
_feature_extractor = FeatureExtractor()
```

### Step 5.4 — Compute FeatureVector inside `_background_log`

- [ ] Read `mlss_monitor/app.py` lines 388–424 (`_background_log` function)

- [ ] Inside `_background_log`, the loop already calls `log_data()` every cycle. After `log_data()`, add a FeatureVector compute step. Find the line:

```python
        try:
            log_data()
        except Exception as e:
            log.error("Error in background log loop: %s", e)
```

And add immediately after the `except` block (still inside the `while True` loop, at the same indentation level):

```python
        try:
            baselines = get_24h_baselines()
            hot_snap = state.hot_tier.snapshot() if state.hot_tier else []
            state.feature_vector = _feature_extractor.extract(hot_snap, baselines)
        except Exception as exc:
            log.error("FeatureExtractor error: %s", exc)
```

### Step 5.5 — Run full test suite

- [ ] Run: `python -m pytest tests/ -v`
  Expected: all existing tests PASS (31 phase-1 + new phase-2 tests), no regressions

- [ ] Confirm `state.feature_vector` is defined:

```bash
python -c "from mlss_monitor import state; print(hasattr(state, 'feature_vector'))"
```
Expected: `True`

### Step 5.6 — Commit

```bash
git add mlss_monitor/state.py mlss_monitor/app.py
git commit -m "feat: compute and store FeatureVector on state after each cold-tier cycle"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| FeatureVector dataclass with per-sensor fields | Task 1 |
| Per-sensor: current, baseline, slope 1m/5m/30m | Task 2 |
| Per-sensor: elevated_minutes, peak_ratio, is_declining, decay_rate, pulse_detected | Task 2 |
| Cross-sensor: nh3_lag_behind_tvoc_seconds | Task 3 |
| Cross-sensor: pm25_correlated_with_tvoc, co_correlated_with_tvoc | Task 3 |
| Derived: vpd_kpa | Task 3 |
| Cold tier baseline (24h median) | Task 4 |
| Wire into detection cycle | Task 5 |
| None when insufficient data | All tasks — every helper returns None gracefully |

**No placeholders:** All steps contain working code.

**Type consistency:**
- `SENSOR_FIELDS` exported from `base.py`, used in `feature_extractor.py` via import
- `baselines` dict keyed by NormalisedReading field names throughout
- `_sensor_features` returns dict keys matching `FeatureVector` field names exactly (verified by `_SENSOR_MAP` prefix alignment)
- `get_24h_baselines()` returns keys matching `_SENSOR_MAP`'s `nr_field` values

**Phase 1 cleanup items (not blocking Phase 2, address before Phase 3):**
- `SGP30Source` and `AHT20Source` propagate hardware exceptions rather than returning `None` fields — inconsistent with `ParticulateSource`/`MICS6814Source`. If I2C bus fails, the merged reading lacks temp/humidity/eco2/tvoc. FeatureExtractor handles this gracefully (returns None for those features), but it's worth fixing for consistency.

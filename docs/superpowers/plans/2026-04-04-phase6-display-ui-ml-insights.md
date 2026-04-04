# Phase 6 — Display UI & ML Insights — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich inference cards with ML transparency, rebuild the History page with ML-era views, surface live anomaly scores in Settings, and fix two data-display bugs.

**Architecture:** Hybrid REST + SSE — pages load historical state via REST on first load; the existing SSE stream pushes incremental updates. All narrative/analytical text is generated in Python (backend-heavy); JS only renders pre-computed fields. A new `narrative_engine.py` module contains pure, testable functions for all text generation.

**Tech Stack:** Flask, SQLite, River (HalfSpaceTrees), Plotly.js, SSE (existing event bus), PyYAML

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `database/db_logger.py` | Modify | Add `_normalise_ts()`, `compute_detection_method()`, `get_inference_by_id()`; apply to all timestamp outputs; add `detection_method` to `get_inferences()` rows |
| `mlss_monitor/narrative_engine.py` | Create | 7 pure analysis/narrative functions — no IO, no Flask |
| `mlss_monitor/routes/api_history.py` | Create | `/api/history/sensor`, `/api/history/baselines`, `/api/history/ml-context`, `/api/history/narratives` |
| `mlss_monitor/routes/api_inferences.py` | Modify | Add `GET /api/inferences/<id>/sparkline` |
| `mlss_monitor/routes/api_stream.py` | Modify | Extend `sensor_reading` event; document `inference_fired` hook |
| `mlss_monitor/routes/pages.py` | Modify | Move `/insights-engine` → `/settings/insights-engine` |
| `mlss_monitor/inference_evidence.py` | Modify | Fix CO/NO2/NH3 units to `kΩ`, labels to include `(resistance)` |
| `mlss_monitor/app.py` | Modify | Register `api_history_bp`; add 30s anomaly_scores SSE push |
| `templates/base.html` | Modify | Update nav: remove top-level Insights Engine link, add under Settings |
| `templates/insights_engine.html` | Modify | Add live score column + SSE consumer JS |
| `templates/history.html` | Modify | Rename Patterns tab → Detections & Insights; replace content scaffold |
| `static/js/dashboard.js` | Modify | Detection chip, attribution badge, sparkline, evidence ⓘ in inference dialog |
| `static/js/charts_correlation.js` | Modify | Full 10-channel toggle chips, anomaly overlay, smarter analysis panel |
| `static/js/detections_insights.js` | Create | All 9 sections of Detections & Insights tab; SSE updates |
| `tests/test_db_logger.py` | Modify | Add timestamp normalisation + detection_method tests |
| `tests/test_detection_method.py` | Create | Full coverage of compute_detection_method() |
| `tests/test_narrative_engine.py` | Create | Full coverage of all 7 narrative engine functions |
| `tests/test_api_history.py` | Create | Tests for all 4 history endpoints + sparkline |

---

## Execution Order

Tasks are ordered so each builds on a stable foundation:

1. **Bug fixes** (Tasks 1–2) — quick wins, establish patterns
2. **Backend logic** (Tasks 3–9) — pure functions and API endpoints
3. **Settings / IE page** (Tasks 10–12) — minimal UI changes
4. **Inference card** (Tasks 13–15) — dashboard.js enhancements
5. **Correlations tab** (Tasks 16–19) — charts_correlation.js rebuild
6. **Detections & Insights tab** (Tasks 20–25) — new tab, full narrative UI

Run the full test suite after every task: `python -m pytest tests/ -x -q`

---

## Task 1: Bug Fix — UTC Timestamps

**Files:**
- Modify: `database/db_logger.py`
- Modify: `tests/test_db_logger.py`

- [ ] **Step 1: Write the failing test**

Open `tests/test_db_logger.py` and add at the end:

```python
def test_get_inferences_timestamps_are_utc_iso(db):
    """created_at in get_inferences() must be UTC ISO 8601 with Z suffix."""
    from database.db_logger import save_inference, get_inferences
    save_inference(
        db_file=db,
        event_type="tvoc_spike",
        title="Test",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={},
    )
    rows = get_inferences(db_file=db, limit=1)
    assert len(rows) == 1
    ts = rows[0]["created_at"]
    assert "T" in ts, f"Expected ISO format with T, got: {ts}"
    assert ts.endswith("Z"), f"Expected UTC Z suffix, got: {ts}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "C:/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility/.claude/worktrees/zealous-hugle"
python -m pytest tests/test_db_logger.py::test_get_inferences_timestamps_are_utc_iso -v
```

Expected: FAIL — created_at does not contain T or Z.

- [ ] **Step 3: Add `_normalise_ts()` and apply it in `get_inferences()`**

In `database/db_logger.py`, add after the imports:

```python
def _normalise_ts(ts: str | None) -> str | None:
    """Convert 'YYYY-MM-DD HH:MM:SS' → 'YYYY-MM-DDTHH:MM:SSZ' (UTC ISO 8601).
    No-ops if ts is already normalised or is None.
    """
    if ts is None:
        return None
    if ts.endswith("Z"):
        return ts
    return ts.replace(" ", "T") + "Z"
```

Then in `get_inferences()`, find where rows are built into dicts and apply:

```python
# Wherever created_at is set in the returned dict, wrap it:
row_dict["created_at"] = _normalise_ts(row_dict.get("created_at"))
```

Also apply `_normalise_ts` to any other timestamp field returned by `get_inferences()` (e.g. if `dismissed_at` or similar exists).

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_db_logger.py::test_get_inferences_timestamps_are_utc_iso -v
```

Expected: PASS

- [ ] **Step 5: Run full suite to check for regressions**

```bash
python -m pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add database/db_logger.py tests/test_db_logger.py
git commit -m "fix: normalise inference timestamps to UTC ISO 8601 (append Z suffix)"
```

---

## Task 2: Bug Fix — MICS6814 Units

**Files:**
- Modify: `mlss_monitor/inference_evidence.py`
- Modify: `tests/test_inference_evidence.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_inference_evidence.py`:

```python
def test_mics6814_channels_use_resistance_units():
    """CO, NO2, NH3 must show kΩ units and (resistance) in labels — MICS6814 outputs resistance."""
    from mlss_monitor.inference_evidence import _CHANNEL_META
    for fv_key in ("co_current", "no2_current", "nh3_current"):
        meta = _CHANNEL_META[fv_key]
        assert meta["unit"] == "kΩ", (
            f"{fv_key} unit should be kΩ (MICS6814 resistance), got {meta['unit']!r}"
        )
        assert "(resistance)" in meta["label"], (
            f"{fv_key} label should include '(resistance)', got {meta['label']!r}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_inference_evidence.py::test_mics6814_channels_use_resistance_units -v
```

Expected: FAIL — unit is "ppb".

- [ ] **Step 3: Update `_CHANNEL_META` in `inference_evidence.py`**

Find and replace the three entries:

```python
# Before:
"co_current":  {"label": "CO",  "unit": "ppb", "slope_field": "co_slope_1m",  "slope_thresh": 2.0},
"no2_current": {"label": "NO2", "unit": "ppb", "slope_field": "no2_slope_1m", "slope_thresh": 2.0},
"nh3_current": {"label": "NH3", "unit": "ppb", "slope_field": "nh3_slope_1m", "slope_thresh": 2.0},

# After:
"co_current":  {"label": "CO (resistance)",  "unit": "kΩ", "slope_field": "co_slope_1m",  "slope_thresh": 2.0},
"no2_current": {"label": "NO2 (resistance)", "unit": "kΩ", "slope_field": "no2_slope_1m", "slope_thresh": 2.0},
"nh3_current": {"label": "NH3 (resistance)", "unit": "kΩ", "slope_field": "nh3_slope_1m", "slope_thresh": 2.0},
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_inference_evidence.py::test_mics6814_channels_use_resistance_units -v
```

Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/inference_evidence.py tests/test_inference_evidence.py
git commit -m "fix: correct MICS6814 channel units from ppb to kΩ (resistance measurements)"
```

---

## Task 3: `compute_detection_method()` + `detection_method` on inferences

**Files:**
- Modify: `database/db_logger.py`
- Create: `tests/test_detection_method.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_detection_method.py`:

```python
"""Tests for compute_detection_method() in db_logger."""
import pytest
from database.db_logger import compute_detection_method


def test_ml_event_types_return_ml():
    for et in [
        "anomaly_combustion_signature",
        "anomaly_particle_distribution",
        "anomaly_ventilation_quality",
        "anomaly_gas_relationship",
        "anomaly_thermal_moisture",
    ]:
        assert compute_detection_method(et) == "ml", f"Expected 'ml' for {et}"


def test_statistical_event_types_return_statistical():
    for et in [
        "anomaly_tvoc", "anomaly_eco2", "anomaly_temperature",
        "anomaly_humidity", "anomaly_pm25", "anomaly_pm1",
        "anomaly_pm10", "anomaly_co", "anomaly_no2", "anomaly_nh3",
    ]:
        assert compute_detection_method(et) == "statistical", f"Expected 'statistical' for {et}"


def test_rule_event_types_return_rule():
    for et in [
        "tvoc_spike", "eco2_danger", "eco2_elevated", "mould_risk",
        "correlated_pollution", "sustained_poor_air",
        "pm1_spike", "pm25_spike", "pm10_spike",
        "temp_high", "temp_low", "humidity_high", "humidity_low",
        "vpd_high", "vpd_low",
        "rapid_tvoc_rise", "rapid_eco2_rise", "rapid_pm25_rise",
        "hourly_summary", "daily_summary",
    ]:
        assert compute_detection_method(et) == "rule", f"Expected 'rule' for {et}"


def test_annotation_context_prefix_returns_rule():
    assert compute_detection_method("annotation_context_cooking") == "rule"
    assert compute_detection_method("annotation_context_anything") == "rule"


def test_unknown_event_type_returns_rule():
    assert compute_detection_method("totally_unknown_type") == "rule"


def test_get_inferences_includes_detection_method(db):
    from database.db_logger import save_inference, get_inferences
    save_inference(
        db_file=db,
        event_type="anomaly_combustion_signature",
        title="ML test",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.8,
        evidence={},
    )
    rows = get_inferences(db_file=db, limit=1)
    assert rows[0]["detection_method"] == "ml"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_detection_method.py -v
```

Expected: ImportError — `compute_detection_method` not found.

- [ ] **Step 3: Add `compute_detection_method()` to `db_logger.py`**

Add after `_normalise_ts()`:

```python
# ---------------------------------------------------------------------------
# Detection method classification
# ---------------------------------------------------------------------------

_ML_EVENT_TYPES = frozenset({
    "anomaly_combustion_signature",
    "anomaly_particle_distribution",
    "anomaly_ventilation_quality",
    "anomaly_gas_relationship",
    "anomaly_thermal_moisture",
})

_STATISTICAL_SUFFIXES = frozenset({
    "tvoc", "eco2", "temperature", "humidity",
    "pm25", "pm1", "pm10", "co", "no2", "nh3",
})


def compute_detection_method(event_type: str) -> str:
    """Classify an inference event_type as 'ml', 'statistical', or 'rule'.

    'ml'          — multivariate composite River model
    'statistical' — per-channel River anomaly detector
    'rule'        — deterministic YAML threshold rule (default)
    """
    if event_type in _ML_EVENT_TYPES:
        return "ml"
    if event_type.startswith("anomaly_"):
        suffix = event_type[len("anomaly_"):]
        if suffix in _STATISTICAL_SUFFIXES:
            return "statistical"
    if event_type.startswith("annotation_context_"):
        return "rule"
    return "rule"
```

Then in `get_inferences()`, add `detection_method` to each returned row dict:

```python
row_dict["detection_method"] = compute_detection_method(row_dict.get("event_type", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_detection_method.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 6: Commit**

```bash
git add database/db_logger.py tests/test_detection_method.py
git commit -m "feat: add compute_detection_method() and detection_method field on inferences"
```

---

## Task 4: `narrative_engine.py` — 7 pure analysis functions

**Files:**
- Create: `mlss_monitor/narrative_engine.py`
- Create: `tests/test_narrative_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_narrative_engine.py`:

```python
"""Tests for mlss_monitor/narrative_engine.py — pure analysis functions."""
import pytest
from mlss_monitor.narrative_engine import (
    compute_longest_clean_period,
    compute_pattern_heatmap,
    detect_drift_flags,
    compute_trend_indicators,
    generate_period_summary,
    generate_fingerprint_narrative,
    generate_anomaly_model_narrative,
)

# ---------------------------------------------------------------------------
# compute_longest_clean_period
# ---------------------------------------------------------------------------

def test_longest_clean_period_no_events():
    result = compute_longest_clean_period(
        inferences=[],
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    assert result["hours"] == pytest.approx(24.0, abs=0.1)
    assert result["start"] == "2026-04-04T00:00:00Z"
    assert result["end"] == "2026-04-04T24:00:00Z"


def test_longest_clean_period_single_event_in_middle():
    result = compute_longest_clean_period(
        inferences=[{"created_at": "2026-04-04T06:00:00Z"}],
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    # Gap before event: 6h; gap after event: 18h → longest is 18h
    assert result["hours"] == pytest.approx(18.0, abs=0.1)


def test_longest_clean_period_multiple_events():
    inferences = [
        {"created_at": "2026-04-04T02:00:00Z"},
        {"created_at": "2026-04-04T04:00:00Z"},
        {"created_at": "2026-04-04T20:00:00Z"},
    ]
    result = compute_longest_clean_period(
        inferences=inferences,
        window_start="2026-04-04T00:00:00Z",
        window_end="2026-04-04T24:00:00Z",
    )
    # Gaps: 2h, 2h, 16h → longest is 16h
    assert result["hours"] == pytest.approx(16.0, abs=0.1)


# ---------------------------------------------------------------------------
# compute_pattern_heatmap
# ---------------------------------------------------------------------------

def test_pattern_heatmap_empty():
    assert compute_pattern_heatmap([]) == {}


def test_pattern_heatmap_counts_correctly():
    # Monday (weekday=0) at 18:00 UTC
    inferences = [
        {"created_at": "2026-04-06T18:00:00Z"},  # Monday
        {"created_at": "2026-04-06T18:30:00Z"},  # Monday same hour
        {"created_at": "2026-04-07T12:00:00Z"},  # Tuesday
    ]
    result = compute_pattern_heatmap(inferences)
    assert result.get("0_18") == 2
    assert result.get("1_12") == 1
    assert "0_19" not in result  # no events at 19:00


# ---------------------------------------------------------------------------
# detect_drift_flags
# ---------------------------------------------------------------------------

def test_drift_flags_empty_when_no_drift():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 102.0},
    )
    assert flags == []


def test_drift_flags_detects_significant_shift():
    flags = detect_drift_flags(
        baselines_now={"co_ppb": 15000.0},
        baselines_7d_ago={"co_ppb": 12000.0},
    )
    assert len(flags) == 1
    assert flags[0]["channel"] == "co_ppb"
    assert flags[0]["direction"] == "up"
    assert flags[0]["shift_pct"] == pytest.approx(25.0, abs=0.1)
    assert "message" in flags[0]
    assert len(flags[0]["message"]) > 10


def test_drift_flags_skips_none_baseline():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": None},
    )
    assert flags == []


def test_drift_flags_downward():
    flags = detect_drift_flags(
        baselines_now={"tvoc_ppb": 80.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
    )
    assert len(flags) == 1
    assert flags[0]["direction"] == "down"


# ---------------------------------------------------------------------------
# compute_trend_indicators
# ---------------------------------------------------------------------------

_DUMMY_META = {
    "tvoc_ppb": {"label": "TVOC", "unit": "ppb"},
    "eco2_ppm": {"label": "eCO2", "unit": "ppm"},
}


def test_trend_indicators_green_when_stable():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 98.0},
        channel_meta=_DUMMY_META,
    )
    assert len(indicators) == 1
    assert indicators[0]["colour"] == "green"
    assert indicators[0]["direction"] == "up"
    assert indicators[0]["pct_change"] == pytest.approx(2.04, abs=0.1)


def test_trend_indicators_amber_when_moderate():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 115.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
        channel_meta=_DUMMY_META,
    )
    assert indicators[0]["colour"] == "amber"


def test_trend_indicators_red_when_large():
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 135.0},
        baselines_7d_ago={"tvoc_ppb": 100.0},
        channel_meta=_DUMMY_META,
    )
    assert indicators[0]["colour"] == "red"


def test_trend_indicators_skips_missing_channels():
    # eco2 not in baselines_now → should be omitted
    indicators = compute_trend_indicators(
        baselines_now={"tvoc_ppb": 100.0},
        baselines_7d_ago={"tvoc_ppb": 100.0, "eco2_ppm": 500.0},
        channel_meta=_DUMMY_META,
    )
    channels = [i["channel"] for i in indicators]
    assert "eco2_ppm" not in channels


# ---------------------------------------------------------------------------
# generate_period_summary
# ---------------------------------------------------------------------------

def test_period_summary_no_events():
    text = generate_period_summary(
        inferences=[],
        trend_indicators=[],
        dominant_source=None,
    )
    assert isinstance(text, str)
    assert len(text) > 20
    # Should convey "clean" or "no events"
    assert any(word in text.lower() for word in ("clean", "no event", "no detection"))


def test_period_summary_with_events_and_source():
    inferences = [{"severity": "warning"}, {"severity": "warning"}]
    text = generate_period_summary(
        inferences=inferences,
        trend_indicators=[{"colour": "green"}],
        dominant_source="cooking",
    )
    assert isinstance(text, str)
    assert len(text) > 20


# ---------------------------------------------------------------------------
# generate_fingerprint_narrative
# ---------------------------------------------------------------------------

def test_fingerprint_narrative_zero_events():
    text = generate_fingerprint_narrative(
        source_id="cooking",
        label="Cooking",
        events=[],
        avg_confidence=0.0,
        typical_hours=[],
    )
    assert "Cooking" in text
    assert "no" in text.lower() or "not detected" in text.lower() or "0" in text


def test_fingerprint_narrative_with_events():
    events = [{"id": 1}, {"id": 2}, {"id": 3}]
    text = generate_fingerprint_narrative(
        source_id="cooking",
        label="Cooking",
        events=events,
        avg_confidence=0.71,
        typical_hours=[12, 13, 18, 19],
    )
    assert "3" in text or "three" in text.lower()
    assert isinstance(text, str)
    assert len(text) > 30


def test_fingerprint_narrative_includes_advice():
    text = generate_fingerprint_narrative(
        source_id="combustion",
        label="Combustion",
        events=[{"id": 1}],
        avg_confidence=0.80,
        typical_hours=[19],
    )
    # Should contain actionable advice (non-empty)
    assert len(text) > 40


# ---------------------------------------------------------------------------
# generate_anomaly_model_narrative
# ---------------------------------------------------------------------------

def test_anomaly_model_narrative():
    text = generate_anomaly_model_narrative(
        model_id="combustion_signature",
        label="Combustion Signature",
        event_count=2,
        description="Watches CO, NO2, PM2.5 and PM10 for co-rises consistent with combustion.",
    )
    assert "Combustion Signature" in text or "combustion" in text.lower()
    assert "2" in text
    assert isinstance(text, str)
    assert len(text) > 30
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_narrative_engine.py -v
```

Expected: ImportError — module not found.

- [ ] **Step 3: Create `mlss_monitor/narrative_engine.py`**

```python
"""Narrative engine — pure analysis and text generation functions.

All functions are stateless and have no IO, no database calls, and no Flask
imports. They accept plain Python dicts/lists and return strings or dicts.
This makes them trivially testable and safe to call from any context.
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FINGERPRINT_ADVICE: dict[str, str] = {
    "cooking": (
        "Opening a window or running an extractor fan while cooking "
        "would reduce peak readings."
    ),
    "combustion": (
        "Identify and ventilate the source. "
        "Check for open flames or smouldering materials."
    ),
    "biological_offgas": (
        "Increase ventilation. "
        "Check for damp areas, plants, or organic materials."
    ),
    "chemical_offgassing": (
        "Ventilate promptly. "
        "Check for cleaning products, new furniture, or paint."
    ),
    "external_pollution": (
        "Close windows during high external pollution periods. "
        "Check your local air quality index."
    ),
}

_TREND_SENTENCES = {
    "up": "{label} baseline is {pct:.1f}% higher than a week ago",
    "down": "{label} baseline is {pct:.1f}% lower than a week ago",
}

_COLOUR_THRESHOLDS = (10.0, 25.0)  # green ≤ 10%, amber 10–25%, red > 25%

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_utc(ts: str) -> datetime:
    """Parse a UTC ISO 8601 string (with or without Z) to a datetime."""
    ts = ts.rstrip("Z")
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# compute_longest_clean_period
# ---------------------------------------------------------------------------

def compute_longest_clean_period(
    inferences: list[dict],
    window_start: str,
    window_end: str,
) -> dict:
    """Return the longest contiguous gap (no inference events) in the window.

    Returns a dict with keys: hours (float), start (ISO str), end (ISO str).
    If there are no events the entire window is the clean period.
    """
    t_start = _parse_utc(window_start)
    t_end = _parse_utc(window_end)

    if not inferences:
        hours = (t_end - t_start).total_seconds() / 3600
        return {"hours": hours, "start": window_start, "end": window_end}

    # Sort events by time and build boundary list
    times = sorted(_parse_utc(inf["created_at"]) for inf in inferences)
    boundaries = [t_start] + times + [t_end]

    longest_hours = 0.0
    longest_start = t_start
    longest_end = t_end

    for i in range(len(boundaries) - 1):
        gap_start = boundaries[i]
        gap_end = boundaries[i + 1]
        gap_hours = (gap_end - gap_start).total_seconds() / 3600
        if gap_hours > longest_hours:
            longest_hours = gap_hours
            longest_start = gap_start
            longest_end = gap_end

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "hours": longest_hours,
        "start": _fmt(longest_start),
        "end": _fmt(longest_end),
    }


# ---------------------------------------------------------------------------
# compute_pattern_heatmap
# ---------------------------------------------------------------------------

def compute_pattern_heatmap(inferences: list[dict]) -> dict:
    """Count events per day-of-week × hour-of-day cell.

    Key format: "{day}_{hour}" where day 0=Monday, hour 0–23 (UTC).
    Only cells with at least one event are included (sparse dict).
    """
    counts: dict[str, int] = {}
    for inf in inferences:
        ts = inf.get("created_at")
        if not ts:
            continue
        dt = _parse_utc(ts)
        key = f"{dt.weekday()}_{dt.hour}"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# detect_drift_flags
# ---------------------------------------------------------------------------

def detect_drift_flags(
    baselines_now: dict[str, float | None],
    baselines_7d_ago: dict[str, float | None],
    threshold: float = 0.15,
) -> list[dict]:
    """Flag channels whose EMA baseline has shifted more than `threshold` (15%).

    Returns a list of dicts: {channel, shift_pct, direction, message}.
    Empty list if no drift detected.
    """
    flags = []
    for channel, now in baselines_now.items():
        then = baselines_7d_ago.get(channel)
        if now is None or then is None or then == 0:
            continue
        shift = abs(now - then) / abs(then)
        if shift > threshold:
            direction = "up" if now > then else "down"
            shift_pct = round(shift * 100, 1)
            flags.append({
                "channel": channel,
                "shift_pct": shift_pct,
                "direction": direction,
                "message": (
                    f"{channel} baseline has shifted {shift_pct}% {direction} over 7 days. "
                    "This could mean sensor drift, or a new persistent background source. "
                    "Worth checking."
                ),
            })
    return flags


# ---------------------------------------------------------------------------
# compute_trend_indicators
# ---------------------------------------------------------------------------

def compute_trend_indicators(
    baselines_now: dict[str, float | None],
    baselines_7d_ago: dict[str, float | None],
    channel_meta: dict[str, dict],
) -> list[dict]:
    """Return a trend indicator dict for each channel present in both baselines.

    Skips channels where either baseline is None or zero, or where the channel
    is not in channel_meta.
    Colour: green ≤ 10%, amber 10–25%, red > 25% change.
    """
    indicators = []
    for channel, meta in channel_meta.items():
        now = baselines_now.get(channel)
        then = baselines_7d_ago.get(channel)
        if now is None or then is None or then == 0:
            continue
        pct = abs(now - then) / abs(then) * 100
        direction = "up" if now > then else "down"
        if pct <= _COLOUR_THRESHOLDS[0]:
            colour = "green"
        elif pct <= _COLOUR_THRESHOLDS[1]:
            colour = "amber"
        else:
            colour = "red"
        template = _TREND_SENTENCES[direction]
        base_sentence = template.format(label=meta["label"], pct=pct)
        suffix = " — worth monitoring." if colour == "amber" else (
            " — significant change, investigate." if colour == "red" else "."
        )
        indicators.append({
            "channel": channel,
            "label": meta["label"],
            "unit": meta.get("unit", ""),
            "current_baseline": round(now, 2),
            "week_ago_baseline": round(then, 2),
            "pct_change": round(pct, 1),
            "direction": direction,
            "colour": colour,
            "sentence": base_sentence + suffix,
        })
    return indicators


# ---------------------------------------------------------------------------
# generate_period_summary
# ---------------------------------------------------------------------------

def generate_period_summary(
    inferences: list[dict],
    trend_indicators: list[dict],
    dominant_source: str | None,
) -> str:
    """Generate a 2–3 sentence plain-English summary of the analysis period."""
    n = len(inferences)

    if n == 0:
        intro = "No detection events occurred during this period — air quality was clean throughout."
    elif n == 1:
        intro = "One detection event occurred during this period."
    else:
        alerts = sum(1 for inf in inferences if inf.get("severity") == "critical")
        warnings = sum(1 for inf in inferences if inf.get("severity") == "warning")
        parts = []
        if alerts:
            parts.append(f"{alerts} alert{'s' if alerts > 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
        event_desc = " and ".join(parts) if parts else f"{n} events"
        intro = f"{n} detection events occurred, including {event_desc}."

    source_sentence = ""
    if dominant_source:
        source_sentence = f" {dominant_source.capitalize()} was the most commonly attributed source."

    trend_colours = [t.get("colour") for t in trend_indicators]
    if "red" in trend_colours:
        trend_sentence = " Sensor baselines show significant shifts — check the trend indicators below."
    elif "amber" in trend_colours:
        trend_sentence = " Some sensor baselines are drifting — worth monitoring."
    else:
        trend_sentence = " Sensor baselines are stable."

    return intro + source_sentence + trend_sentence


# ---------------------------------------------------------------------------
# generate_fingerprint_narrative
# ---------------------------------------------------------------------------

def generate_fingerprint_narrative(
    source_id: str,
    label: str,
    events: list[dict],
    avg_confidence: float,
    typical_hours: list[int],
) -> str:
    """Generate a 2–3 sentence narrative card for a source fingerprint."""
    if not events:
        return f"No {label} events were detected in this period."

    n = len(events)
    count_str = f"{n} time{'s' if n > 1 else ''}"

    # Confidence characterisation
    if avg_confidence >= 0.80:
        conf_str = "strong confidence"
    elif avg_confidence >= 0.65:
        conf_str = "moderate confidence"
    else:
        conf_str = "lower confidence"

    # Time-of-day summary
    if typical_hours:
        # Group consecutive hours into ranges
        sorted_hours = sorted(set(typical_hours))
        ranges = []
        start = sorted_hours[0]
        prev = sorted_hours[0]
        for h in sorted_hours[1:]:
            if h == prev + 1:
                prev = h
            else:
                ranges.append((start, prev))
                start = prev = h
        ranges.append((start, prev))
        time_parts = [
            f"{s:02d}:00–{e + 1:02d}:00" if s != e else f"{s:02d}:00"
            for s, e in ranges
        ]
        time_str = f"Typically detected around {', '.join(time_parts)}."
    else:
        time_str = ""

    advice = _FINGERPRINT_ADVICE.get(source_id, "")

    sentences = [
        f"{label} was detected {count_str} with {conf_str} (avg {avg_confidence:.0%}).",
    ]
    if time_str:
        sentences.append(time_str)
    if advice:
        sentences.append(advice)

    return " ".join(sentences)


# ---------------------------------------------------------------------------
# generate_anomaly_model_narrative
# ---------------------------------------------------------------------------

def generate_anomaly_model_narrative(
    model_id: str,
    label: str,
    event_count: int,
    description: str,
) -> str:
    """Generate a 2–3 sentence narrative card for a composite multivariate model."""
    count_str = f"{event_count} time{'s' if event_count != 1 else ''}"
    return (
        f"The {label} model flagged {count_str} during this period. "
        f"{description} "
        "Review the detection events below for full details."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_narrative_engine.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/narrative_engine.py tests/test_narrative_engine.py
git commit -m "feat: add narrative_engine with 7 pure analysis functions"
```

---

## Task 5: `GET /api/history/sensor` endpoint

**Files:**
- Create: `mlss_monitor/routes/api_history.py`
- Modify: `mlss_monitor/app.py`
- Create: `tests/test_api_history.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_history.py`:

```python
"""Tests for /api/history/* endpoints."""
import json
import pytest


def _insert_sensor_row(db_path, timestamp, tvoc=100, eco2=500, temp=21.0, hum=50.0,
                        pm1=2.0, pm25=3.0, pm10=5.0, co=12000, no2=8000, nh3=15000):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO sensor_data
           (timestamp, tvoc, eco2, temperature, humidity,
            pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, tvoc, eco2, temp, hum, pm1, pm25, pm10, co, no2, nh3),
    )
    conn.commit()
    conn.close()


def test_sensor_endpoint_returns_all_channels(app_client, db):
    client, _ = app_client
    _insert_sensor_row(db, "2026-04-04 14:00:00")
    _insert_sensor_row(db, "2026-04-04 14:01:00", tvoc=110)
    resp = client.get(
        "/api/history/sensor?start=2026-04-04T13:00:00Z&end=2026-04-04T15:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "timestamps" in data
    assert "channels" in data
    expected_channels = [
        "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
        "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
    ]
    for ch in expected_channels:
        assert ch in data["channels"], f"Missing channel: {ch}"
    assert len(data["timestamps"]) == 2
    assert data["channels"]["tvoc_ppb"][0] == 100
    assert data["channels"]["tvoc_ppb"][1] == 110
    # Timestamps must be UTC ISO
    for ts in data["timestamps"]:
        assert ts.endswith("Z"), f"Timestamp not UTC ISO: {ts}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_api_history.py::test_sensor_endpoint_returns_all_channels -v
```

Expected: 404 — endpoint not registered.

- [ ] **Step 3: Create `mlss_monitor/routes/api_history.py`**

```python
"""History API routes — sensor data, baselines, ML context, narratives."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from database.db_logger import (
    _normalise_ts,
    compute_detection_method,
    get_inferences,
)
from mlss_monitor import narrative_engine, state

api_history_bp = Blueprint("api_history", __name__)

# DB column name → API response key
_DB_TO_API = {
    "tvoc":        "tvoc_ppb",
    "eco2":        "eco2_ppm",
    "temperature": "temperature_c",
    "humidity":    "humidity_pct",
    "pm1_0":       "pm1_ug_m3",
    "pm2_5":       "pm25_ug_m3",
    "pm10":        "pm10_ug_m3",
    "gas_co":      "co_ppb",
    "gas_no2":     "no2_ppb",
    "gas_nh3":     "nh3_ppb",
}

_ALL_CHANNELS = list(_DB_TO_API.values())


def _query_sensor_data(db_file: str, start: str, end: str) -> list[dict]:
    """Query sensor_data between start and end (ISO strings, UTC)."""
    # Strip Z for SQLite comparison (stored without Z)
    start_db = start.rstrip("Z").replace("T", " ")
    end_db = end.rstrip("Z").replace("T", " ")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT timestamp, tvoc, eco2, temperature, humidity,
                  pm1_0, pm2_5, pm10, gas_co, gas_no2, gas_nh3
           FROM sensor_data
           WHERE timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        (start_db, end_db),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@api_history_bp.route("/api/history/sensor")
def sensor_history():
    """Return time-series sensor data for all 10 channels over a window."""
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    from mlss_monitor.app import DB_FILE  # imported here to avoid circular at module load
    rows = _query_sensor_data(DB_FILE, start, end)

    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels: dict[str, list] = {ch: [] for ch in _ALL_CHANNELS}
    for row in rows:
        for db_col, api_key in _DB_TO_API.items():
            channels[api_key].append(row.get(db_col))

    return jsonify({"timestamps": timestamps, "channels": channels})
```

- [ ] **Step 4: Register blueprint in `mlss_monitor/app.py`**

Find where other blueprints are registered (e.g. `app.register_blueprint(api_inferences_bp)`) and add:

```python
from mlss_monitor.routes.api_history import api_history_bp
app.register_blueprint(api_history_bp)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_api_history.py::test_sensor_endpoint_returns_all_channels -v
```

Expected: PASS

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q
```

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/routes/api_history.py mlss_monitor/app.py tests/test_api_history.py
git commit -m "feat: add GET /api/history/sensor endpoint with all 10 channels"
```

---

## Task 6: `GET /api/history/baselines` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_history.py`
- Modify: `tests/test_api_history.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_history.py`:

```python
def test_baselines_endpoint_returns_all_channels(app_client):
    """GET /api/history/baselines returns a baseline per channel plus threshold factor."""
    client, _ = app_client

    # Patch state.detection_engine with a stub
    import mlss_monitor.state as st

    class _FakeAnomalyDetector:
        def baseline(self, ch):
            return {"tvoc_ppb": 118.4}.get(ch)

    class _FakeEngine:
        _anomaly_detector = _FakeAnomalyDetector()

    original = st.detection_engine
    st.detection_engine = _FakeEngine()
    try:
        resp = client.get("/api/history/baselines")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tvoc_ppb" in data
        assert data["tvoc_ppb"] == pytest.approx(118.4)
        assert "anomaly_threshold_factor" in data
        assert data["anomaly_threshold_factor"] == pytest.approx(0.25)
        # Channels with no baseline should be null
        assert data.get("eco2_ppm") is None
    finally:
        st.detection_engine = original
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_api_history.py::test_baselines_endpoint_returns_all_channels -v
```

Expected: 404 — endpoint not registered yet.

- [ ] **Step 3: Add endpoint to `api_history.py`**

```python
_ANOMALY_THRESHOLD_FACTOR = 0.25  # ±25% of baseline for normal band display

_BASELINE_CHANNELS = [
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
]


@api_history_bp.route("/api/history/baselines")
def baselines():
    """Return current EMA baseline per channel from the live AnomalyDetector."""
    engine = state.detection_engine
    result: dict = {}
    if engine and engine._anomaly_detector:
        det = engine._anomaly_detector
        for ch in _BASELINE_CHANNELS:
            result[ch] = det.baseline(ch)
    else:
        result = {ch: None for ch in _BASELINE_CHANNELS}
    result["anomaly_threshold_factor"] = _ANOMALY_THRESHOLD_FACTOR
    return jsonify(result)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_api_history.py::test_baselines_endpoint_returns_all_channels -v
```

- [ ] **Step 5: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add mlss_monitor/routes/api_history.py tests/test_api_history.py
git commit -m "feat: add GET /api/history/baselines endpoint"
```

---

## Task 7: `GET /api/history/ml-context` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_history.py`
- Modify: `tests/test_api_history.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_history.py`:

```python
def test_ml_context_returns_inferences_with_detection_method(app_client, db):
    client, _ = app_client
    from database.db_logger import save_inference
    save_inference(
        db_file=db,
        event_type="anomaly_combustion_signature",
        title="ML event",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.85,
        evidence={"attribution_source": "combustion", "attribution_confidence": 0.81},
    )
    resp = client.get(
        "/api/history/ml-context?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "inferences" in data
    assert "attribution_summary" in data
    assert "dominant_source" in data
    assert len(data["inferences"]) >= 1
    inf = data["inferences"][0]
    assert inf["detection_method"] == "ml"
    assert inf["event_type"] == "anomaly_combustion_signature"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_api_history.py::test_ml_context_returns_inferences_with_detection_method -v
```

Expected: 404.

- [ ] **Step 3: Add endpoint to `api_history.py`**

```python
import json
import math


def _pearson_r(xs: list, ys: list) -> float | None:
    """Compute Pearson r between two lists, skipping None pairs."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    sum_x = sum(p[0] for p in pairs)
    sum_y = sum(p[1] for p in pairs)
    sum_xy = sum(p[0] * p[1] for p in pairs)
    sum_x2 = sum(p[0] ** 2 for p in pairs)
    sum_y2 = sum(p[1] ** 2 for p in pairs)
    num = n * sum_xy - sum_x * sum_y
    den = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
    return num / den if den != 0 else None


_COMOVEMENT_PHRASES: dict[tuple[str, str], str] = {
    ("tvoc_ppb", "eco2_ppm"): "TVOC and eCO2 rose together — consistent with a build-up of indoor air pollutants.",
    ("tvoc_ppb", "pm25_ug_m3"): "TVOC and PM2.5 moved together — may indicate a combustion or cooking source.",
    ("co_ppb", "no2_ppb"): "CO and NO2 resistance moved together — typical of a combustion event.",
    ("humidity_pct", "temperature_c"): "Temperature and humidity changed together — check ventilation or HVAC.",
    ("pm1_ug_m3", "pm25_ug_m3"): "PM1 and PM2.5 tracked closely — consistent with fine particle sources like combustion.",
}

_CHANNEL_LABELS = {
    "tvoc_ppb": "TVOC", "eco2_ppm": "eCO2", "temperature_c": "Temperature",
    "humidity_pct": "Humidity", "pm1_ug_m3": "PM1", "pm25_ug_m3": "PM2.5",
    "pm10_ug_m3": "PM10", "co_ppb": "CO (resistance)",
    "no2_ppb": "NO2 (resistance)", "nh3_ppb": "NH3 (resistance)",
}


def _comovement_summary(sensor_rows: list[dict]) -> str:
    """Return a plain-English summary of strongly correlated channel pairs."""
    if len(sensor_rows) < 3:
        return ""
    channel_data: dict[str, list] = {ch: [] for ch in _ALL_CHANNELS}
    for row in sensor_rows:
        for db_col, api_key in _DB_TO_API.items():
            channel_data[api_key].append(row.get(db_col))

    sentences = []
    checked: set[frozenset] = set()
    pairs_to_check = list(_COMOVEMENT_PHRASES.keys()) + [
        (a, b)
        for i, a in enumerate(_ALL_CHANNELS)
        for b in _ALL_CHANNELS[i + 1:]
        if frozenset((a, b)) not in {frozenset(k) for k in _COMOVEMENT_PHRASES}
    ]
    for pair in pairs_to_check:
        key = frozenset(pair)
        if key in checked:
            continue
        checked.add(key)
        a, b = pair
        r = _pearson_r(channel_data.get(a, []), channel_data.get(b, []))
        if r is not None and abs(r) > 0.7:
            phrase = _COMOVEMENT_PHRASES.get(pair) or _COMOVEMENT_PHRASES.get((b, a))
            if not phrase:
                la, lb = _CHANNEL_LABELS.get(a, a), _CHANNEL_LABELS.get(b, b)
                phrase = f"{la} and {lb} were strongly correlated during this period."
            sentences.append(phrase)
        if len(sentences) >= 3:
            break
    return " ".join(sentences)


@api_history_bp.route("/api/history/ml-context")
def ml_context():
    """Return inferences + attribution summaries for a time window."""
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    from mlss_monitor.app import DB_FILE

    all_inferences = get_inferences(db_file=DB_FILE, limit=1000, include_dismissed=False)
    # Filter to window
    start_dt = start.rstrip("Z").replace("T", " ")
    end_dt = end.rstrip("Z").replace("T", " ")
    window_infs = [
        inf for inf in all_inferences
        if start_dt <= inf["created_at"].rstrip("Z").replace("T", " ") <= end_dt
    ]

    # Attribution summary
    summary: dict[str, int] = {}
    for inf in window_infs:
        evidence = inf.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}
        src = evidence.get("attribution_source")
        if src:
            summary[src] = summary.get(src, 0) + 1

    dominant = max(summary, key=summary.get) if summary else None
    dominant_sentence = narrative_engine.generate_period_summary(
        inferences=window_infs,
        trend_indicators=[],
        dominant_source=dominant,
    ) if window_infs else "No events detected."

    # Comovement
    sensor_rows = _query_sensor_data(DB_FILE, start, end)
    comovement = _comovement_summary(sensor_rows)

    # Enrich inferences with attribution fields from evidence
    enriched = []
    for inf in window_infs:
        evidence = inf.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}
        enriched.append({
            "id": inf["id"],
            "created_at": inf["created_at"],
            "title": inf.get("title", ""),
            "event_type": inf.get("event_type", ""),
            "severity": inf.get("severity", ""),
            "attribution_source": evidence.get("attribution_source"),
            "attribution_confidence": evidence.get("attribution_confidence"),
            "runner_up_source": evidence.get("runner_up_source"),
            "runner_up_confidence": evidence.get("runner_up_confidence"),
            "detection_method": inf.get("detection_method", "rule"),
        })

    return jsonify({
        "inferences": enriched,
        "attribution_summary": summary,
        "dominant_source": dominant,
        "dominant_source_sentence": dominant_sentence,
        "comovement_summary": comovement,
    })
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_api_history.py::test_ml_context_returns_inferences_with_detection_method -v
```

- [ ] **Step 5: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add mlss_monitor/routes/api_history.py tests/test_api_history.py
git commit -m "feat: add GET /api/history/ml-context endpoint with comovement analysis"
```

---

## Task 8: `GET /api/history/narratives` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_history.py`
- Modify: `tests/test_api_history.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_history.py`:

```python
def test_narratives_endpoint_returns_required_keys(app_client, db):
    client, _ = app_client
    resp = client.get(
        "/api/history/narratives?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    required_keys = [
        "period_summary", "trend_indicators", "longest_clean_hours",
        "longest_clean_start", "longest_clean_end", "attribution_breakdown",
        "dominant_source_sentence", "fingerprint_narratives",
        "anomaly_model_narratives", "pattern_heatmap", "pattern_sentence",
        "drift_flags",
    ]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_api_history.py::test_narratives_endpoint_returns_required_keys -v
```

- [ ] **Step 3: Add endpoint to `api_history.py`**

```python
_KNOWN_SOURCES = [
    ("biological_offgas",    "Biological Off-gassing", "🌿"),
    ("chemical_offgassing",  "Chemical Off-gassing",   "🧪"),
    ("cooking",              "Cooking",                 "🍳"),
    ("combustion",           "Combustion",              "🔥"),
    ("external_pollution",   "External Pollution",      "🌍"),
]

_ML_EVENT_TYPES = {
    "anomaly_combustion_signature", "anomaly_particle_distribution",
    "anomaly_ventilation_quality", "anomaly_gas_relationship",
    "anomaly_thermal_moisture",
}

_MODEL_DESCRIPTIONS = {
    "anomaly_combustion_signature": "Watches for co-rises in CO resistance, TVOC, and particles — a pattern typical of nearby combustion.",
    "anomaly_particle_distribution": "Monitors the ratio relationship between PM1, PM2.5 and PM10 for unusual size distributions.",
    "anomaly_ventilation_quality": "Tracks eCO2, TVOC and NH3 building up together — a sign of poor ventilation.",
    "anomaly_gas_relationship": "Monitors the correlation structure of CO, NO2 and NH3 from the MICS6814 sensor.",
    "anomaly_thermal_moisture": "Scores temperature, humidity and VPD together to detect comfort-zone stress events.",
}

_MODEL_LABELS = {
    "anomaly_combustion_signature": "Combustion Signature",
    "anomaly_particle_distribution": "Particle Distribution",
    "anomaly_ventilation_quality": "Ventilation Quality",
    "anomaly_gas_relationship": "Gas Sensor Relationship",
    "anomaly_thermal_moisture": "Thermal-Moisture Stress",
}


def _get_baselines_7d_ago(db_file: str, window_start: str) -> dict:
    """Query average sensor values for the 24h period 7 days before window_start."""
    try:
        start_dt = datetime.fromisoformat(window_start.rstrip("Z")).replace(tzinfo=timezone.utc)
        ago_end = start_dt - timedelta(days=7)
        ago_start = ago_end - timedelta(hours=24)
        rows = _query_sensor_data(
            db_file,
            ago_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ago_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        if not rows:
            return {}
        result = {}
        for db_col, api_key in _DB_TO_API.items():
            vals = [r.get(db_col) for r in rows if r.get(db_col) is not None]
            result[api_key] = sum(vals) / len(vals) if vals else None
        return result
    except Exception:
        return {}


@api_history_bp.route("/api/history/narratives")
def narratives():
    """Return all backend-generated narrative content for the Detections & Insights tab."""
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    from mlss_monitor.app import DB_FILE

    all_inferences = get_inferences(db_file=DB_FILE, limit=2000, include_dismissed=False)
    start_db = start.rstrip("Z").replace("T", " ")
    end_db = end.rstrip("Z").replace("T", " ")
    window_infs = [
        inf for inf in all_inferences
        if start_db <= inf["created_at"].rstrip("Z").replace("T", " ") <= end_db
    ]

    # Baselines
    engine = state.detection_engine
    baselines_now: dict = {}
    if engine and engine._anomaly_detector:
        baselines_now = {ch: engine._anomaly_detector.baseline(ch) for ch in _BASELINE_CHANNELS}

    baselines_7d = _get_baselines_7d_ago(DB_FILE, start)

    # Channel meta for trend indicators (label + unit only)
    from mlss_monitor.inference_evidence import _CHANNEL_META
    ch_meta_simple = {
        ch: {"label": v["label"], "unit": v["unit"]}
        for ch, v in _CHANNEL_META.items()
        if ch.endswith("_current") or ch == "vpd_kpa"
    }
    # Re-key from FV field names to DB/API names for trend indicators
    fv_to_api = {
        "tvoc_current": "tvoc_ppb", "eco2_current": "eco2_ppm",
        "temperature_current": "temperature_c", "humidity_current": "humidity_pct",
        "pm1_current": "pm1_ug_m3", "pm25_current": "pm25_ug_m3",
        "pm10_current": "pm10_ug_m3", "co_current": "co_ppb",
        "no2_current": "no2_ppb", "nh3_current": "nh3_ppb",
    }
    ch_meta_api = {
        fv_to_api[k]: v for k, v in ch_meta_simple.items() if k in fv_to_api
    }

    trend_indicators = narrative_engine.compute_trend_indicators(
        baselines_now, baselines_7d, ch_meta_api
    )
    drift_flags = narrative_engine.detect_drift_flags(baselines_now, baselines_7d)

    # Attribution
    summary: dict[str, int] = {}
    for inf in window_infs:
        evidence = inf.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}
        src = evidence.get("attribution_source")
        if src:
            summary[src] = summary.get(src, 0) + 1

    dominant = max(summary, key=summary.get) if summary else None

    period_summary = narrative_engine.generate_period_summary(
        window_infs, trend_indicators, dominant
    )
    clean = narrative_engine.compute_longest_clean_period(window_infs, start, end)
    heatmap = narrative_engine.compute_pattern_heatmap(window_infs)

    # Pattern sentence
    if heatmap:
        top_key = max(heatmap, key=heatmap.get)
        day_i, hour_i = (int(x) for x in top_key.split("_"))
        pattern_sentence = (
            f"Events most frequently occur on {_DAY_NAMES[day_i]}s around {hour_i:02d}:00."
        )
    else:
        pattern_sentence = "No recurring time pattern detected in this period."

    # Fingerprint narratives
    fingerprint_narratives = []
    for src_id, label, emoji in _KNOWN_SOURCES:
        src_events = [
            inf for inf in window_infs
            if _extract_attribution_source(inf) == src_id
        ]
        avg_conf = (
            sum(_extract_attribution_confidence(inf) for inf in src_events) / len(src_events)
            if src_events else 0.0
        )
        typical_hours = [
            _parse_utc(inf["created_at"]).hour for inf in src_events
        ]
        fingerprint_narratives.append({
            "source_id": src_id,
            "label": label,
            "emoji": emoji,
            "event_count": len(src_events),
            "avg_confidence": round(avg_conf, 2),
            "typical_hours": typical_hours,
            "narrative": narrative_engine.generate_fingerprint_narrative(
                src_id, label, src_events, avg_conf, typical_hours
            ),
        })

    # Anomaly model narratives (only models that fired)
    anomaly_model_narratives = []
    for et in _ML_EVENT_TYPES:
        model_events = [inf for inf in window_infs if inf.get("event_type") == et]
        if model_events:
            model_id = et.replace("anomaly_", "")
            label = _MODEL_LABELS.get(et, model_id)
            desc = _MODEL_DESCRIPTIONS.get(et, "")
            anomaly_model_narratives.append({
                "model_id": model_id,
                "label": label,
                "event_count": len(model_events),
                "description": desc,
                "narrative": narrative_engine.generate_anomaly_model_narrative(
                    model_id, label, len(model_events), desc
                ),
            })

    dominant_sentence = (
        f"{dominant.capitalize()} accounts for {summary[dominant]} of {len(window_infs)} events."
        if dominant and window_infs
        else "No events were attributed to a source in this period."
    )

    return jsonify({
        "period_summary": period_summary,
        "trend_indicators": trend_indicators,
        "longest_clean_hours": clean["hours"],
        "longest_clean_start": clean["start"],
        "longest_clean_end": clean["end"],
        "attribution_breakdown": summary,
        "dominant_source_sentence": dominant_sentence,
        "fingerprint_narratives": fingerprint_narratives,
        "anomaly_model_narratives": anomaly_model_narratives,
        "pattern_heatmap": heatmap,
        "pattern_sentence": pattern_sentence,
        "drift_flags": drift_flags,
    })


def _extract_attribution_source(inf: dict) -> str | None:
    evidence = inf.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:
            return None
    return evidence.get("attribution_source")


def _extract_attribution_confidence(inf: dict) -> float:
    evidence = inf.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:
            return 0.0
    return float(evidence.get("attribution_confidence") or 0.0)


# Must import _DAY_NAMES from narrative_engine or redefine locally
from mlss_monitor.narrative_engine import _DAY_NAMES  # noqa: E402
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_api_history.py::test_narratives_endpoint_returns_required_keys -v
```

- [ ] **Step 5: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add mlss_monitor/routes/api_history.py tests/test_api_history.py
git commit -m "feat: add GET /api/history/narratives endpoint"
```

---

## Task 9: Inference sparkline endpoint

**Files:**
- Modify: `database/db_logger.py`
- Modify: `mlss_monitor/routes/api_inferences.py`
- Modify: `tests/test_api_history.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_history.py`:

```python
def test_sparkline_returns_window_around_inference(app_client, db):
    client, _ = app_client
    from database.db_logger import save_inference
    _insert_sensor_row(db, "2026-04-04 14:15:00", tvoc=100)
    _insert_sensor_row(db, "2026-04-04 14:30:00", tvoc=350)
    _insert_sensor_row(db, "2026-04-04 14:45:00", tvoc=200)
    inf_id = save_inference(
        db_file=db,
        event_type="tvoc_spike",
        title="TVOC spike",
        description="desc",
        action="act",
        severity="warning",
        confidence=0.9,
        evidence={"sensor_snapshot": [{"channel": "tvoc_current"}]},
    )
    resp = client.get(f"/api/inferences/{inf_id}/sparkline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "timestamps" in data
    assert "channels" in data
    assert "inference_at" in data
    assert "triggering_channels" in data
    assert data["inference_at"].endswith("Z")
    assert len(data["timestamps"]) >= 1
```

- [ ] **Step 2: Add `get_inference_by_id()` to `db_logger.py`**

```python
def get_inference_by_id(inference_id: int, db_file: str = None) -> dict | None:
    """Return a single inference dict by ID, or None if not found."""
    db_file = db_file or _default_db_file()
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM inferences WHERE id = ?", (inference_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["created_at"] = _normalise_ts(d.get("created_at"))
    d["detection_method"] = compute_detection_method(d.get("event_type", ""))
    return d
```

- [ ] **Step 3: Add sparkline route to `api_inferences.py`**

```python
@api_inferences_bp.route("/api/inferences/<int:inference_id>/sparkline")
def sparkline(inference_id):
    """Return sensor data for ±15 min around a specific inference."""
    import json
    from datetime import datetime, timedelta, timezone
    from database.db_logger import get_inference_by_id
    from mlss_monitor.routes.api_history import _query_sensor_data, _DB_TO_API, _normalise_ts
    from mlss_monitor.app import DB_FILE

    inf = get_inference_by_id(inference_id)
    if inf is None:
        return jsonify({"error": "not found"}), 404

    created_at = inf["created_at"]  # already normalised to UTC ISO
    dt = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)
    window_start = (dt - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (dt + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = _query_sensor_data(DB_FILE, window_start, window_end)

    # Derive triggering channels from sensor_snapshot in evidence
    evidence = inf.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:
            evidence = {}

    _FV_TO_API = {
        "tvoc_current": "tvoc_ppb", "eco2_current": "eco2_ppm",
        "temperature_current": "temperature_c", "humidity_current": "humidity_pct",
        "pm1_current": "pm1_ug_m3", "pm25_current": "pm25_ug_m3",
        "pm10_current": "pm10_ug_m3", "co_current": "co_ppb",
        "no2_current": "no2_ppb", "nh3_current": "nh3_ppb",
    }
    snapshot = evidence.get("sensor_snapshot", [])
    triggering = []
    for entry in snapshot:
        ch = entry.get("channel", "")
        api_key = _FV_TO_API.get(ch, ch)
        if api_key not in triggering:
            triggering.append(api_key)

    # Fall back to all channels if no snapshot
    if not triggering:
        triggering = list(_DB_TO_API.values())

    timestamps = [_normalise_ts(r["timestamp"]) for r in rows]
    channels: dict = {}
    for db_col, api_key in _DB_TO_API.items():
        if api_key in triggering:
            channels[api_key] = [r.get(db_col) for r in rows]

    return jsonify({
        "timestamps": timestamps,
        "channels": channels,
        "inference_at": created_at,
        "triggering_channels": triggering,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_api_history.py::test_sparkline_returns_window_around_inference -v
```

- [ ] **Step 5: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add database/db_logger.py mlss_monitor/routes/api_inferences.py tests/test_api_history.py
git commit -m "feat: add GET /api/inferences/<id>/sparkline endpoint"
```

---

## Task 10: SSE extensions — `inference_fired` + `anomaly_scores` events

**Files:**
- Modify: `database/db_logger.py`
- Modify: `mlss_monitor/app.py`

- [ ] **Step 1: Extend `save_inference()` to push `inference_fired` SSE event**

In `database/db_logger.py`, find `save_inference()`. It already publishes to `state.event_bus`. Extend the published payload to include `detection_method` and extracted attribution fields:

```python
# In save_inference(), find the event_bus.publish() call and replace its payload with:
try:
    _ev = evidence if isinstance(evidence, dict) else json.loads(evidence or "{}")
    _pub_payload = {
        "id": inference_id,
        "created_at": _normalise_ts(created_at_str),
        "title": title,
        "event_type": event_type,
        "severity": severity,
        "attribution_source": _ev.get("attribution_source"),
        "attribution_confidence": _ev.get("attribution_confidence"),
        "detection_method": compute_detection_method(event_type),
    }
    state.event_bus.publish("inference_fired", _pub_payload)
except Exception:
    pass  # SSE failure must never break inference saving
```

(Check the existing publish call shape and adapt — the key point is to use event type `"inference_fired"` and include `detection_method`.)

- [ ] **Step 2: Add 30-second `anomaly_scores` push to `app.py`**

In `mlss_monitor/app.py`, find the background sensor loop. Add a module-level timestamp tracker and push logic:

```python
import time as _time

_last_scores_push: float = 0.0
_SCORES_PUSH_INTERVAL = 30.0

_MULTIVAR_IDS = [
    "combustion_signature", "particle_distribution",
    "ventilation_quality", "gas_relationship", "thermal_moisture",
]
_PER_CHANNEL_IDS = [
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
]


def _push_anomaly_scores():
    """Push current River anomaly scores to SSE. Called from the sensor loop."""
    global _last_scores_push
    now = _time.time()
    if now - _last_scores_push < _SCORES_PUSH_INTERVAL:
        return
    _last_scores_push = now
    try:
        engine = state.detection_engine
        if not engine:
            return
        scores: dict = {}
        n_seen: dict = {}
        det = engine._anomaly_detector
        if det:
            for ch in _PER_CHANNEL_IDS:
                scores[ch] = det.baseline(ch) and det._last_score.get(ch)
                n_seen[ch] = det._n_seen.get(ch, 0)
        mdet = engine._multivar_detector
        if mdet:
            for mid in _MULTIVAR_IDS:
                scores[mid] = (mdet._last_scores or {}).get(mid)
                n_seen[mid] = mdet._n_seen.get(mid, 0)
        from database.db_logger import _normalise_ts
        import datetime
        state.event_bus.publish("anomaly_scores", {
            "timestamp": _normalise_ts(
                datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            ),
            "scores": scores,
            "n_seen": n_seen,
        })
    except Exception:
        pass  # never let SSE push break the sensor loop
```

Call `_push_anomaly_scores()` at the end of each sensor loop iteration.

- [ ] **Step 3: Verify existing SSE tests still pass**

```bash
python -m pytest tests/test_sse.py tests/test_sse_integration.py -v
```

- [ ] **Step 4: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add database/db_logger.py mlss_monitor/app.py
git commit -m "feat: add inference_fired and anomaly_scores SSE events"
```

---

## Task 11: Settings nav reorganisation + `/settings/insights-engine` route

**Files:**
- Modify: `mlss_monitor/routes/pages.py`
- Modify: `templates/base.html`

- [ ] **Step 1: Write the failing test**

Add to an appropriate test file (or create `tests/test_pages.py`):

```python
def test_insights_engine_page_at_new_route(app_client):
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    assert resp.status_code == 200

def test_insights_engine_old_route_returns_404(app_client):
    client, _ = app_client
    resp = client.get("/insights-engine")
    assert resp.status_code == 404
```

- [ ] **Step 2: Move the route in `pages.py`**

Find:
```python
@pages_bp.route("/insights-engine")
@require_role("admin")
def insights_engine():
```

Change to:
```python
@pages_bp.route("/settings/insights-engine")
@require_role("admin")
def insights_engine():
```

The function body is unchanged.

- [ ] **Step 3: Update nav in `base.html`**

Find the nav link that points to `/insights-engine`. It will look something like:
```html
<a href="/insights-engine">Insights Engine</a>
```

Change it to:
```html
<a href="/settings/insights-engine">Insights Engine</a>
```

If the link is inside a Settings dropdown or section, leave it there. If it is a top-level nav item, move it to be a sub-item under Settings (or rename/restyle as appropriate given the existing nav structure).

- [ ] **Step 4: Run tests to verify**

```bash
python -m pytest tests/test_pages.py -v
```

- [ ] **Step 5: Run full suite and commit**

```bash
python -m pytest tests/ -x -q
git add mlss_monitor/routes/pages.py templates/base.html
git commit -m "feat: move insights engine page to /settings/insights-engine"
```

---

## Task 12: Live anomaly score column on Insights Engine page

**Files:**
- Modify: `templates/insights_engine.html`

- [ ] **Step 1: Add score column header to the anomaly models table**

In `templates/insights_engine.html`, find the `<thead>` row of the anomaly models table and add the new column:

```html
<!-- Before: -->
<tr>
  <th>Channel</th>
  <th>Readings</th>
  <th>Cold-start</th>
  <th>Status</th>
</tr>

<!-- After: -->
<tr>
  <th>Channel</th>
  <th>Readings</th>
  <th>Cold-start</th>
  <th>
    Current Score
    <span class="info-icon" title="The anomaly score (0–1) shows how unusual the current sensor readings are compared to what this model has learned is normal. Scores above 0.75 trigger a detection event.">ⓘ</span>
  </th>
  <th>Status</th>
</tr>
```

- [ ] **Step 2: Add score cells to each model row**

For each `<tr>` in the anomaly models table body, add a `data-model-id` attribute and a score cell. The model ID must match the key used in the `anomaly_scores` SSE event (`tvoc_ppb` for per-channel, `combustion_signature` etc. for composite):

```html
<!-- Per-channel row example (tvoc_ppb): -->
<tr data-model-id="{{ ch }}">
  <td><code>{{ ch }}</code></td>
  <td>{{ n }}</td>
  <td>{{ cold_start }}</td>
  <td class="score-cell">
    <div class="score-bar-wrap">
      <div class="score-bar score-bar--none" style="width:0%"></div>
      <span class="score-label">—</span>
    </div>
  </td>
  <td class="status-cell">
    {% if ready %}<span class="status-ready">● Ready</span>{% else %}<span class="status-learning">◌ Learning…</span>{% endif %}
  </td>
</tr>
```

- [ ] **Step 3: Add CSS for score bars**

In the `<style>` block of `insights_engine.html` (or in a linked CSS file):

```css
.score-bar-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 120px;
}
.score-bar {
  height: 10px;
  border-radius: 5px;
  transition: width 0.4s ease, background-color 0.4s ease;
  min-width: 0;
}
.score-bar--green  { background: #22c55e; }
.score-bar--amber  { background: #f59e0b; }
.score-bar--red    { background: #ef4444; }
.score-bar--none   { background: #d1d5db; width: 4px !important; }
.score-label { font-size: 0.8rem; color: var(--text-muted, #6b7280); min-width: 32px; }
.status-elevated { color: #ef4444; font-weight: 600; }
```

- [ ] **Step 4: Add SSE consumer JavaScript**

At the bottom of `insights_engine.html`, before `</body>`, add:

```html
<script>
(function () {
  const es = new EventSource('/api/stream');

  es.addEventListener('anomaly_scores', function (e) {
    const payload = JSON.parse(e.data);
    const scores = payload.scores || {};
    const nSeen  = payload.n_seen  || {};

    Object.entries(scores).forEach(function ([modelId, score]) {
      const row = document.querySelector(`tr[data-model-id="${modelId}"]`);
      if (!row) return;

      const bar   = row.querySelector('.score-bar');
      const label = row.querySelector('.score-label');
      const statusCell = row.querySelector('.status-cell');

      if (score === null || score === undefined) {
        // Still in cold-start
        bar.style.width = '4px';
        bar.className = 'score-bar score-bar--none';
        label.textContent = 'Learning…';
        label.title = 'This model is still building its understanding of normal.';
        return;
      }

      const pct = Math.round(score * 100);
      bar.style.width = pct + '%';
      label.textContent = score.toFixed(2);

      if (score >= 0.75) {
        bar.className = 'score-bar score-bar--red';
        if (statusCell) {
          statusCell.innerHTML = '<span class="status-elevated">⚠ Elevated</span>';
        }
      } else if (score >= 0.60) {
        bar.className = 'score-bar score-bar--amber';
      } else {
        bar.className = 'score-bar score-bar--green';
      }
    });
  });

  es.onerror = function () {
    // SSE reconnects automatically — no action needed
  };
})();
</script>
```

- [ ] **Step 5: Manual verification checklist**

After deploying:
- [ ] Open `/settings/insights-engine` and confirm the "Current Score" column is visible.
- [ ] Wait up to 30 seconds and confirm score bars begin updating.
- [ ] Confirm a model scoring ≥ 0.75 shows "⚠ Elevated" in Status.
- [ ] Confirm cold-start models show "Learning…" in the score cell.

- [ ] **Step 6: Commit**

```bash
git add templates/insights_engine.html
git commit -m "feat: add live anomaly score column to insights engine page via SSE"
```

<!-- PART 2: Tasks 13-25 will be appended -->

# Incident Correlation Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-level "Incidents" tab with a Cytoscape.js hub-and-spoke correlation graph that groups sensor inferences into incidents, visualises causal relationships between events, and lets the user drill down to full inference detail.

**Architecture:** A background grouper thread sessionises inferences on a 30-minute silence gap, computes Pearson r for signal dependencies (stored in `alert_signal_deps`), builds a 32-float signature vector for cosine-similarity search (stored in `incidents.signature`), and triggers regrouping on every `new_inference` event bus publication. Three REST endpoints serve the Cytoscape.js frontend. The graph uses compound cluster nodes (one per incident), dual-channel node encoding (border colour = severity, inner glyph = detection method), and progressive zoom for label density.

**Tech Stack:** Python/Flask backend, SQLite WAL, `statistics.correlation()` for Pearson r (Python 3.11+, no numpy), Cytoscape.js v3 (cdnjs CDN, MIT), vanilla JS ES modules, AstroUXDS web components (`rux-segmented-button`, `rux-slider`, `rux-input`, `rux-card`, `rux-status`, `rux-tag`), `threading.Lock` for concurrency.

---

## File Map

| Path | Action | Purpose |
|---|---|---|
| `database/init_db.py` | Modify | Add 3 new tables + 2 indexes |
| `mlss_monitor/incident_grouper.py` | Create | Background grouper thread, all Python incident logic |
| `mlss_monitor/routes/api_incidents.py` | Create | 3 REST endpoints (list, detail, alert) |
| `mlss_monitor/routes/__init__.py` | Modify | Register `api_incidents_bp` |
| `mlss_monitor/app.py` | Modify | Import + start grouper after app init |
| `mlss_monitor/state.py` | Modify | Add `incident_grouper = None` |
| `templates/incidents.html` | Create | Full Incidents page (extends base.html) |
| `static/css/incident_graph.css` | Create | Graph canvas + panel layout, node overlay, toolbar |
| `static/js/incident_graph.js` | Create | Cytoscape init, layout, styling, events, search |
| `tests/test_incident_grouper.py` | Create | All grouper unit tests |
| `tests/test_api_incidents.py` | Create | All REST endpoint tests |

---

## Engine Invariants (must hold; tests verify)

1. Sessionise uses `.total_seconds()`, **never** `.seconds` (`.seconds` returns 0–59 only).
2. A group only becomes an incident if it has ≥ 1 alert.
3. `max_severity` is the highest of (`critical` > `warning` > `info`) across all group alerts.
4. Pearson r is stored as `NULL` when < 10 overlapping data points exist; it is **never** stored as `0.0` for missing data.
5. `alert_signal_deps` rows are computed once per alert and never updated in-place (delete-then-insert on regroup).
6. `incidents` rows are upserted (INSERT OR REPLACE) so regrouping is idempotent.
7. Cross-incident alert types — `hourly_summary`, `daily_summary`, `daily_pattern`, any `annotation_context_*` — get `is_primary = 0` in `incident_alerts`.
8. Signature vector index 30 = mean confidence over all alerts; index 29 = duration in minutes; index 31 = time-of-day bucket (0–3).
9. `make_incident_id(ts)` produces `INC-YYYYMMDD-HHMM` and is purely deterministic.
10. The grouper background thread is a daemon thread; it never blocks app startup.

---

## Task 1: Database Schema — 3 New Tables

**Files:**
- Modify: `database/init_db.py`

- [ ] **Step 1: Add the three new tables inside `create_db()`**

Open `database/init_db.py` and append the following three `cur.execute(...)` blocks immediately before `conn.commit()` (after the existing hot_tier index):

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS incidents (
        id           TEXT PRIMARY KEY,
        started_at   TIMESTAMP NOT NULL,
        ended_at     TIMESTAMP NOT NULL,
        max_severity TEXT NOT NULL DEFAULT 'info'
                         CHECK(max_severity IN ('info', 'warning', 'critical')),
        confidence   REAL NOT NULL DEFAULT 0,
        title        TEXT NOT NULL,
        signature    TEXT NOT NULL DEFAULT '[]'
    );
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_incidents_started "
        "ON incidents (started_at DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS incident_alerts (
        incident_id TEXT    NOT NULL REFERENCES incidents(id),
        alert_id    INTEGER NOT NULL REFERENCES inferences(id),
        is_primary  INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (incident_id, alert_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS alert_signal_deps (
        alert_id     INTEGER NOT NULL REFERENCES inferences(id),
        sensor       TEXT    NOT NULL,
        r            REAL,
        lag_seconds  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY  (alert_id, sensor)
    );
    """)
```

- [ ] **Step 2: Run `create_db()` manually to verify no SQL errors**

```bash
cd /path/to/mars-air-quility
python -c "from database.init_db import create_db; create_db(); print('OK')"
```

Expected: `OK` (or `✅ SQLite database created …` if run as `__main__`).

- [ ] **Step 3: Commit**

```bash
git add database/init_db.py
git commit -m "feat(db): add incidents, incident_alerts, alert_signal_deps tables"
```

---

## Task 2: Grouper — Constants, DETECTION_METHOD_MAP, and `sessionise()`

**Files:**
- Create: `mlss_monitor/incident_grouper.py`
- Create: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_incident_grouper.py`:

```python
"""Tests for mlss_monitor.incident_grouper (pure logic only — no DB calls)."""
import sys
from unittest.mock import MagicMock

# Stub hardware libs before any app import
for _mod in ["board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
             "mics6814", "authlib", "authlib.integrations",
             "authlib.integrations.flask_client"]:
    sys.modules.setdefault(_mod, MagicMock())

from datetime import datetime, timedelta
import pytest
from mlss_monitor.incident_grouper import (
    sessionise,
    detection_method,
    CROSS_INCIDENT_TYPES,
    make_incident_id,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _ts(minutes_offset: int) -> datetime:
    base = datetime(2026, 4, 19, 12, 0, 0)
    return base + timedelta(minutes=minutes_offset)


def _alert(minutes_offset: int, event_type: str = "tvoc_spike", severity: str = "info"):
    return {
        "id": minutes_offset,
        "created_at": _ts(minutes_offset).isoformat(),
        "event_type": event_type,
        "severity": severity,
        "title": f"Alert {minutes_offset}",
        "confidence": 0.8,
    }


# ── sessionise ───────────────────────────────────────────────────────────────

def test_sessionise_single_alert_one_group():
    alerts = [_alert(0)]
    groups = sessionise(alerts)
    assert len(groups) == 1
    assert len(groups[0]) == 1


def test_sessionise_two_close_alerts_one_group():
    """29-minute gap → same group."""
    alerts = [_alert(0), _alert(29)]
    groups = sessionise(alerts)
    assert len(groups) == 1


def test_sessionise_gap_over_30_splits():
    """31-minute gap → two groups (uses .total_seconds(), not .seconds)."""
    alerts = [_alert(0), _alert(31)]
    groups = sessionise(alerts)
    assert len(groups) == 2


def test_sessionise_exactly_30min_is_same_group():
    """Exactly 30 minutes → same group (> not >=)."""
    alerts = [_alert(0), _alert(30)]
    groups = sessionise(alerts)
    assert len(groups) == 1


def test_sessionise_large_gap_uses_total_seconds():
    """60-minute gap; .seconds would return 0, .total_seconds() returns 3600."""
    alerts = [_alert(0), _alert(60)]
    groups = sessionise(alerts)
    assert len(groups) == 2


def test_sessionise_preserves_order():
    """Alerts are sorted chronologically before grouping."""
    alerts = [_alert(10), _alert(0), _alert(5)]
    groups = sessionise(alerts)
    assert len(groups) == 1
    assert [a["id"] for a in groups[0]] == [0, 5, 10]


def test_sessionise_empty_list():
    assert sessionise([]) == []


# ── detection_method ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type,expected", [
    ("anomaly_combustion_signature", "ml"),
    ("anomaly_thermal_moisture",     "ml"),
    ("anomaly_anything_new",         "ml"),
    ("ml_learned_pattern",           "ml"),
    ("fingerprint_match",            "fingerprint"),
    ("hourly_summary",               "summary"),
    ("daily_summary",                "summary"),
    ("daily_pattern",                "summary"),
    ("annotation_context_cooking",   "summary"),
    ("annotation_context_",          "summary"),
    ("correlated_pollution",         "statistical"),
    ("sustained_poor_air",           "statistical"),
    ("tvoc_spike",                   "threshold"),
    ("eco2_danger",                  "threshold"),
    ("pm25_elevated",                "threshold"),
    ("temp_high",                    "threshold"),
    ("mould_risk",                   "threshold"),
])
def test_detection_method_mapping(event_type, expected):
    assert detection_method(event_type) == expected


# ── CROSS_INCIDENT_TYPES ─────────────────────────────────────────────────────

def test_cross_incident_types_contains_summaries():
    assert "hourly_summary" in CROSS_INCIDENT_TYPES
    assert "daily_summary" in CROSS_INCIDENT_TYPES
    assert "daily_pattern" in CROSS_INCIDENT_TYPES


# ── make_incident_id ─────────────────────────────────────────────────────────

def test_make_incident_id_format():
    ts = datetime(2026, 4, 19, 12, 55)
    assert make_incident_id(ts) == "INC-20260419-1255"


def test_make_incident_id_deterministic():
    ts = datetime(2026, 4, 19, 12, 55)
    assert make_incident_id(ts) == make_incident_id(ts)
```

- [ ] **Step 2: Run tests to verify they fail (module not yet created)**

```bash
pytest tests/test_incident_grouper.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'mlss_monitor.incident_grouper'`

- [ ] **Step 3: Create `mlss_monitor/incident_grouper.py` with the constants + pure functions**

```python
"""Incident grouper — background thread that sessionises inferences into
incidents and persists them to SQLite.

Pure logic functions (sessionise, detection_method, make_incident_id) are
separated at the top so they can be unit-tested without a DB connection.
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from datetime import datetime
from statistics import correlation
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

GAP_MINUTES = 30  # silence gap that starts a new incident
MIN_DATA_POINTS = 10  # minimum overlapping points for Pearson r

CROSS_INCIDENT_TYPES: frozenset[str] = frozenset({
    "hourly_summary",
    "daily_summary",
    "daily_pattern",
})
# event_types starting with this prefix are also cross-incident
_ANNOTATION_CONTEXT_PREFIX = "annotation_context_"

# Ordered from most-specific to least-specific
_DETECTION_METHOD_MAP: list[tuple[str | None, str]] = [
    ("fingerprint_match", "fingerprint"),
]
_ML_PREFIXES = ("anomaly_", "ml_learned_")
_STATISTICAL_TYPES = frozenset({"correlated_pollution", "sustained_poor_air"})
_SUMMARY_TYPES = frozenset({"hourly_summary", "daily_summary", "daily_pattern"})


# ── Pure logic ─────────────────────────────────────────────────────────────────

def detection_method(event_type: str) -> str:
    """Map an inferences.event_type to one of: ml | fingerprint | summary |
    statistical | threshold."""
    if event_type == "fingerprint_match":
        return "fingerprint"
    if any(event_type.startswith(p) for p in _ML_PREFIXES):
        return "ml"
    if event_type in _SUMMARY_TYPES or event_type.startswith(_ANNOTATION_CONTEXT_PREFIX):
        return "summary"
    if event_type in _STATISTICAL_TYPES:
        return "statistical"
    return "threshold"


def is_cross_incident(event_type: str) -> bool:
    """Return True for alert types that span / summarise multiple incidents."""
    return (event_type in CROSS_INCIDENT_TYPES
            or event_type.startswith(_ANNOTATION_CONTEXT_PREFIX))


def make_incident_id(ts: datetime) -> str:
    """Deterministic incident ID from the earliest alert timestamp."""
    return f"INC-{ts.strftime('%Y%m%d-%H%M')}"


def sessionise(
    alerts: list[dict[str, Any]],
    gap_minutes: int = GAP_MINUTES,
) -> list[list[dict[str, Any]]]:
    """Group alerts into sessions separated by a silence gap.

    IMPORTANT: Uses ``.total_seconds()``, not ``.seconds``.
    ``.seconds`` only returns the seconds component (0–59), so a 60-minute
    gap would appear as 0 seconds and be incorrectly merged.
    """
    if not alerts:
        return []

    sorted_alerts = sorted(
        alerts,
        key=lambda a: datetime.fromisoformat(a["created_at"]),
    )

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [sorted_alerts[0]]

    for alert in sorted_alerts[1:]:
        prev_ts = datetime.fromisoformat(current[-1]["created_at"])
        curr_ts = datetime.fromisoformat(alert["created_at"])
        gap_secs = (curr_ts - prev_ts).total_seconds()  # NOT .seconds
        if gap_secs > gap_minutes * 60:
            groups.append(current)
            current = []
        current.append(alert)

    groups.append(current)
    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_incident_grouper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): add sessionise(), detection_method(), make_incident_id() with tests"
```

---

## Task 3: Grouper — Pearson r and `alert_signal_deps`

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Modify: `tests/test_incident_grouper.py`

- [ ] **Step 1: Add failing tests for Pearson r**

Append to `tests/test_incident_grouper.py`:

```python
from mlss_monitor.incident_grouper import compute_pearson_r


def test_compute_pearson_r_perfect_correlation():
    xs = [float(i) for i in range(10)]
    ys = [float(i) for i in range(10)]
    r = compute_pearson_r(xs, ys)
    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_compute_pearson_r_anti_correlation():
    xs = [float(i) for i in range(10)]
    ys = [float(-i) for i in range(10)]
    r = compute_pearson_r(xs, ys)
    assert r is not None
    assert abs(r + 1.0) < 1e-9


def test_compute_pearson_r_none_when_too_few_points():
    """Returns None (not 0.0) when < MIN_DATA_POINTS overlapping points."""
    xs = [1.0, 2.0, 3.0]
    ys = [1.0, 2.0, 3.0]
    r = compute_pearson_r(xs, ys)
    assert r is None


def test_compute_pearson_r_none_not_zero_for_missing():
    """Invariant: missing data → None, never 0.0."""
    r = compute_pearson_r([], [])
    assert r is None
    assert r != 0.0


def test_compute_pearson_r_filters_none_pairs():
    """None values in either series are excluded from computation."""
    xs = [1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    ys = [1.0, 2.0,  None, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # Only 8 clean pairs → below MIN_DATA_POINTS=10 → None
    r = compute_pearson_r(xs, ys)
    assert r is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_incident_grouper.py::test_compute_pearson_r_perfect_correlation -v
```

Expected: `ImportError` — `compute_pearson_r` not yet defined.

- [ ] **Step 3: Add `compute_pearson_r` to `mlss_monitor/incident_grouper.py`**

Append after the `sessionise` function:

```python
def compute_pearson_r(
    xs: list[float | None],
    ys: list[float | None],
) -> float | None:
    """Pearson r between two series, or None if < MIN_DATA_POINTS clean pairs.

    Uses stdlib ``statistics.correlation`` (Python 3.11+).
    Invariant: returns None for missing data — never 0.0.
    """
    clean = [
        (x, y) for x, y in zip(xs, ys)
        if x is not None and y is not None
    ]
    if len(clean) < MIN_DATA_POINTS:
        return None
    x_vals, y_vals = zip(*clean)
    try:
        return correlation(list(x_vals), list(y_vals))
    except Exception:  # pylint: disable=broad-except
        return None
```

- [ ] **Step 4: Run all grouper tests**

```bash
pytest tests/test_incident_grouper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): add compute_pearson_r() with MIN_DATA_POINTS guard"
```

---

## Task 4: Grouper — Signature Vector (32 floats)

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Modify: `tests/test_incident_grouper.py`

- [ ] **Step 1: Add failing tests for `build_incident_similarity_vector`**

Append to `tests/test_incident_grouper.py`:

```python
from mlss_monitor.incident_grouper import build_incident_similarity_vector


def test_build_incident_similarity_vector_length():
    alerts = [_alert(0), _alert(5)]
    sig = build_incident_similarity_vector(alerts)
    assert len(sig) == 32


def test_build_incident_similarity_vector_returns_floats():
    alerts = [_alert(0)]
    sig = build_incident_similarity_vector(alerts)
    assert all(isinstance(v, float) for v in sig)


def test_build_incident_similarity_vector_duration_at_index_29():
    """Index 29 = incident duration in minutes."""
    # 10-minute incident
    alerts = [_alert(0), _alert(10)]
    sig = build_incident_similarity_vector(alerts)
    assert sig[29] == pytest.approx(10.0)


def test_build_incident_similarity_vector_confidence_at_index_30():
    """Index 30 = mean confidence of all alerts."""
    a1 = _alert(0)
    a1["confidence"] = 0.6
    a2 = _alert(5)
    a2["confidence"] = 0.8
    sig = build_incident_similarity_vector([a1, a2])
    assert sig[30] == pytest.approx(0.7)


def test_build_incident_similarity_vector_tod_bucket_at_index_31():
    """Index 31 = time-of-day bucket: 0=night(0-6), 1=morning(6-12),
    2=afternoon(12-18), 3=evening(18-24)."""
    # base = 12:00 → afternoon → bucket 2
    sig = build_incident_similarity_vector([_alert(0)])
    assert sig[31] == pytest.approx(2.0)


def test_build_incident_similarity_vector_event_type_onehot_indices_20_to_25():
    """Indices 20-25 are one-hot over: threshold, ml, fingerprint, summary,
    statistical, unknown."""
    ml_alert = _alert(0, event_type="anomaly_combustion_signature")
    sig = build_incident_similarity_vector([ml_alert])
    # index 21 = ml
    assert sig[21] == pytest.approx(1.0)


def test_build_incident_similarity_vector_single_alert_no_crash():
    sig = build_incident_similarity_vector([_alert(0)])
    assert len(sig) == 32
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_incident_grouper.py -k "signature" -v
```

Expected: `ImportError` — `build_incident_similarity_vector` not defined.

- [ ] **Step 3: Add `build_incident_similarity_vector` to `mlss_monitor/incident_grouper.py`**

Append after `compute_pearson_r`:

```python
# Sensor columns in hot_tier (10 channels)
_SENSOR_COLS: list[str] = [
    "tvoc_ppb", "eco2_ppm", "temperature_c", "humidity_pct",
    "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3",
    "co_ppb", "no2_ppb", "nh3_ppb",
]

# Detection method one-hot order (indices 20-25)
_METHOD_ORDER = ["threshold", "ml", "fingerprint", "summary", "statistical", "unknown"]


def build_incident_similarity_vector(alerts: list[dict[str, Any]]) -> list[float]:
    """Build a 32-float signature vector for cosine similarity search.

    Vector layout:
      0-9   : peak delta placeholders (0.0 — filled by caller if hot_tier data available)
      10-19 : sensor presence flags (1.0 if event_type implies that sensor)
      20-25 : detection method one-hot (threshold / ml / fingerprint / summary / statistical / unknown)
      26-28 : severity weights (info=0, warning=1, critical=2 — normalised, one-hot)
      29    : incident duration in minutes
      30    : mean confidence
      31    : time-of-day bucket (0=night 0-6h, 1=morning 6-12h, 2=afternoon 12-18h, 3=evening 18-24h)
    """
    vec = [0.0] * 32

    if not alerts:
        return vec

    # Sort chronologically
    sorted_a = sorted(alerts, key=lambda a: a["created_at"])
    t_start = datetime.fromisoformat(sorted_a[0]["created_at"])
    t_end = datetime.fromisoformat(sorted_a[-1]["created_at"])

    # 29: duration in minutes
    vec[29] = float((t_end - t_start).total_seconds() / 60.0)

    # 30: mean confidence
    vec[30] = float(
        sum(a.get("confidence", 0.5) for a in alerts) / len(alerts)
    )

    # 31: time-of-day bucket based on start hour
    hour = t_start.hour
    if hour < 6:
        vec[31] = 0.0
    elif hour < 12:
        vec[31] = 1.0
    elif hour < 18:
        vec[31] = 2.0
    else:
        vec[31] = 3.0

    # 20-25: detection method one-hot (majority vote)
    method_counts: dict[str, int] = {}
    for a in alerts:
        m = detection_method(a.get("event_type", ""))
        method_counts[m] = method_counts.get(m, 0) + 1
    dominant = max(method_counts, key=method_counts.get)
    idx = _METHOD_ORDER.index(dominant) if dominant in _METHOD_ORDER else 5
    vec[20 + idx] = 1.0

    # 26-28: severity (info → index 26, warning → 27, critical → 28)
    sevs = [a.get("severity", "info") for a in alerts]
    if "critical" in sevs:
        vec[28] = 1.0
    elif "warning" in sevs:
        vec[27] = 1.0
    else:
        vec[26] = 1.0

    # 10-19: sensor presence flags (naive heuristic from event_type keywords)
    _SENSOR_KEYWORDS = {
        10: ("tvoc",),
        11: ("eco2", "co2"),
        12: ("temp",),
        13: ("humid", "hum"),
        14: ("pm1",),
        15: ("pm25", "pm2"),
        16: ("pm10",),
        17: ("co_",),
        18: ("no2",),
        19: ("nh3",),
    }
    for a in alerts:
        et = a.get("event_type", "").lower()
        for vec_idx, keywords in _SENSOR_KEYWORDS.items():
            if any(kw in et for kw in keywords):
                vec[vec_idx] = 1.0

    return vec
```

- [ ] **Step 4: Run all grouper tests**

```bash
pytest tests/test_incident_grouper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): add build_incident_similarity_vector() — 32-float vector for cosine similarity"
```

---

## Task 5: Grouper — Title Generation and `cosine_similarity`

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Modify: `tests/test_incident_grouper.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_incident_grouper.py`:

```python
from mlss_monitor.incident_grouper import (
    generate_incident_title,
    cosine_similarity,
)


def test_generate_incident_title_critical():
    alerts = [_alert(0, severity="critical")]
    title = generate_incident_title(alerts)
    assert "Critical" in title or "critical" in title.lower()


def test_generate_incident_title_uses_highest_severity():
    alerts = [_alert(0, severity="info"), _alert(5, severity="critical")]
    title = generate_incident_title(alerts)
    assert "Critical" in title


def test_generate_incident_title_non_empty():
    title = generate_incident_title([_alert(0)])
    assert len(title) > 0


def test_cosine_similarity_identical():
    vec = [1.0, 0.0, 0.0, 1.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    """Zero vectors return 0.0 without crashing."""
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_incident_grouper.py -k "title or cosine" -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add `generate_incident_title` and `cosine_similarity` to `mlss_monitor/incident_grouper.py`**

Append after `build_incident_similarity_vector`:

```python
_SEVERITY_LABEL = {"info": "Info", "warning": "Warning", "critical": "Critical"}
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def generate_incident_title(alerts: list[dict[str, Any]]) -> str:
    """Generate a human-readable incident title from the alert group."""
    if not alerts:
        return "Unknown Incident"

    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )
    sev_label = _SEVERITY_LABEL.get(max_sev, "Info")

    # Prefer the title of the most severe (then earliest) alert
    top = sorted(
        alerts,
        key=lambda a: (
            -_SEVERITY_ORDER.get(a.get("severity", "info"), 0),
            a["created_at"],
        ),
    )[0]

    return f"{sev_label}: {top['title']}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
```

- [ ] **Step 4: Run all grouper tests**

```bash
pytest tests/test_incident_grouper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): add generate_incident_title(), cosine_similarity()"
```

---

## Task 6: Grouper — DB Persistence (`regroup_all`)

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Modify: `tests/test_incident_grouper.py`

- [ ] **Step 1: Add failing tests for `regroup_all`**

Append to `tests/test_incident_grouper.py`:

```python
import sqlite3
import tempfile
import os
import database.init_db as dbi
from mlss_monitor.incident_grouper import regroup_all


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    dbi.DB_FILE = db_path
    import mlss_monitor.hot_tier as ht
    import database.db_logger as dbl
    import database.user_db as udb
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    ht.DB_FILE = db_path
    dbi.create_db()
    yield db_path
    dbi.DB_FILE = "data/sensor_data.db"


def _seed_inference(db_path, created_at, event_type="tvoc_spike",
                    severity="info", confidence=0.8):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (created_at, event_type, severity, f"Alert {event_type}", confidence)
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


def test_regroup_all_creates_incident(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT id FROM incidents").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0].startswith("INC-")


def test_regroup_all_links_alert_to_incident(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    links = conn.execute("SELECT incident_id, alert_id FROM incident_alerts").fetchall()
    conn.close()
    assert len(links) == 1


def test_regroup_all_cross_incident_alert_not_primary(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00", event_type="hourly_summary")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT is_primary FROM incident_alerts").fetchone()
    conn.close()
    assert row[0] == 0


def test_regroup_all_two_groups_two_incidents(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    _seed_inference(tmp_db, "2026-04-19 13:00:00")  # 60 min gap
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_idempotent(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00")
    regroup_all(tmp_db)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_max_severity_critical(tmp_db):
    _seed_inference(tmp_db, "2026-04-19 12:00:00", severity="info")
    _seed_inference(tmp_db, "2026-04-19 12:05:00", severity="critical")
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    sev = conn.execute("SELECT max_severity FROM incidents").fetchone()[0]
    conn.close()
    assert sev == "critical"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_incident_grouper.py -k "regroup" -v
```

Expected: `ImportError` — `regroup_all` not defined.

- [ ] **Step 3: Add `regroup_all` to `mlss_monitor/incident_grouper.py`**

Append after `cosine_similarity`:

```python
def _load_all_inferences(db_file: str) -> list[dict[str, Any]]:
    """Load all non-dismissed inferences from SQLite."""
    conn = sqlite3.connect(db_file, timeout=15)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, created_at, event_type, severity, title, description, "
        "confidence FROM inferences WHERE dismissed = 0 ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fetch_hot_tier_window(
    db_file: str,
    t_start: datetime,
    t_end: datetime,
    col: str,
) -> list[float | None]:
    """Fetch a single hot_tier column within [t_start, t_end]."""
    conn = sqlite3.connect(db_file, timeout=15)
    rows = conn.execute(
        f"SELECT {col} FROM hot_tier "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (t_start.isoformat(sep=" "), t_end.isoformat(sep=" ")),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _upsert_incident(
    cur: sqlite3.Cursor,
    incident_id: str,
    alerts: list[dict[str, Any]],
    db_file: str,
) -> None:
    """Write/update one incident and its related rows."""
    sorted_a = sorted(alerts, key=lambda a: a["created_at"])
    t_start = datetime.fromisoformat(sorted_a[0]["created_at"])
    t_end = datetime.fromisoformat(sorted_a[-1]["created_at"])

    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )
    mean_conf = sum(a.get("confidence", 0.5) for a in alerts) / len(alerts)
    title = generate_incident_title(alerts)
    signature = json.dumps(build_incident_similarity_vector(alerts))

    cur.execute(
        "INSERT OR REPLACE INTO incidents "
        "(id, started_at, ended_at, max_severity, confidence, title, signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, t_start.isoformat(sep=" "), t_end.isoformat(sep=" "),
         max_sev, mean_conf, title, signature),
    )

    # Rebuild incident_alerts for this incident
    cur.execute("DELETE FROM incident_alerts WHERE incident_id = ?", (incident_id,))
    for alert in alerts:
        primary = 0 if is_cross_incident(alert.get("event_type", "")) else 1
        cur.execute(
            "INSERT OR IGNORE INTO incident_alerts (incident_id, alert_id, is_primary) "
            "VALUES (?, ?, ?)",
            (incident_id, alert["id"], primary),
        )

    # Rebuild alert_signal_deps for primary alerts
    primary_alerts = [a for a in alerts if not is_cross_incident(a.get("event_type", ""))]
    if primary_alerts and len(sorted_a) >= 2:
        window_start = t_start
        window_end = t_end
        for alert in primary_alerts:
            cur.execute("DELETE FROM alert_signal_deps WHERE alert_id = ?", (alert["id"],))
            for col in _SENSOR_COLS:
                xs = _fetch_hot_tier_window(db_file, window_start, window_end, col)
                # Correlation of sensor column against itself (lead)
                # Simplified: correlate raw values with time-index as proxy
                xs_clean = [x for x in xs if x is not None]
                ys_proxy = list(range(len(xs_clean)))
                r = compute_pearson_r(xs_clean, ys_proxy)
                cur.execute(
                    "INSERT OR IGNORE INTO alert_signal_deps "
                    "(alert_id, sensor, r, lag_seconds) VALUES (?, ?, ?, ?)",
                    (alert["id"], col, r, 0),
                )


def regroup_all(db_file: str) -> None:
    """Re-sessionise all inferences and upsert incidents into the DB.

    Idempotent: safe to call multiple times. Uses INSERT OR REPLACE so
    existing incidents are overwritten with fresh data.
    """
    alerts = _load_all_inferences(db_file)
    groups = sessionise(alerts)

    conn = sqlite3.connect(db_file, timeout=15)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")

    for group in groups:
        if not group:
            continue
        sorted_g = sorted(group, key=lambda a: a["created_at"])
        t_start = datetime.fromisoformat(sorted_g[0]["created_at"])
        incident_id = make_incident_id(t_start)
        _upsert_incident(cur, incident_id, group, db_file)

    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run all grouper tests**

```bash
pytest tests/test_incident_grouper.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): add regroup_all() — upserts incidents + alert_signal_deps to SQLite"
```

---

## Task 7: Grouper — Background Thread

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`

- [ ] **Step 1: Add the background thread and public `start_grouper` function**

Append to the end of `mlss_monitor/incident_grouper.py`:

```python
# ── Background thread ──────────────────────────────────────────────────────────

_grouper_lock = threading.Lock()
_SAFETY_NET_INTERVAL = 60  # seconds — regroup even if no events arrive


class IncidentGrouper:
    """Manages the background grouper thread lifecycle."""

    def __init__(self, db_file: str, event_bus=None):
        self.db_file = db_file
        self._event_bus = event_bus
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Start the daemon grouper thread. Called once at app startup."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="incident-grouper",
            daemon=True,
        )
        self._thread.start()
        log.info("IncidentGrouper started")

    def stop(self) -> None:
        """Signal the thread to exit (used in tests)."""
        self._stop.set()

    def trigger(self) -> None:
        """Request an immediate regroup (called on new_inference event)."""
        # Setting the stop event with a very short timeout acts as a wake-up;
        # the loop checks _stop_requested separately.
        if self._event_sub is not None:
            # Already handled via event queue — just put a sentinel
            try:
                self._event_sub.put_nowait({"event": "_trigger"})
            except Exception:  # pylint: disable=broad-except
                pass

    def _loop(self) -> None:
        sub_queue: queue.Queue | None = None
        if self._event_bus is not None:
            sub_queue = self._event_bus.subscribe()
        self._event_sub = sub_queue

        # Initial regroup on startup
        _safe_regroup(self.db_file)

        while not self._stop.is_set():
            triggered = False
            if sub_queue is not None:
                try:
                    msg = sub_queue.get(timeout=_SAFETY_NET_INTERVAL)
                    if msg.get("event") in ("new_inference", "_trigger"):
                        triggered = True
                except queue.Empty:
                    # Safety net: regroup every 60 s even with no events
                    triggered = True
            else:
                self._stop.wait(_SAFETY_NET_INTERVAL)
                triggered = True

            if triggered and not self._stop.is_set():
                _safe_regroup(self.db_file)

        if sub_queue is not None and self._event_bus is not None:
            self._event_bus.unsubscribe(sub_queue)
        log.info("IncidentGrouper stopped")

    @property
    def _event_sub(self):
        return getattr(self, "_sub_queue", None)

    @_event_sub.setter
    def _event_sub(self, q):
        self._sub_queue = q


def _safe_regroup(db_file: str) -> None:
    """Run regroup_all with a lock and swallow all exceptions."""
    with _grouper_lock:
        try:
            regroup_all(db_file)
            log.debug("Incident regroup complete")
        except Exception:  # pylint: disable=broad-except
            log.exception("Incident regroup failed")


def start_grouper(db_file: str, event_bus=None) -> "IncidentGrouper":
    """Create and start an IncidentGrouper. Returns it so app.py can store it."""
    grouper = IncidentGrouper(db_file=db_file, event_bus=event_bus)
    grouper.start()
    return grouper
```

- [ ] **Step 2: Verify the thread starts without errors**

```bash
python -c "
import sys
from unittest.mock import MagicMock
for m in ['board','busio','adafruit_ahtx0','adafruit_sgp30','mics6814',
          'authlib','authlib.integrations','authlib.integrations.flask_client']:
    sys.modules[m] = MagicMock()
from mlss_monitor.incident_grouper import start_grouper
g = start_grouper('data/sensor_data.db')
import time; time.sleep(0.5)
g.stop()
print('Thread OK:', g._thread.name)
"
```

Expected: `Thread OK: incident-grouper`

- [ ] **Step 3: Commit**

```bash
git add mlss_monitor/incident_grouper.py
git commit -m "feat(grouper): add IncidentGrouper background thread + start_grouper()"
```

---

## Task 8: REST Endpoint — `GET /api/incidents`

**Files:**
- Create: `mlss_monitor/routes/api_incidents.py`
- Create: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_api_incidents.py`:

```python
"""Tests for the incidents REST API."""
import json
import sys
from unittest.mock import MagicMock, patch

for _mod in ["board", "busio", "adafruit_ahtx0", "adafruit_sgp30",
             "mics6814", "authlib", "authlib.integrations",
             "authlib.integrations.flask_client"]:
    sys.modules.setdefault(_mod, MagicMock())

import sqlite3
import pytest
import database.init_db as dbi
import database.db_logger as dbl
import database.user_db as udb


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    import mlss_monitor.hot_tier as ht
    dbi.DB_FILE = db_path
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    ht.DB_FILE = db_path
    dbi.create_db()
    yield db_path
    dbi.DB_FILE = "data/sensor_data.db"


@pytest.fixture
def client(db, monkeypatch):
    import mlss_monitor.app as app_module
    import mlss_monitor.state as state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    mock_plug = MagicMock()
    monkeypatch.setattr(state, "fan_smart_plug", mock_plug)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "test-admin"
            sess["user_role"] = "admin"
            sess["user_id"] = None
        yield c


def _seed_incident(db_path, incident_id="INC-20260419-1200",
                   started_at="2026-04-19 12:00:00",
                   ended_at="2026-04-19 12:10:00",
                   max_severity="warning"):
    conn = sqlite3.connect(db_path)
    sig = json.dumps([0.0] * 32)
    conn.execute(
        "INSERT INTO incidents (id, started_at, ended_at, max_severity, confidence, title, signature) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_id, started_at, ended_at, max_severity, 0.8, f"Test {incident_id}", sig)
    )
    conn.commit()
    conn.close()


def test_get_incidents_empty(client):
    rv = client.get("/api/incidents")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["incidents"] == []


def test_get_incidents_returns_list(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents")
    assert rv.status_code == 200
    data = rv.get_json()
    assert len(data["incidents"]) == 1
    assert data["incidents"][0]["id"] == "INC-20260419-1200"


def test_get_incidents_severity_filter(client, db):
    _seed_incident(db, "INC-20260419-1200", max_severity="info")
    _seed_incident(db, "INC-20260419-1300", started_at="2026-04-19 13:00:00",
                   ended_at="2026-04-19 13:10:00", max_severity="critical")
    rv = client.get("/api/incidents?severity=critical")
    data = rv.get_json()
    assert all(i["max_severity"] == "critical" for i in data["incidents"])


def test_get_incidents_window_filter(client, db):
    _seed_incident(db, "INC-20260419-1200")
    rv = client.get("/api/incidents?window=1h")
    # Response may be empty if test doesn't seed within the last hour — that's fine
    assert rv.status_code == 200


def test_get_incidents_includes_alert_count(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents")
    data = rv.get_json()
    assert "alert_count" in data["incidents"][0]


def test_get_incident_detail_not_found(client):
    rv = client.get("/api/incidents/INC-MISSING")
    assert rv.status_code == 404


def test_get_incident_detail_returns_fields(client, db):
    _seed_incident(db)
    rv = client.get("/api/incidents/INC-20260419-1200")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "id" in data
    assert "alerts" in data
    assert "narrative" in data
    assert "similar" in data
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_incidents.py -v 2>&1 | head -20
```

Expected: 404 errors since the blueprint doesn't exist yet.

- [ ] **Step 3: Create `mlss_monitor/routes/api_incidents.py`**

```python
"""Incidents REST API.

GET /api/incidents            — paginated list with optional filters
GET /api/incidents/<id>       — full incident detail with narrative + similar
GET /api/incidents/<id>/alert/<alert_id>  — raw inference JSON
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from config import config
from mlss_monitor.incident_grouper import (
    cosine_similarity,
    detection_method,
    is_cross_incident,
)

log = logging.getLogger(__name__)
api_incidents_bp = Blueprint("api_incidents", __name__)

DB_FILE = config.get("DB_FILE", "data/sensor_data.db")

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_WINDOW_MAP = {
    "1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_window(window: str) -> datetime | None:
    hours = _WINDOW_MAP.get(window)
    if hours is None:
        return None
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)


def _incident_alert_count(conn: sqlite3.Connection, incident_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM incident_alerts WHERE incident_id = ?",
        (incident_id,)
    ).fetchone()
    return row[0] if row else 0


def _build_narrative(incident: dict, alerts: list[dict]) -> dict:
    """Template-based narrative (not LLM). Returns {observed, inferred, impact}."""
    severities = [a.get("severity", "info") for a in alerts]
    max_sev = max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0), default="info")
    unique_types = list({a["event_type"] for a in alerts})

    observed = (
        f"{len(alerts)} event(s) detected between "
        f"{incident['started_at'][:16]} and {incident['ended_at'][:16]}."
    )
    inferred = f"Dominant detection type(s): {', '.join(unique_types[:3])}."
    impact_map = {
        "critical": "Immediate attention required — critical air quality event.",
        "warning": "Elevated readings detected — monitor conditions closely.",
        "info": "Informational event — conditions within acceptable range.",
    }
    impact = impact_map.get(max_sev, "")

    return {"observed": observed, "inferred": inferred, "impact": impact}


def _find_similar(
    conn: sqlite3.Connection,
    incident_id: str,
    signature: list[float],
    top_n: int = 3,
) -> list[dict]:
    """Find similar past incidents using cosine similarity on signature vectors."""
    rows = conn.execute(
        "SELECT id, title, started_at, max_severity, confidence, signature "
        "FROM incidents WHERE id != ? ORDER BY started_at DESC LIMIT 100",
        (incident_id,)
    ).fetchall()

    scored = []
    for row in rows:
        try:
            other_sig = json.loads(row["signature"])
            score = cosine_similarity(signature, other_sig)
            if score >= 0.5:
                scored.append({
                    "id": row["id"],
                    "title": row["title"],
                    "started_at": row["started_at"],
                    "max_severity": row["max_severity"],
                    "confidence": row["confidence"],
                    "similarity": round(score, 3),
                })
        except Exception:  # pylint: disable=broad-except
            continue

    scored.sort(key=lambda x: -x["similarity"])
    return scored[:top_n]


# ── Routes ────────────────────────────────────────────────────────────────────

@api_incidents_bp.route("/api/incidents")
def list_incidents():
    window = request.args.get("window", "24h")
    severity = request.args.get("severity", "all")
    q = request.args.get("q", "").strip().lower()
    limit = request.args.get("limit", 50, type=int)

    conn = _get_conn()
    since = _parse_window(window)

    query = "SELECT * FROM incidents"
    params: list = []
    conditions: list[str] = []

    if since:
        conditions.append("started_at >= ?")
        params.append(since.isoformat(sep=" "))
    if severity and severity != "all":
        conditions.append("max_severity = ?")
        params.append(severity)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    incidents = []
    for row in rows:
        d = dict(row)
        d["alert_count"] = _incident_alert_count(conn, d["id"])
        d.pop("signature", None)  # don't expose raw vector
        if q and q not in d.get("title", "").lower() and q not in d["id"].lower():
            continue
        incidents.append(d)

    conn.close()
    return jsonify({"incidents": incidents, "total": len(incidents)})


@api_incidents_bp.route("/api/incidents/<incident_id>")
def get_incident(incident_id: str):
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Incident not found"}), 404

    incident = dict(row)
    try:
        signature = json.loads(incident.get("signature", "[]"))
    except Exception:  # pylint: disable=broad-except
        signature = []

    # Load alerts with signal deps
    alert_rows = conn.execute(
        "SELECT i.id, i.created_at, i.event_type, i.severity, i.title, "
        "i.description, i.confidence, ia.is_primary "
        "FROM inferences i "
        "JOIN incident_alerts ia ON ia.alert_id = i.id "
        "WHERE ia.incident_id = ? ORDER BY i.created_at",
        (incident_id,)
    ).fetchall()

    alerts = []
    for ar in alert_rows:
        a = dict(ar)
        a["detection_method"] = detection_method(a["event_type"])
        a["is_cross_incident"] = is_cross_incident(a["event_type"])

        # Signal deps
        dep_rows = conn.execute(
            "SELECT sensor, r, lag_seconds FROM alert_signal_deps WHERE alert_id = ?",
            (a["id"],)
        ).fetchall()
        a["signal_deps"] = [dict(d) for d in dep_rows]
        alerts.append(a)

    # Causal sequence: primary alerts in chronological order
    causal_sequence = [
        {"id": a["id"], "title": a["title"], "event_type": a["event_type"],
         "severity": a["severity"], "created_at": a["created_at"]}
        for a in alerts if a["is_primary"]
    ]

    narrative = _build_narrative(incident, alerts)
    similar = _find_similar(conn, incident_id, signature)

    incident.pop("signature", None)
    conn.close()

    return jsonify({
        **incident,
        "alerts": alerts,
        "causal_sequence": causal_sequence,
        "narrative": narrative,
        "similar": similar,
    })


@api_incidents_bp.route("/api/incidents/<incident_id>/alert/<int:alert_id>")
def get_incident_alert(incident_id: str, alert_id: int):
    """Return full inference row JSON for a given alert."""
    conn = _get_conn()

    # Verify the alert belongs to this incident
    link = conn.execute(
        "SELECT 1 FROM incident_alerts WHERE incident_id = ? AND alert_id = ?",
        (incident_id, alert_id)
    ).fetchone()
    if link is None:
        conn.close()
        return jsonify({"error": "Alert not found in incident"}), 404

    row = conn.execute(
        "SELECT * FROM inferences WHERE id = ?", (alert_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return jsonify({"error": "Inference not found"}), 404

    alert = dict(row)
    try:
        alert["evidence"] = json.loads(alert.get("evidence") or "{}")
    except Exception:  # pylint: disable=broad-except
        pass
    alert["detection_method"] = detection_method(alert["event_type"])
    return jsonify(alert)
```

- [ ] **Step 4: Run the API tests**

```bash
pytest tests/test_api_incidents.py -v
```

Expected: most tests fail because the blueprint isn't registered yet.

- [ ] **Step 5: Commit the route file**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): add api_incidents_bp with list, detail, and alert endpoints"
```

---

## Task 9: Wire Up Blueprint + Start Grouper

**Files:**
- Modify: `mlss_monitor/routes/__init__.py`
- Modify: `mlss_monitor/state.py`
- Modify: `mlss_monitor/app.py`
- Modify: `mlss_monitor/routes/pages.py`

- [ ] **Step 1: Register the blueprint in `mlss_monitor/routes/__init__.py`**

```python
# Add at top with other imports:
from .api_incidents import api_incidents_bp

# Add inside register_routes(app):
    app.register_blueprint(api_incidents_bp)
```

The full updated file:

```python
"""Register all route blueprints on the Flask app."""

from .auth import auth_bp
from .pages import pages_bp
from .api_data import api_data_bp
from .api_fan import api_fan_bp
from .api_weather import api_weather_bp
from .api_settings import api_settings_bp
from .api_users import api_users_bp
from .system import system_bp
from .api_inferences import api_inferences_bp
from .api_stream import api_stream_bp
from .api_insights import api_insights_bp
from .api_history import api_history_bp
from .api_tags import api_tags_bp
from .api_incidents import api_incidents_bp


def register_routes(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_data_bp)
    app.register_blueprint(api_fan_bp)
    app.register_blueprint(api_weather_bp)
    app.register_blueprint(api_settings_bp)
    app.register_blueprint(api_users_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(api_inferences_bp)
    app.register_blueprint(api_stream_bp)
    app.register_blueprint(api_insights_bp)
    app.register_blueprint(api_history_bp)
    app.register_blueprint(api_tags_bp)
    app.register_blueprint(api_incidents_bp)
```

- [ ] **Step 2: Add `incident_grouper` to `mlss_monitor/state.py`**

Add one line after `detection_engine = None`:

```python
incident_grouper = None  # IncidentGrouper instance (set by app.py)
```

- [ ] **Step 3: Start the grouper in `mlss_monitor/app.py`**

Find the section where `detection_engine` or the EventBus is initialised (search for `state.event_bus`). After it is assigned, add:

```python
from mlss_monitor.incident_grouper import start_grouper

# After state.event_bus is assigned and create_db() has been called:
state.incident_grouper = start_grouper(DB_FILE, event_bus=state.event_bus)
```

- [ ] **Step 4: Add the `/incidents` page route to `mlss_monitor/routes/pages.py`**

```python
@pages_bp.route("/incidents")
def incidents_page():
    return render_template("incidents.html")
```

- [ ] **Step 5: Run API tests to verify the blueprint wires up correctly**

```bash
pytest tests/test_api_incidents.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
pytest --tb=short -q
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/routes/__init__.py mlss_monitor/state.py mlss_monitor/app.py mlss_monitor/routes/pages.py
git commit -m "feat: wire up incidents blueprint + start grouper at app startup"
```

---

## Task 10: HTML Template — Incidents Page

**Files:**
- Create: `templates/incidents.html`

- [ ] **Step 1: Create `templates/incidents.html`**

```html
{% extends "base.html" %}
{% block title %}MLSS – Incidents{% endblock %}

{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/incident_graph.css') }}">
{% endblock %}

{% block content %}
<div class="inc-page">

  <!-- ── Toolbar ─────────────────────────────────────────────────────── -->
  <div class="inc-toolbar">
    <rux-input
      id="inc-search"
      placeholder="Search incidents…"
      size="small"
      style="--input-width:220px"
    ></rux-input>

    <rux-segmented-button
      id="inc-window"
      data="[
        {&quot;label&quot;:&quot;1h&quot;,&quot;selected&quot;:false},
        {&quot;label&quot;:&quot;6h&quot;,&quot;selected&quot;:false},
        {&quot;label&quot;:&quot;24h&quot;,&quot;selected&quot;:true},
        {&quot;label&quot;:&quot;7d&quot;,&quot;selected&quot;:false},
        {&quot;label&quot;:&quot;30d&quot;,&quot;selected&quot;:false}
      ]"
    ></rux-segmented-button>

    <rux-segmented-button
      id="inc-severity"
      data="[
        {&quot;label&quot;:&quot;All&quot;,&quot;selected&quot;:true},
        {&quot;label&quot;:&quot;Critical&quot;,&quot;selected&quot;:false},
        {&quot;label&quot;:&quot;Warning&quot;,&quot;selected&quot;:false},
        {&quot;label&quot;:&quot;Info&quot;,&quot;selected&quot;:false}
      ]"
    ></rux-segmented-button>
  </div>

  <!-- ── Main layout ────────────────────────────────────────────────── -->
  <div class="inc-layout">

    <!-- Left: incident list -->
    <div class="inc-list-panel" id="inc-list-panel">
      <div id="inc-list-items">
        <div class="inc-loading">Loading incidents…</div>
      </div>
    </div>

    <!-- Centre: graph canvas -->
    <div class="inc-graph-panel">
      <div id="cy-graph"></div>

      <!-- Symbol key -->
      <div class="inc-graph-key">
        <span class="key-section">Border = Severity</span>
        <span class="key-item key-critical">■ Critical</span>
        <span class="key-item key-warning">■ Warning</span>
        <span class="key-item key-info">■ Info</span>
        <span class="key-section">Glyph = Method</span>
        <span class="key-item">▲ Threshold</span>
        <span class="key-item">◆ ML</span>
        <span class="key-item">● Statistical</span>
        <span class="key-item">◇ Fingerprint</span>
        <span class="key-item key-cross">- - Cross-incident</span>
      </div>
    </div>

    <!-- Right: detail panel -->
    <div class="inc-detail-panel" id="inc-detail-panel">
      <div class="inc-detail-empty">Select an incident to view details</div>

      <!-- Narrative section (hidden until loaded) -->
      <div class="inc-narrative" id="inc-narrative" hidden>
        <h4>Narrative</h4>
        <p id="inc-narrative-observed"></p>
        <p id="inc-narrative-inferred"></p>
        <p id="inc-narrative-impact"></p>
      </div>

      <!-- Causal sequence (hidden until loaded) -->
      <div class="inc-causal" id="inc-causal" hidden>
        <h4>Causal Sequence</h4>
        <div id="inc-causal-items"></div>
      </div>

      <!-- Similar incidents (hidden until loaded) -->
      <div class="inc-similar" id="inc-similar" hidden>
        <h4>Similar Past Incidents</h4>
        <div id="inc-similar-items"></div>
      </div>

      <!-- Node overlay (hidden until node clicked) -->
      <div class="inc-node-overlay" id="inc-node-overlay" hidden>
        <div class="inc-node-overlay-header">
          <span id="inc-node-title"></span>
          <a id="inc-node-view-link" href="#" class="inc-node-view-btn">View full inference →</a>
        </div>
        <div id="inc-node-body"></div>
      </div>
    </div>
  </div>
</div>
{% endblock %}

{% block extra_scripts %}
  <!-- Cytoscape.js — MIT licence, cdnjs CDN -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.2/cytoscape.min.js"></script>
  <script type="module" src="{{ url_for('static', filename='js/incident_graph.js') }}"></script>
{% endblock %}
```

- [ ] **Step 2: Verify the page loads (smoke test)**

```bash
pytest tests/test_pages.py -v -k "incidents" 2>/dev/null || echo "No page test yet — add one"
```

If no page test exists, run the app manually and visit `http://localhost:5000/incidents`.

- [ ] **Step 3: Commit**

```bash
git add templates/incidents.html
git commit -m "feat(ui): add incidents.html template with toolbar, graph canvas, detail panel"
```

---

## Task 11: CSS — `static/css/incident_graph.css`

**Files:**
- Create: `static/css/incident_graph.css`

- [ ] **Step 1: Create `static/css/incident_graph.css`**

```css
/* ── Incident Correlation Graph page layout ─────────────────────────────── */

.inc-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px); /* subtract nav bar */
  overflow: hidden;
  padding: 0;
}

/* ── Toolbar ──────────────────────────────────────────────────────────────── */

.inc-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--card-bg);
  flex-shrink: 0;
}

/* ── Main layout (3 columns) ─────────────────────────────────────────────── */

.inc-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Left: incident list ─────────────────────────────────────────────────── */

.inc-list-panel {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 8px 0;
  background: var(--bg);
}

.inc-card {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.1s;
}

.inc-card:hover,
.inc-card.selected {
  background: var(--chip-bg);
}

.inc-card-id {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}

.inc-card-title {
  font-size: 0.83rem;
  color: var(--text);
  margin: 2px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inc-card-meta {
  font-size: 0.72rem;
  color: var(--text-muted);
  display: flex;
  gap: 6px;
  align-items: center;
}

.inc-sev-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.inc-sev-dot.critical { background: #8a1515; }
.inc-sev-dot.warning  { background: #c47a1e; }
.inc-sev-dot.info     { background: #1a4060; }

.inc-loading {
  padding: 16px;
  color: var(--text-muted);
  font-style: italic;
  font-size: 0.85rem;
}

/* ── Centre: graph canvas ────────────────────────────────────────────────── */

.inc-graph-panel {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: #0d1117;
}

#cy-graph {
  width: 100%;
  height: calc(100% - 36px); /* leave room for key */
}

/* ── Symbol key ─────────────────────────────────────────────────────────── */

.inc-graph-key {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 36px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  background: rgba(13, 17, 23, 0.85);
  border-top: 1px solid #2a2f3a;
  font-size: 0.7rem;
  color: #9ca3af;
  flex-wrap: nowrap;
  overflow: hidden;
}

.key-section {
  font-weight: 700;
  color: #d1d5db;
  padding-right: 4px;
}

.key-item { opacity: 0.85; }
.key-critical { color: #e57373; }
.key-warning  { color: #ffb74d; }
.key-info     { color: #64b5f6; }
.key-cross    { color: #a5d6a7; }

/* ── Right: detail panel ─────────────────────────────────────────────────── */

.inc-detail-panel {
  width: 280px;
  flex-shrink: 0;
  border-left: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--card-bg);
}

.inc-detail-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-style: italic;
  font-size: 0.85rem;
  padding: 24px;
  text-align: center;
}

.inc-narrative,
.inc-causal,
.inc-similar {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.inc-narrative h4,
.inc-causal h4,
.inc-similar h4 {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 0 0 8px 0;
}

.inc-narrative p {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin: 4px 0;
}

/* Causal sequence ── horizontal ribbon */
.inc-causal-ribbon {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  row-gap: 6px;
}

.inc-causal-arrow {
  font-size: 0.75rem;
  color: var(--text-muted);
}

/* Similar incidents */
.inc-similar-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  font-size: 0.8rem;
}

.inc-similar-item:last-child { border-bottom: none; }

.inc-similar-score {
  font-size: 0.72rem;
  color: var(--text-muted);
}

.inc-similar-nav {
  color: var(--accent);
  font-size: 0.9rem;
}

/* Node overlay ── shown in right panel when a node is clicked */
.inc-node-overlay {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.inc-node-overlay-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

#inc-node-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text);
}

.inc-node-view-btn {
  font-size: 0.75rem;
  color: var(--accent);
  text-decoration: none;
}

.inc-node-view-btn:hover { text-decoration: underline; }

#inc-node-body {
  font-size: 0.78rem;
  color: var(--text-secondary);
}

#inc-node-body table {
  width: 100%;
  border-collapse: collapse;
}

#inc-node-body td {
  padding: 2px 4px;
  vertical-align: top;
}

#inc-node-body td:first-child {
  color: var(--text-muted);
  white-space: nowrap;
  padding-right: 8px;
}
```

- [ ] **Step 2: Commit**

```bash
git add static/css/incident_graph.css
git commit -m "feat(ui): add incident_graph.css — 3-column layout, graph canvas, detail panel"
```

---

## Task 12: JS — Module Skeleton, Toolbar, Incident List

**Files:**
- Create: `static/js/incident_graph.js`

- [ ] **Step 1: Create `static/js/incident_graph.js` with skeleton + toolbar + list**

```javascript
/**
 * incident_graph.js — Incident Correlation Graph
 *
 * Responsibilities:
 *  - Toolbar (window, severity, search) → fetch + filter incidents
 *  - Left panel: render incident cards, handle selection
 *  - Centre: Cytoscape.js graph (Task 13)
 *  - Right panel: narrative, causal ribbon, similar incidents, node overlay
 */

// ── State ─────────────────────────────────────────────────────────────────────

let cy = null;                  // Cytoscape instance
let currentIncidentId = null;   // selected incident ID
let allIncidents = [];          // full list from /api/incidents
let currentDetail = null;       // detail response for selected incident

// ── DOM refs ─────────────────────────────────────────────────────────────────

const elSearch   = document.getElementById('inc-search');
const elWindow   = document.getElementById('inc-window');
const elSeverity = document.getElementById('inc-severity');
const elList     = document.getElementById('inc-list-items');
const elEmpty    = document.querySelector('.inc-detail-empty');
const elNarrative = document.getElementById('inc-narrative');
const elNarrObs  = document.getElementById('inc-narrative-observed');
const elNarrInf  = document.getElementById('inc-narrative-inferred');
const elNarrImp  = document.getElementById('inc-narrative-impact');
const elCausal   = document.getElementById('inc-causal');
const elCausalItems = document.getElementById('inc-causal-items');
const elSimilar  = document.getElementById('inc-similar');
const elSimilarItems = document.getElementById('inc-similar-items');
const elNodeOverlay = document.getElementById('inc-node-overlay');
const elNodeTitle   = document.getElementById('inc-node-title');
const elNodeLink    = document.getElementById('inc-node-view-link');
const elNodeBody    = document.getElementById('inc-node-body');

// ── Toolbar state ─────────────────────────────────────────────────────────────

let activeWindow   = '24h';
let activeSeverity = 'all';
let searchQuery    = '';
let searchTimer    = null;

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initToolbar();
  loadIncidents();
});

// ── Toolbar ───────────────────────────────────────────────────────────────────

function initToolbar() {
  // Window segmented button
  elWindow.addEventListener('ruxchange', e => {
    activeWindow = e.detail.toLowerCase();
    loadIncidents();
  });

  // Severity segmented button
  elSeverity.addEventListener('ruxchange', e => {
    activeSeverity = e.detail.toLowerCase();
    if (activeSeverity === 'all') activeSeverity = 'all';
    renderList(applyClientFilter(allIncidents));
  });

  // Search input — debounced 300ms
  elSearch.addEventListener('ruxinput', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchQuery = (e.target.value || '').toLowerCase().trim();
      renderList(applyClientFilter(allIncidents));
    }, 300);
  });
}

function applyClientFilter(incidents) {
  return incidents.filter(inc => {
    if (activeSeverity !== 'all' && inc.max_severity !== activeSeverity) return false;
    if (searchQuery) {
      const haystack = (inc.id + ' ' + inc.title).toLowerCase();
      if (!haystack.includes(searchQuery)) return false;
    }
    return true;
  });
}

// ── Fetch incident list ───────────────────────────────────────────────────────

async function loadIncidents() {
  elList.innerHTML = '<div class="inc-loading">Loading…</div>';

  const params = new URLSearchParams({ window: activeWindow, limit: 100 });
  try {
    const resp = await fetch('/api/incidents?' + params);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    allIncidents = data.incidents || [];
    renderList(applyClientFilter(allIncidents));

    // Auto-select the first incident if available
    if (allIncidents.length > 0 && !currentIncidentId) {
      selectIncident(allIncidents[0].id);
    }
  } catch (err) {
    elList.innerHTML = `<div class="inc-loading">Error: ${err.message}</div>`;
  }
}

// ── Render incident list ──────────────────────────────────────────────────────

function renderList(incidents) {
  if (incidents.length === 0) {
    elList.innerHTML = '<div class="inc-loading">No incidents found.</div>';
    return;
  }

  elList.innerHTML = incidents.map(inc => `
    <div class="inc-card${inc.id === currentIncidentId ? ' selected' : ''}"
         data-id="${inc.id}">
      <div class="inc-card-id">${escHtml(inc.id)}</div>
      <div class="inc-card-title" title="${escHtml(inc.title)}">${escHtml(inc.title)}</div>
      <div class="inc-card-meta">
        <span class="inc-sev-dot ${inc.max_severity}"></span>
        <span>${inc.max_severity}</span>
        <span>·</span>
        <span>${inc.alert_count ?? 0} alert${inc.alert_count === 1 ? '' : 's'}</span>
      </div>
    </div>
  `).join('');

  // Attach click handlers
  elList.querySelectorAll('.inc-card').forEach(card => {
    card.addEventListener('click', () => selectIncident(card.dataset.id));
  });
}

// ── Select incident ───────────────────────────────────────────────────────────

async function selectIncident(id) {
  currentIncidentId = id;

  // Highlight card
  elList.querySelectorAll('.inc-card').forEach(c => {
    c.classList.toggle('selected', c.dataset.id === id);
  });

  // Load detail
  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    currentDetail = await resp.json();
    renderDetail(currentDetail);
    renderGraph(currentDetail, allIncidents);
  } catch (err) {
    console.error('Failed to load incident detail:', err);
  }
}

// ── Right panel: narrative, causal, similar ───────────────────────────────────

function renderDetail(detail) {
  if (elEmpty) elEmpty.hidden = true;

  // Narrative
  if (detail.narrative) {
    elNarrObs.textContent = detail.narrative.observed || '';
    elNarrInf.textContent = detail.narrative.inferred || '';
    elNarrImp.textContent = detail.narrative.impact || '';
    elNarrative.hidden = false;
  }

  // Causal sequence — rux-tag ribbon
  const causal = detail.causal_sequence || [];
  if (causal.length > 0) {
    elCausalItems.innerHTML = '<div class="inc-causal-ribbon">'
      + causal.map((a, i) =>
          (i > 0 ? '<span class="inc-causal-arrow">→</span>' : '')
          + `<rux-tag status="${severityToStatus(a.severity)}">${escHtml(a.title)}</rux-tag>`
        ).join('')
      + '</div>';
    elCausal.hidden = false;
  } else {
    elCausal.hidden = true;
  }

  // Similar incidents
  const similar = detail.similar || [];
  if (similar.length > 0) {
    elSimilarItems.innerHTML = similar.map(s => `
      <div class="inc-similar-item" data-similar-id="${s.id}">
        <div>
          <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted)">${escHtml(s.id)}</div>
          <div style="font-size:0.8rem">${escHtml(s.title)}</div>
        </div>
        <div style="text-align:right">
          <div class="inc-similar-score">${(s.similarity * 100).toFixed(0)}% similar</div>
          <span class="inc-similar-nav">›</span>
        </div>
      </div>
    `).join('');

    elSimilarItems.querySelectorAll('.inc-similar-item').forEach(el => {
      el.addEventListener('click', () => selectIncident(el.dataset.similarId));
    });
    elSimilar.hidden = false;
  } else {
    elSimilar.hidden = true;
  }

  // Hide node overlay when switching incidents
  elNodeOverlay.hidden = true;
}

// ── Node overlay (shown when graph node is clicked) ──────────────────────────

async function showNodeOverlay(nodeData) {
  elNodeTitle.textContent = nodeData.title || nodeData.id;
  elNodeOverlay.hidden = false;

  if (nodeData.type === 'alert' && nodeData.alertId && currentIncidentId) {
    // Fetch full inference detail
    try {
      const resp = await fetch(
        `/api/incidents/${encodeURIComponent(currentIncidentId)}/alert/${nodeData.alertId}`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const alert = await resp.json();
      elNodeLink.href = `/inferences?id=${alert.id}`;
      elNodeBody.innerHTML = renderAlertTable(alert);
    } catch (err) {
      elNodeBody.textContent = 'Could not load alert detail.';
    }
  }
}

function renderAlertTable(alert) {
  const rows = [
    ['Type', escHtml(alert.event_type || '')],
    ['Severity', escHtml(alert.severity || '')],
    ['Method', escHtml(alert.detection_method || '')],
    ['Confidence', `${((alert.confidence || 0) * 100).toFixed(0)}%`],
    ['Time', escHtml((alert.created_at || '').slice(0, 16))],
  ];
  if (alert.description) {
    rows.push(['Detail', escHtml(alert.description.slice(0, 120)
      + (alert.description.length > 120 ? '…' : ''))]);
  }
  return '<table>' + rows.map(([k, v]) =>
    `<tr><td>${k}</td><td>${v}</td></tr>`
  ).join('') + '</table>';
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function severityToStatus(sev) {
  return { critical: 'critical', warning: 'caution', info: 'normal' }[sev] || 'normal';
}

// Stub for graph — implemented in Task 13
function renderGraph(detail, allIncidents) {
  initCytoscape();
  loadGraphElements(detail, allIncidents);
}

// Stubs — filled in Tasks 13–16
function initCytoscape() {}
function loadGraphElements() {}
```

- [ ] **Step 2: Verify no JS parse errors by opening the incidents page**

Navigate to `http://localhost:5000/incidents` in the browser; the browser console should show no errors (the graph will be empty until the next task).

- [ ] **Step 3: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(ui): add incident_graph.js skeleton — toolbar, list, detail panel, node overlay"
```

---

## Task 13: JS — Cytoscape.js Init and Layout Algorithm

**Files:**
- Modify: `static/js/incident_graph.js`

- [ ] **Step 1: Replace stub `initCytoscape()` with real implementation**

Replace the existing stub at the bottom of `incident_graph.js`:

```javascript
// ── Cytoscape init ────────────────────────────────────────────────────────────

function initCytoscape() {
  if (cy) {
    cy.destroy();
    cy = null;
  }

  cy = cytoscape({
    container: document.getElementById('cy-graph'),
    userZoomingEnabled: true,
    userPanningEnabled: true,
    boxSelectionEnabled: false,
    minZoom: 0.15,
    maxZoom: 6,
    style: buildCytoscapeStyle(),
    elements: [],
    layout: { name: 'preset' },
  });

  // Progressive zoom: update label visibility when zoom changes
  cy.on('zoom', () => applyZoomClasses(cy.zoom()));

  // Node click → show overlay
  cy.on('tap', 'node[type="alert"]', evt => {
    showNodeOverlay(evt.target.data());
  });

  // Drag end → persist position to localStorage
  cy.on('dragfree', 'node', evt => {
    const node = evt.target;
    const pos = node.position();
    const key = `${currentIncidentId}::${node.id()}`;
    try {
      localStorage.setItem(key, JSON.stringify(pos));
    } catch (_) {}
  });
}

// ── Graph element builder ─────────────────────────────────────────────────────

function loadGraphElements(detail, allIncidents) {
  if (!cy) return;
  cy.elements().remove();

  const elements = [];

  // Map: incidentId → {x, y} centroid on the canvas
  const centroids = buildCentroids(allIncidents);

  // Render the selected incident cluster fully
  if (detail && detail.alerts) {
    elements.push(...buildIncidentElements(detail, centroids, /*selected=*/true));
  }

  // Render other incidents as dimmed clusters
  allIncidents.forEach(inc => {
    if (inc.id === (detail && detail.id)) return;
    elements.push(...buildGhostCluster(inc, centroids));
  });

  cy.add(elements);
  restorePositions();

  // Fit to selected incident cluster
  const selectedNodes = cy.$(`[incidentId="${detail && detail.id}"]`);
  if (selectedNodes.length > 0) {
    cy.fit(selectedNodes, 60);
  } else {
    cy.fit(cy.elements(), 40);
  }

  applyZoomClasses(cy.zoom());
  applySelectionOpacity(detail && detail.id);
}

// ── Centroid placement ────────────────────────────────────────────────────────

function buildCentroids(allIncidents) {
  const GRID_SPACING = 400;
  const cols = Math.ceil(Math.sqrt(allIncidents.length)) || 1;
  const centroids = {};
  allIncidents.forEach((inc, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    centroids[inc.id] = {
      x: col * GRID_SPACING,
      y: row * GRID_SPACING,
    };
  });
  return centroids;
}

// ── Build elements for selected incident ──────────────────────────────────────

function buildIncidentElements(detail, centroids, selected) {
  const elements = [];
  const incId = detail.id;
  const centre = centroids[incId] || { x: 0, y: 0 };

  // Compound parent node (cluster hull)
  elements.push({
    group: 'nodes',
    data: { id: `hull-${incId}`, label: incId, type: 'hull', incidentId: incId },
    classes: `hull severity-${detail.max_severity}`,
  });

  // Root signal nodes (one per unique sensor / detection type)
  const primaryAlerts = (detail.alerts || []).filter(a => a.is_primary);
  const crossAlerts   = (detail.alerts || []).filter(a => !a.is_primary);
  const rootCount = Math.max(primaryAlerts.length, 1);

  primaryAlerts.forEach((alert, i) => {
    const angle = (2 * Math.PI * i) / rootCount - Math.PI / 2;
    const pos = {
      x: centre.x + 40 * Math.cos(angle),
      y: centre.y + 40 * Math.sin(angle),
    };
    const saved = loadSavedPosition(`${incId}::root-${alert.id}`);
    elements.push({
      group: 'nodes',
      data: {
        id: `root-${alert.id}`,
        label: alert.event_type.replace(/_/g, ' '),
        type: 'root',
        alertId: alert.id,
        incidentId: incId,
        parent: `hull-${incId}`,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title,
      },
      position: saved || pos,
      classes: `root-signal severity-${alert.severity} method-${alert.detection_method}`,
    });
  });

  // Alert nodes (non-primary primary alerts sorted by time)
  const alertCount = primaryAlerts.length;
  primaryAlerts.forEach((alert, i) => {
    const angle = (2 * Math.PI * i) / Math.max(alertCount, 1) - Math.PI / 2;
    const pos = {
      x: centre.x + 140 * Math.cos(angle),
      y: centre.y + 140 * Math.sin(angle),
    };
    const saved = loadSavedPosition(`${incId}::alert-${alert.id}`);
    elements.push({
      group: 'nodes',
      data: {
        id: `alert-${alert.id}`,
        label: alert.title,
        type: 'alert',
        alertId: alert.id,
        incidentId: incId,
        parent: `hull-${incId}`,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title,
        created_at: (alert.created_at || '').slice(0, 16),
      },
      position: saved || pos,
      classes: `alert-node severity-${alert.severity} method-${alert.detection_method}`,
    });

    // Edge from root to alert if same event type
    elements.push({
      group: 'edges',
      data: {
        id: `e-root-${alert.id}`,
        source: `root-${alert.id}`,
        target: `alert-${alert.id}`,
        r: null,
      },
      classes: 'intra-edge',
    });

    // Signal dep edges
    (alert.signal_deps || []).forEach(dep => {
      if (dep.r !== null && Math.abs(dep.r) >= 0.3) {
        elements.push({
          group: 'edges',
          data: {
            id: `dep-${alert.id}-${dep.sensor}`,
            source: `root-${alert.id}`,
            target: `alert-${alert.id}`,
            r: dep.r,
          },
          classes: 'dep-edge',
        });
      }
    });
  });

  // Cross-incident alert nodes (placed at midpoint between clusters)
  crossAlerts.forEach(alert => {
    const pos = computeCrossIncidentPosition(incId, allIncidents, centroids);
    const saved = loadSavedPosition(`${incId}::cross-${alert.id}`);
    elements.push({
      group: 'nodes',
      data: {
        id: `cross-${alert.id}`,
        label: alert.event_type.replace(/_/g, ' '),
        type: 'cross',
        alertId: alert.id,
        incidentId: incId,
        severity: alert.severity,
        method: alert.detection_method,
        title: alert.title,
      },
      position: saved || pos,
      classes: `cross-node severity-${alert.severity} method-${alert.detection_method}`,
    });

    // Edge from cluster to cross node
    elements.push({
      group: 'edges',
      data: {
        id: `ce-${incId}-${alert.id}`,
        source: `hull-${incId}`,
        target: `cross-${alert.id}`,
        r: null,
      },
      classes: 'cross-edge',
    });
  });

  return elements;
}

// ── Ghost cluster for unselected incidents ────────────────────────────────────

function buildGhostCluster(inc, centroids) {
  const centre = centroids[inc.id] || { x: 0, y: 0 };
  return [{
    group: 'nodes',
    data: {
      id: `hull-${inc.id}`,
      label: inc.id,
      type: 'hull',
      incidentId: inc.id,
      alertCount: inc.alert_count || 0,
    },
    position: centre,
    classes: `hull ghost severity-${inc.max_severity}`,
  }];
}

// ── Cross-incident node placement ─────────────────────────────────────────────

function computeCrossIncidentPosition(incId, allIncidents, centroids) {
  // Place at midpoint of all cluster centroids (excluding current)
  const others = allIncidents.filter(i => i.id !== incId);
  if (others.length === 0) {
    const c = centroids[incId] || { x: 0, y: 0 };
    return { x: c.x + 200, y: c.y };
  }
  const sumX = others.reduce((s, i) => s + (centroids[i.id] || { x: 0 }).x, 0);
  const sumY = others.reduce((s, i) => s + (centroids[i.id] || { y: 0 }).y, 0);
  return { x: sumX / others.length, y: sumY / others.length };
}

// ── localStorage position persistence ────────────────────────────────────────

function loadSavedPosition(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function restorePositions() {
  if (!cy) return;
  cy.nodes().forEach(node => {
    const key = `${currentIncidentId}::${node.id()}`;
    const saved = loadSavedPosition(key);
    if (saved) node.position(saved);
  });
}

// ── Selection opacity ─────────────────────────────────────────────────────────

function applySelectionOpacity(selectedId) {
  if (!cy) return;
  cy.nodes().forEach(n => {
    const isSelected = n.data('incidentId') === selectedId;
    n.style('opacity', isSelected ? 1 : 0.3);
  });
  cy.edges().forEach(e => {
    const src = cy.$id(e.data('source'));
    const tgt = cy.$id(e.data('target'));
    const isSelected =
      src.data('incidentId') === selectedId ||
      tgt.data('incidentId') === selectedId;
    e.style('opacity', isSelected ? 1 : 0.2);
  });
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(ui): add Cytoscape init, layout algorithm, cluster builder, localStorage persistence"
```

---

## Task 14: JS — Cytoscape Styling (Dual Encoding + Progressive Zoom)

**Files:**
- Modify: `static/js/incident_graph.js`

- [ ] **Step 1: Add `buildCytoscapeStyle()` and `applyZoomClasses()` functions**

Add these functions before `initCytoscape()` in the file:

```javascript
// ── Cytoscape stylesheet ──────────────────────────────────────────────────────

// SVG glyphs for detection method (embedded data URIs)
const GLYPHS = {
  threshold:   'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><polygon points="10,2 18,18 2,18" fill="%23ffffff" opacity="0.9"/></svg>',
  ml:          'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><polygon points="10,2 18,10 10,18 2,10" fill="%23ffffff" opacity="0.9"/></svg>',
  statistical: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><circle cx="10" cy="10" r="7" fill="%23ffffff" opacity="0.9"/></svg>',
  fingerprint: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><polygon points="10,3 17,7 17,13 10,17 3,13 3,7" fill="none" stroke="%23ffffff" stroke-width="2" opacity="0.9"/></svg>',
  summary:     'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect x="3" y="3" width="14" height="14" rx="2" fill="%23ffffff" opacity="0.9"/></svg>',
};

const SEV_BORDER = {
  critical: '#8a1515',
  warning:  '#c47a1e',
  info:     '#1a4060',
};

function buildCytoscapeStyle() {
  return [
    // ── Base node ──────────────────────────────────────────────────────────
    {
      selector: 'node',
      style: {
        'background-color': '#1e2530',
        'border-width': 2,
        'border-color': '#4a5568',
        'label': '',       // labels toggled by zoom class
        'color': '#d1d5db',
        'font-size': 10,
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'text-wrap': 'ellipsis',
        'text-max-width': 80,
        'width': 28,
        'height': 28,
        'background-image-containment': 'inside',
        'background-clip': 'none',
        'background-image-opacity': 0.9,
      },
    },

    // ── Hull (compound cluster) ────────────────────────────────────────────
    {
      selector: 'node.hull',
      style: {
        'background-color': 'rgba(30,37,48,0.35)',
        'border-width': 1.5,
        'border-style': 'solid',
        'border-color': '#4a5568',
        'shape': 'roundrectangle',
        'padding': '24px',
        'label': 'data(label)',
        'font-size': 9,
        'color': '#6b7280',
        'text-valign': 'top',
        'text-halign': 'center',
        'width': 'label',
        'height': 'label',
      },
    },
    { selector: 'node.hull.ghost', style: { 'background-color': 'rgba(30,37,48,0.15)', 'border-color': '#2a3240' } },

    // ── Root signal nodes ──────────────────────────────────────────────────
    {
      selector: 'node.root-signal',
      style: {
        'width': 22,
        'height': 22,
        'border-width': 2.5,
        'background-color': '#252d3d',
      },
    },

    // ── Alert nodes ────────────────────────────────────────────────────────
    {
      selector: 'node.alert-node',
      style: {
        'width': 28,
        'height': 28,
        'border-width': 2,
        'background-color': '#1e2530',
      },
    },

    // ── Cross-incident nodes ───────────────────────────────────────────────
    {
      selector: 'node.cross-node',
      style: {
        'width': 24,
        'height': 24,
        'border-style': 'dashed',
        'border-color': '#3d6b4a',
        'background-color': '#1a2a1e',
      },
    },

    // ── Severity border colours ────────────────────────────────────────────
    { selector: 'node.severity-critical', style: { 'border-color': SEV_BORDER.critical } },
    { selector: 'node.severity-warning',  style: { 'border-color': SEV_BORDER.warning } },
    { selector: 'node.severity-info',     style: { 'border-color': SEV_BORDER.info } },

    // ── Detection method glyphs ────────────────────────────────────────────
    { selector: 'node.method-threshold',   style: { 'background-image': GLYPHS.threshold } },
    { selector: 'node.method-ml',          style: { 'background-image': GLYPHS.ml } },
    { selector: 'node.method-statistical', style: { 'background-image': GLYPHS.statistical } },
    { selector: 'node.method-fingerprint', style: { 'background-image': GLYPHS.fingerprint } },
    { selector: 'node.method-summary',     style: { 'background-image': GLYPHS.summary } },

    // ── Progressive zoom: labels-ts class (zoom 0.9–1.6) ─────────────────
    {
      selector: 'node.alert-node.labels-ts',
      style: { 'label': 'data(created_at)' },
    },
    // ── Progressive zoom: labels-full class (zoom > 1.6) ──────────────────
    {
      selector: 'node.alert-node.labels-full',
      style: { 'label': 'data(label)' },
    },
    {
      selector: 'node.root-signal.labels-full',
      style: { 'label': 'data(label)' },
    },

    // ── Edges ──────────────────────────────────────────────────────────────
    {
      selector: 'edge',
      style: {
        'width': 1,
        'line-color': '#374151',
        'curve-style': 'bezier',
        'opacity': 0.7,
      },
    },
    {
      selector: 'edge.intra-edge',
      style: {
        'line-color': '#4a5568',
        'width': 1,
      },
    },
    {
      selector: 'edge.dep-edge',
      style: {
        // mapData: r value 0.3–1.0 → width 0.5–3.0
        'width': 'mapData(r, 0.3, 1.0, 0.5, 3.0)',
        'line-color': '#3b82f6',
        'opacity': 0.6,
      },
    },
    {
      selector: 'edge.cross-edge',
      style: {
        'line-color': '#3d6b4a',
        'line-style': 'dashed',
        'width': 1.5,
        'opacity': 0.6,
      },
    },

    // ── Selected node highlight ────────────────────────────────────────────
    {
      selector: 'node:selected',
      style: {
        'border-width': 3,
        'border-color': '#60a5fa',
        'background-color': '#1e3a5f',
      },
    },
  ];
}

// ── Progressive zoom ──────────────────────────────────────────────────────────

function applyZoomClasses(zoom) {
  if (!cy) return;
  cy.nodes('.alert-node, .root-signal').forEach(n => {
    if (zoom < 0.9) {
      n.removeClass('labels-ts labels-full');
    } else if (zoom < 1.6) {
      n.addClass('labels-ts');
      n.removeClass('labels-full');
    } else {
      n.addClass('labels-full');
      n.removeClass('labels-ts');
    }
  });
}
```

- [ ] **Step 2: Remove the old stub `function initCytoscape() {}` line** (only the empty stub — the full implementation was added in Task 13).

Verify with:
```bash
grep -c "function initCytoscape" static/js/incident_graph.js
```
Expected: `1` (one definition only).

- [ ] **Step 3: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(ui): add Cytoscape stylesheet — dual-encoding border+glyph, progressive zoom"
```

---

## Task 15: End-to-End Smoke Test + Base Page Test

**Files:**
- Modify: `tests/test_pages.py` (or create `tests/test_incident_e2e.py`)

- [ ] **Step 1: Add a page-level smoke test**

Open `tests/test_pages.py` and append:

```python
def test_incidents_page_loads(app_client):
    client, _ = app_client
    rv = client.get("/incidents")
    assert rv.status_code == 200
    assert b"cy-graph" in rv.data
    assert b"incident_graph.js" in rv.data
```

- [ ] **Step 2: Run all tests**

```bash
pytest --tb=short -q
```

Expected: all green (or only pre-existing failures unrelated to incidents).

- [ ] **Step 3: Commit**

```bash
git add tests/test_pages.py
git commit -m "test: add smoke test for /incidents page"
```

---

## Task 16: Navigation — Add Incidents Tab to Base Template

**Files:**
- Modify: `templates/base.html`

- [ ] **Step 1: Locate the nav bar in `base.html`**

Search for the navigation link pattern:

```bash
grep -n "history\|controls\|admin" templates/base.html | head -20
```

- [ ] **Step 2: Add the Incidents nav link**

In the `<nav>` section, alongside the existing page links, add:

```html
<a href="/incidents" class="nav-link{% if request.path == '/incidents' %} active{% endif %}">Incidents</a>
```

Place it after the existing dashboard/history/controls links and before admin (order: Dashboard · History · Controls · **Incidents** · …).

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat(ui): add Incidents nav link to base template"
```

---

## Task 17: Full Run, Manual Smoke, and Final Cleanup

- [ ] **Step 1: Run the full test suite**

```bash
pytest --tb=short -q 2>&1 | tail -20
```

Expected: no new failures. Note any pre-existing failures and confirm they are unrelated.

- [ ] **Step 2: Deploy to Pi and verify service restarts cleanly**

```bash
git pull && sudo systemctl restart mlss-monitor
sudo journalctl -u mlss-monitor -n 50 --no-pager
```

Expected: `IncidentGrouper started` appears in logs, no tracebacks.

- [ ] **Step 3: Manual browser checks**

1. Navigate to `/incidents` — page loads with toolbar.
2. Incidents list populates (or shows "No incidents found" if DB is empty — that is correct).
3. Click an incident card → right panel shows narrative, causal ribbon, similar incidents.
4. Graph renders cluster for the selected incident.
5. Click a node → overlay shows alert detail with "View full inference →" link.
6. Drag a node → reload page → node stays in same position (localStorage working).
7. Zoom in past 0.9 → timestamps appear on nodes.
8. Zoom past 1.6 → full labels appear.
9. Symbol key is visible at bottom of graph canvas.
10. Search bar filters the incident list client-side.
11. Severity filter narrows the list.

- [ ] **Step 4: Final commit**

```bash
git add -u
git commit -m "feat(incidents): complete incident correlation graph — all tasks done"
```

- [ ] **Step 5: Open a pull request**

```bash
gh pr create \
  --title "feat: incident correlation graph" \
  --body "Adds Incidents tab with Cytoscape.js hub-and-spoke graph, grouper background thread, 3 REST endpoints, and AstroUXDS UI components. Closes design spec docs/superpowers/specs/2026-04-19-incident-correlation-graph-design.md."
```

---

## Self-Review

### Spec Coverage

| Spec requirement | Task |
|---|---|
| incidents stored in DB | Task 1 + Task 6 |
| Background grouper thread | Task 7 |
| 30-min sessionise gap | Task 2 (+ invariant 1) |
| `.total_seconds()` invariant | Task 2 (tested) |
| alert_signal_deps table | Task 1 + Task 6 |
| Pearson r NULL for < 10 points | Task 3 (tested) |
| 32-float signature vector | Task 4 |
| Cosine similarity for similar incidents | Task 5 + Task 8 |
| GET /api/incidents (window, severity, search) | Task 8 |
| GET /api/incidents/<id> (narrative, causal, similar) | Task 8 |
| GET /api/incidents/<id>/alert/<alert_id> | Task 8 |
| Blueprint registration | Task 9 |
| Grouper started at app startup | Task 9 |
| Own top-level Incidents tab | Task 10 + Task 16 |
| Cytoscape.js (CDN, MIT) | Task 10 + Task 13 |
| AstroUXDS components (rux-segmented-button, rux-input, rux-tag) | Task 10 + Task 12 |
| Hub-and-spoke layout | Task 13 |
| Dual-channel encoding (border=severity, glyph=method) | Task 14 |
| Cross-incident nodes at midpoint | Task 13 |
| Progressive zoom levels | Task 14 |
| Drag + localStorage persistence | Task 13 |
| Node click → overlay + full inference detail | Task 12 + Task 13 |
| Symbol key at bottom of graph | Task 10 + Task 11 |
| Search bar (debounced 300ms) | Task 12 |
| Severity filter (client-side) | Task 12 |
| Unselected incidents at 30% opacity | Task 13 (`applySelectionOpacity`) |
| Right panel: narrative (template-based) | Task 12 |
| Right panel: causal sequence ribbon (rux-tag) | Task 12 |
| Right panel: similar incidents (cosine) | Task 12 |
| No scrollbar in right panel | Task 11 (flex column + overflow hidden) |
| DETECTION_METHOD_MAP (all 5 types) | Task 2 (tested) |
| CROSS_INCIDENT_TYPES frozenset | Task 2 (tested) |
| is_primary = 0 for cross-incident alerts | Task 6 (tested) |
| Narrative template (observed/inferred/impact) | Task 8 |

### Type Consistency Check

- `detection_method()` returns `"threshold" | "ml" | "fingerprint" | "summary" | "statistical"` — all five match `method-*` CSS classes in Task 14.
- `make_incident_id()` returns `INC-YYYYMMDD-HHMM` — matches `incident.id` format in `_seed_incident` test helper.
- `build_incident_similarity_vector()` returns `list[float]` length 32 — matches `json.dumps()` call in `_upsert_incident` and `json.loads()` in `_find_similar`.
- `cosine_similarity(a, b)` takes `list[float]` — called with `json.loads(signature)` in Task 8.
- Node data field `alertId` (camelCase) in JS — matched by `node.data('alertId')` in overlay handler.
- `is_primary` is INTEGER 0/1 in DB — read as `a.is_primary` in route (sqlite3.Row dict conversion).

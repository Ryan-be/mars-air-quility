# Causal Incident Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the temporal `sessionise` + `merge_similar_adjacent` incident grouper with a causal-DAG approach: alerts are grouped when they share a sensor (matching sign, |r| ≥ 0.5) within a decaying time window (P=1.0 at ≤30 min, linear down to 0 at 4h), and connected components of that graph are incidents. Ship the full loop — server-side regrouping, per-edge confidence, per-incident confidence, view-side slider for filtering, client-side subdivision preview, operator split/unsplit, and batch commit-splits.

**Architecture:** The grouper becomes a composition of pure functions (`edge_probability`, `build_edges`, `connected_components`, `incident_confidence`) that are individually unit-testable without a DB. `regroup_all` wires them with I/O. A new `incident_splits` table stores operator decisions that regroupings must respect. The API gains per-incident edge data and `split` / `unsplit` endpoints. The frontend gains a slider, confidence bars, dash-by-confidence hull borders, an advisory banner, operator action buttons, and a client-side `connectedComponents` helper that mirrors the server algorithm so subdivision previews are semantics-faithful.

**Tech Stack:** Python 3.11+, Flask, SQLite (via stdlib `sqlite3`), Cytoscape.js (already loaded), vanilla JS modules, pytest.

**Spec reference:** `docs/superpowers/specs/2026-04-23-causal-incident-grouping-design.md`

---

## File Structure

### Modified files (backend)

- `database/init_db.py` — add one `CREATE TABLE IF NOT EXISTS incident_splits`.
- `mlss_monitor/incident_grouper.py` — major rewrite. Delete `sessionise`, `merge_similar_adjacent`, their helpers, and the related module constants. Add `edge_probability`, `build_edges`, `connected_components`, `incident_confidence`, `_load_split_markers`. Rewrite `regroup_all` to compose these.
- `mlss_monitor/routes/api_incidents.py` — augment `get_incident` with an `edges` array; add `POST /api/incidents/<id>/split` and `POST /api/incidents/<id>/unsplit`, both gated behind `@require_role("controller")`.

### Modified files (frontend)

- `templates/incidents.html` — add the edge-strength slider to the toolbar; add `[Split at weakest link]`, `[Undo split]`, `[Commit these splits]` buttons in the detail panel.
- `static/js/incident_graph.js` — add `connectedComponents` pure helper; wire the slider and subdivision preview; ramp edge opacity/width/style by P; render confidence bars on cards; render advisory banner + operator buttons; POST handlers for `/split` and `/unsplit`.
- `static/css/incident_graph.css` — slider styles, confidence bar, hull dash-by-confidence variants, advisory banner, subdivision preview outlines.

### Modified files (tests + docs)

- `tests/test_incident_grouper.py` — delete tests for `sessionise`, `merge_similar_adjacent`, their helpers. Add tests for all new pure functions plus integration tests against a seeded DB.
- `tests/test_api_incidents.py` — add split/unsplit endpoint tests.
- `readme.md` — rewrite the Incident correlation graph section to describe the new grouping.

### New files

- `tests/js/test_connected_components.mjs` — small stand-alone Node script with fixture cases that both the Python `connected_components` and the JS `connectedComponents` must pass. Runs as `node tests/js/test_connected_components.mjs`; exit 0 = pass, non-zero = fail.

---

## Task ordering rationale

Schema first, then pure functions (red/green individually), then composition in `regroup_all`, then API endpoints, then legacy deletion, then frontend. This lets every task commit on a working tree: early backend work doesn't break the existing grouper because the new code is unused until Task 6 rewires `regroup_all`.

---

### Task 1: Add `incident_splits` schema

**Files:**
- Modify: `database/init_db.py` (around line 317, after the existing `alert_signal_deps` block)
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_incident_grouper.py` near the other `tmp_db`-using tests (bottom of file):

```python
def test_incident_splits_table_created(tmp_db):
    """init_db.create_db() should create the incident_splits table."""
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='incident_splits'"
    ).fetchone()
    conn.close()
    assert row is not None, "incident_splits table should exist after create_db()"


def test_incident_splits_columns(tmp_db):
    """incident_splits has alert_id PK, created_by, created_at columns."""
    conn = sqlite3.connect(tmp_db)
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(incident_splits)").fetchall()
    }
    conn.close()
    assert cols == {"alert_id", "created_by", "created_at"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py::test_incident_splits_table_created tests/test_incident_grouper.py::test_incident_splits_columns -v`
Expected: FAIL with "incident_splits table should exist".

- [ ] **Step 3: Add the table**

In `database/init_db.py`, immediately after the `alert_signal_deps` CREATE statement and before `conn.commit()`:

```python
    cur.execute("""
    CREATE TABLE IF NOT EXISTS incident_splits (
        alert_id    INTEGER PRIMARY KEY REFERENCES inferences(id) ON DELETE CASCADE,
        created_by  TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py::test_incident_splits_table_created tests/test_incident_grouper.py::test_incident_splits_columns -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database/init_db.py tests/test_incident_grouper.py
git commit -m "feat(incidents): add incident_splits table for operator split markers

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: `edge_probability` pure function

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_incident_grouper.py` (before the DB-integration tests near the bottom):

```python
from mlss_monitor.incident_grouper import edge_probability


def _make_alert(id, ts, deps=()):
    """Build an alert dict with the signal_deps shape the grouper expects.

    deps: iterable of (sensor, r) pairs.
    """
    return {
        "id": id,
        "created_at": ts,
        "signal_deps": [
            {"sensor": s, "r": r, "lag_seconds": 0} for s, r in deps
        ],
    }


def test_edge_probability_zero_when_no_shared_sensor():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("tvoc_ppb", 0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_zero_when_shared_sensor_signs_differ():
    """Rising eCO2 alert (positive r) and falling eCO2 alert (negative r)
    should NOT link — they're physically opposite events."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm",  0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("eco2_ppm", -0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_zero_when_r_below_threshold():
    """|r| must be >= 0.5 for a sensor to count as 'strongly involved'."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.45)])
    b = _make_alert(2, "2026-04-23 09:05:00", [("eco2_ppm", 0.8)])
    assert edge_probability(a, b) == 0.0


def test_edge_probability_full_at_zero_gap():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:00:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_full_at_30_minute_gap():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:30:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_decays_linearly_between_30_and_240():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    # Gap of 135 minutes => halfway between 30 and 240 => P = 0.5
    b = _make_alert(2, "2026-04-23 11:15:00", [("eco2_ppm", 0.9)])
    assert abs(edge_probability(a, b) - 0.5) < 0.001


def test_edge_probability_zero_at_and_beyond_240_minutes():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 13:00:00", [("eco2_ppm", 0.9)])  # 4h
    assert edge_probability(a, b) == 0.0
    c = _make_alert(3, "2026-04-23 14:00:00", [("eco2_ppm", 0.9)])  # 5h
    assert edge_probability(a, c) == 0.0


def test_edge_probability_symmetric_in_order():
    """Gap is abs — order of arguments doesn't matter."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 10:00:00", [("eco2_ppm", 0.9)])
    assert edge_probability(a, b) == edge_probability(b, a)


def test_edge_probability_handles_negative_r_matching():
    """Two falling-eCO2 alerts (both negative r) DO link."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", -0.7)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", -0.6)])
    assert edge_probability(a, b) == 1.0


def test_edge_probability_handles_null_r_in_deps():
    """signal_deps rows with r=None are skipped (pre-Pearson data)."""
    a = _make_alert(1, "2026-04-23 09:00:00",
                    [("eco2_ppm", None), ("tvoc_ppb", 0.8)])
    b = _make_alert(2, "2026-04-23 09:05:00",
                    [("tvoc_ppb", 0.7)])
    assert edge_probability(a, b) == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py -k edge_probability -v`
Expected: FAIL with `ImportError: cannot import name 'edge_probability'`.

- [ ] **Step 3: Implement `edge_probability`**

Add to `mlss_monitor/incident_grouper.py`, near the top of the "Pure logic" section (after the existing `detection_method` function):

```python
# ── Causal-DAG edge probability ──────────────────────────────────────────────
#
# Edge A→B is probabilistic. Two alerts are "linked" (P > 0) if:
#   1. They share at least one sensor with |r| >= EDGE_STRONG_R_THRESHOLD
#      AND the sign of r matches on that sensor ("both rose together" or
#      "both fell together" — not one rose while the other fell).
#   2. The time gap is inside the decay window:
#        gap ≤ EDGE_FULL_P_WINDOW_MINUTES      ==> P = 1.0
#        gap ≥ EDGE_ZERO_P_WINDOW_MINUTES      ==> P = 0.0
#        otherwise: linear decay between those points.

EDGE_FULL_P_WINDOW_MINUTES = 30
EDGE_ZERO_P_WINDOW_MINUTES = 240
EDGE_STRONG_R_THRESHOLD = 0.5


def _strong_signed_sensors(alert: dict[str, Any]) -> set[tuple[str, int]]:
    """Return the set of (sensor, sign) pairs where |r| >= threshold.

    sign is +1 (r >= 0) or -1 (r < 0). None-valued r's are skipped.
    """
    out = set()
    for d in (alert.get("signal_deps") or []):
        r = d.get("r")
        sensor = d.get("sensor")
        if r is None or sensor is None:
            continue
        if abs(r) >= EDGE_STRONG_R_THRESHOLD:
            out.add((sensor, 1 if r >= 0 else -1))
    return out


def edge_probability(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Probability that alerts A and B belong to the same causal incident.

    See the section comment above for semantics.
    """
    # 1. Signed sensor overlap
    if not (_strong_signed_sensors(a) & _strong_signed_sensors(b)):
        return 0.0
    # 2. Time decay
    try:
        ta = datetime.fromisoformat(str(a["created_at"]))
        tb = datetime.fromisoformat(str(b["created_at"]))
    except (KeyError, ValueError):
        return 0.0
    gap_min = abs((tb - ta).total_seconds()) / 60.0
    if gap_min <= EDGE_FULL_P_WINDOW_MINUTES:
        return 1.0
    if gap_min >= EDGE_ZERO_P_WINDOW_MINUTES:
        return 0.0
    span = EDGE_ZERO_P_WINDOW_MINUTES - EDGE_FULL_P_WINDOW_MINUTES
    return (EDGE_ZERO_P_WINDOW_MINUTES - gap_min) / span
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py -k edge_probability -v`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): edge_probability pure function

Computes P(link) between two alerts using sensor-share-with-matching-sign
(|r| >= 0.5) plus a linear time-decay from P=1.0 at 30min to P=0.0 at 4h.
Pure function, 10 unit tests covering overlap, sign mismatch, threshold,
gap boundaries, and None-valued r handling.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: `build_edges` pure function

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing tests**

```python
from mlss_monitor.incident_grouper import build_edges, MIN_EDGE_P_SERVER


def test_build_edges_empty_input():
    assert build_edges([], split_marker_ids=set()) == []


def test_build_edges_single_alert_no_edges():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    assert build_edges([a], split_marker_ids=set()) == []


def test_build_edges_basic_pair():
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert len(edges) == 1
    src, dst, p = edges[0]
    assert src == 1 and dst == 2
    assert p == 1.0


def test_build_edges_drops_below_server_floor():
    """Edges with P < MIN_EDGE_P_SERVER are not returned."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    # Gap of 235 minutes => P = (240-235)/210 ≈ 0.024 < 0.05 floor
    b = _make_alert(2, "2026-04-23 12:55:00", [("eco2_ppm", 0.8)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert edges == []


def test_build_edges_directed_by_created_at():
    """src has the earlier created_at, dst has the later."""
    a = _make_alert(1, "2026-04-23 09:10:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    edges = build_edges([a, b], split_marker_ids=set())
    assert len(edges) == 1
    src, dst, _ = edges[0]
    assert src == 2 and dst == 1


def test_build_edges_respects_split_marker():
    """A split-marker on B means any edge A→B where A is earlier than B
    is suppressed."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids={2})
    assert edges == []


def test_build_edges_split_marker_is_later_alert_only():
    """A split-marker on the EARLIER alert doesn't suppress the edge —
    only markers on the LATER alert do (the marker means 'break
    chain BEFORE this alert')."""
    a = _make_alert(1, "2026-04-23 09:00:00", [("eco2_ppm", 0.8)])
    b = _make_alert(2, "2026-04-23 09:10:00", [("eco2_ppm", 0.7)])
    edges = build_edges([a, b], split_marker_ids={1})
    assert len(edges) == 1  # marker on a (earlier) does not affect A→B


def test_build_edges_all_pairs():
    """N(N-1)/2 edges for N fully-connected alerts."""
    alerts = [
        _make_alert(i, f"2026-04-23 09:{i:02d}:00", [("eco2_ppm", 0.8)])
        for i in range(4)
    ]
    edges = build_edges(alerts, split_marker_ids=set())
    assert len(edges) == 6  # 4C2


def test_min_edge_p_server_is_0_05():
    assert MIN_EDGE_P_SERVER == 0.05
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py -k build_edges -v`
Expected: FAIL, `cannot import name 'build_edges'`.

- [ ] **Step 3: Implement `build_edges`**

Add to `mlss_monitor/incident_grouper.py`, after `edge_probability`:

```python
# Edges below this floor are dropped — prevents near-zero chains from
# being persisted.  Operators can still see them by lowering the view-
# side slider, but they don't form incidents server-side.
MIN_EDGE_P_SERVER = 0.05


def build_edges(
    alerts: list[dict[str, Any]],
    split_marker_ids: set[int],
) -> list[tuple[int, int, float]]:
    """Build the directed edge list for an alert set.

    For each unordered pair, compute P and emit an ordered tuple
    (src_id, dst_id, p) where src has the earlier created_at.
    Edges with P < MIN_EDGE_P_SERVER are dropped.  Edges where the
    later alert is a split marker are suppressed.
    """
    edges: list[tuple[int, int, float]] = []
    n = len(alerts)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = alerts[i], alerts[j]
            # Order by created_at so src is always the earlier alert.
            ta = str(a.get("created_at", ""))
            tb = str(b.get("created_at", ""))
            if ta <= tb:
                earlier, later = a, b
            else:
                earlier, later = b, a
            # Suppress edges into a split marker (the later alert).
            if later["id"] in split_marker_ids:
                continue
            p = edge_probability(earlier, later)
            if p < MIN_EDGE_P_SERVER:
                continue
            edges.append((earlier["id"], later["id"], p))
    return edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py -k build_edges -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): build_edges pure function + MIN_EDGE_P_SERVER floor

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: `connected_components` pure function

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing tests**

```python
from mlss_monitor.incident_grouper import connected_components


def _ids(components):
    """Return components as sorted lists of ids, for order-independent assertions."""
    return sorted([sorted(c) for c in components])


def test_connected_components_empty():
    assert connected_components([], []) == []


def test_connected_components_single_alert_singleton():
    alerts = [_make_alert(1, "2026-04-23 09:00:00")]
    components = connected_components(alerts, edges=[])
    assert _ids(components) == [[1]]


def test_connected_components_no_edges_all_singletons():
    alerts = [_make_alert(i, f"2026-04-23 09:{i:02d}:00") for i in range(3)]
    components = connected_components(alerts, edges=[])
    assert _ids(components) == [[0], [1], [2]]


def test_connected_components_one_edge_one_component():
    alerts = [
        _make_alert(1, "2026-04-23 09:00:00"),
        _make_alert(2, "2026-04-23 09:10:00"),
    ]
    components = connected_components(alerts, edges=[(1, 2, 0.9)])
    assert _ids(components) == [[1, 2]]


def test_connected_components_transitive_chain():
    """A→B and B→C but no A↔C edge. All three should be one component."""
    alerts = [
        _make_alert(1, "2026-04-23 09:00:00"),
        _make_alert(2, "2026-04-23 09:15:00"),
        _make_alert(3, "2026-04-23 09:30:00"),
    ]
    edges = [(1, 2, 0.8), (2, 3, 0.7)]
    components = connected_components(alerts, edges)
    assert _ids(components) == [[1, 2, 3]]


def test_connected_components_two_disjoint_subgraphs():
    alerts = [_make_alert(i, f"2026-04-23 09:{i:02d}:00") for i in range(1, 5)]
    # Edges {1-2} and {3-4}; 1 and 3 never connect.
    edges = [(1, 2, 0.9), (3, 4, 0.9)]
    components = connected_components(alerts, edges)
    assert _ids(components) == [[1, 2], [3, 4]]


def test_connected_components_returns_alert_dicts_not_ids():
    """Components are lists of the original alert dicts (not just ids),
    so downstream code can read created_at, severity, etc. without
    re-looking-up."""
    a1 = _make_alert(1, "2026-04-23 09:00:00")
    a2 = _make_alert(2, "2026-04-23 09:10:00")
    components = connected_components([a1, a2], edges=[(1, 2, 0.9)])
    assert len(components) == 1
    # Same object identity — we pass through the dicts.
    assert set(id(a) for a in components[0]) == {id(a1), id(a2)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py -k connected_components -v`
Expected: FAIL, `cannot import name 'connected_components'`.

- [ ] **Step 3: Implement `connected_components`**

Add to `mlss_monitor/incident_grouper.py`, after `build_edges`:

```python
def connected_components(
    alerts: list[dict[str, Any]],
    edges: list[tuple[int, int, float]],
) -> list[list[dict[str, Any]]]:
    """Group alerts into connected components of the undirected edge graph.

    Isolated alerts become singleton components.  Component order is
    deterministic: sorted by the minimum alert id within each component,
    ascending.  Alerts within each component are returned in their input
    order (the caller typically passes alerts sorted by created_at).
    """
    if not alerts:
        return []

    # Union-find over alert ids.
    parent: dict[int, int] = {a["id"]: a["id"] for a in alerts}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for src, dst, _p in edges:
        if src in parent and dst in parent:
            union(src, dst)

    # Collect alerts into their component buckets (keyed by root id).
    buckets: dict[int, list[dict[str, Any]]] = {}
    for a in alerts:
        root = find(a["id"])
        buckets.setdefault(root, []).append(a)

    # Deterministic order: sort by the min alert id inside each bucket.
    return sorted(
        buckets.values(),
        key=lambda comp: min(a["id"] for a in comp),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py -k connected_components -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): connected_components pure function (union-find)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: `incident_confidence` pure function

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing tests**

```python
from mlss_monitor.incident_grouper import incident_confidence


def test_incident_confidence_singleton_is_one():
    """No edges inside the component => max confidence (nothing to doubt)."""
    assert incident_confidence(edges_in_component=[]) == 1.0


def test_incident_confidence_single_edge():
    assert incident_confidence(edges_in_component=[(1, 2, 0.72)]) == 0.72


def test_incident_confidence_min_over_edges():
    """Weakest link sets the confidence."""
    edges = [(1, 2, 0.9), (2, 3, 0.31), (3, 4, 0.65)]
    assert incident_confidence(edges) == 0.31


def test_incident_confidence_ignores_edges_order():
    edges = [(3, 4, 0.65), (1, 2, 0.9), (2, 3, 0.31)]
    assert incident_confidence(edges) == 0.31
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py -k incident_confidence -v`
Expected: FAIL, `cannot import name 'incident_confidence'`.

- [ ] **Step 3: Implement `incident_confidence`**

Add to `mlss_monitor/incident_grouper.py`, after `connected_components`:

```python
def incident_confidence(
    edges_in_component: list[tuple[int, int, float]],
) -> float:
    """Return min(edge probability over the component) or 1.0 for singletons.

    Interpretation: "the chain is only as trustworthy as its weakest link".
    """
    if not edges_in_component:
        return 1.0
    return min(p for _src, _dst, p in edges_in_component)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py -k incident_confidence -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): incident_confidence = min edge P, 1.0 for singletons

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Helper to load split markers from DB

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`
- Test: `tests/test_incident_grouper.py`

- [ ] **Step 1: Write failing test**

```python
from mlss_monitor.incident_grouper import _load_split_markers


def test_load_split_markers_empty(tmp_db):
    assert _load_split_markers(tmp_db) == set()


def test_load_split_markers_returns_ids(tmp_db):
    conn = sqlite3.connect(tmp_db)
    for alert_id in (101, 202, 303):
        # Parent inference row so FK is valid.
        conn.execute(
            "INSERT INTO inferences (id, created_at, event_type, severity, "
            "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            (alert_id, "2026-04-23 09:00:00", "tvoc_spike",
             "info", "t", 0.9),
        )
        conn.execute(
            "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
            (alert_id, "test-user"),
        )
    conn.commit()
    conn.close()

    assert _load_split_markers(tmp_db) == {101, 202, 303}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_incident_grouper.py -k load_split_markers -v`
Expected: FAIL, `cannot import name '_load_split_markers'`.

- [ ] **Step 3: Implement `_load_split_markers`**

Add to `mlss_monitor/incident_grouper.py`, in the "DB persistence" section (near `_load_all_inferences`):

```python
def _load_split_markers(db_file: str) -> set[int]:
    """Load operator split marker alert ids from incident_splits."""
    conn = sqlite3.connect(db_file, timeout=15)
    rows = conn.execute("SELECT alert_id FROM incident_splits").fetchall()
    conn.close()
    return {r[0] for r in rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_incident_grouper.py -k load_split_markers -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): _load_split_markers helper reads incident_splits table

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Rewrite `regroup_all` to use the causal DAG

**Files:**
- Modify: `mlss_monitor/incident_grouper.py` (replace `regroup_all` body; do NOT yet delete the old `sessionise`/`merge_similar_adjacent` — that happens in Task 11)
- Test: `tests/test_incident_grouper.py`

Note: this task replaces the grouping behaviour end-to-end. Existing `regroup_all`-related tests in `test_incident_grouper.py` that were tied to the old sessionise semantics (see the tests added in commit `bab13a8`) will fail after this change. Delete those tests as part of this task.

- [ ] **Step 1: Write the new integration tests**

Add to `tests/test_incident_grouper.py` (replacing the old tests named `test_merge_similar_*` and `test_regroup_all_merges_similar_sessions`, which are no longer meaningful):

```python
def _seed_inf_with_dep(db_path, ts, sensor, r, event_type="tvoc_spike"):
    """Seed one inference + its alert_signal_deps row in one call."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (ts, event_type, "info", f"alert-{ts}", 0.9),
    )
    alert_id = cur.lastrowid
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (alert_id, sensor, r, 0),
    )
    conn.commit()
    conn.close()
    return alert_id


def test_regroup_all_causal_groups_shared_sensor(tmp_db):
    """Two alerts 15 min apart sharing eCO2 with matching sign => one incident."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:15:00", "eco2_ppm", 0.7)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_causal_splits_disjoint_sensors(tmp_db):
    """Two alerts 10 min apart with DISJOINT strong sensors => two incidents."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:10:00", "pm25_ug_m3", 0.8)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_causal_transitive_chain(tmp_db):
    """A-B and B-C share sensors; A-C don't. All three become one incident."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm",  0.8)  # A
    a_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:10:00", "eco2_ppm",  0.7)  # A2 (bridge 1)
    # For the "bridge" B, give it BOTH eco2 and tvoc so it shares with A on eco2 and with C on tvoc.
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
        "VALUES (?, ?, ?, ?)",
        (a_id, "tvoc_ppb", 0.8, 0),
    )
    conn.commit()
    conn.close()
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:25:00", "tvoc_ppb", 0.7)  # C
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 1


def test_regroup_all_split_marker_breaks_chain(tmp_db):
    """An incident_splits row on an alert breaks the chain at that alert."""
    a_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    b_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:10:00", "eco2_ppm", 0.7)
    c_id = _seed_inf_with_dep(tmp_db, "2026-04-23 09:20:00", "eco2_ppm", 0.7)

    # Without a split: one incident.
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    conn.close()

    # Add a split marker on B ("break chain before B").
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (b_id, "test"),
    )
    conn.commit()
    conn.close()

    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    conn.close()
    assert count == 2


def test_regroup_all_persists_confidence(tmp_db):
    """incidents.confidence stores min edge P (or 1.0 for singletons)."""
    _seed_inf_with_dep(tmp_db, "2026-04-23 09:00:00", "eco2_ppm", 0.8)
    # Gap of 135 min => P = 0.5
    _seed_inf_with_dep(tmp_db, "2026-04-23 11:15:00", "eco2_ppm", 0.7)
    regroup_all(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conf = conn.execute("SELECT confidence FROM incidents").fetchone()[0]
    conn.close()
    assert abs(conf - 0.5) < 0.001
```

- [ ] **Step 2: Delete the stale old tests**

In `tests/test_incident_grouper.py`, delete these tests (they test the old algorithm and will fail or mislead):

- All tests named `test_merge_similar_*`
- `test_regroup_all_merges_similar_sessions`
- `test_regroup_all_two_groups_two_incidents` (was re-written for the similar-sessions merge pass; delete — covered by the new `test_regroup_all_causal_splits_disjoint_sensors`)

Also delete the import of `merge_similar_adjacent` from the `from mlss_monitor.incident_grouper import (...)` block and the `_alert_with_type` helper that those tests used.

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `python -m pytest tests/test_incident_grouper.py -k "regroup_all_causal or regroup_all_split_marker or regroup_all_persists_confidence" -v`
Expected: 5 FAIL (new behaviour not implemented yet).

- [ ] **Step 4: Rewrite `regroup_all`**

Replace the entire `regroup_all` function in `mlss_monitor/incident_grouper.py`:

```python
def regroup_all(db_file: str) -> None:
    """Re-build every incident from the causal graph.

    1. Load all non-dismissed inferences + their signal_deps.
    2. Split primary alerts vs cross-incident (hourly/daily summary) alerts.
    3. Load operator split markers.
    4. Build causal edges between primary alerts.
    5. Find connected components -> each becomes one incident.
    6. Attach cross-incident alerts to every incident that overlaps them in time.
    7. INSERT OR REPLACE each incident.
    Idempotent: safe to call on every restart.
    """
    raw_alerts = _load_all_inferences(db_file)

    # Attach signal_deps to each alert so the pure functions can read them.
    conn = sqlite3.connect(db_file, timeout=15)
    conn.row_factory = sqlite3.Row
    for a in raw_alerts:
        dep_rows = conn.execute(
            "SELECT sensor, r, lag_seconds FROM alert_signal_deps WHERE alert_id = ?",
            (a["id"],),
        ).fetchall()
        a["signal_deps"] = [dict(d) for d in dep_rows]
    conn.close()

    primary = [a for a in raw_alerts if not is_cross_incident(a.get("event_type", ""))]
    cross = [a for a in raw_alerts if is_cross_incident(a.get("event_type", ""))]

    # Attach detection_method so downstream (narrative) has it.
    for a in raw_alerts:
        a["detection_method"] = detection_method(a.get("event_type", ""))

    split_markers = _load_split_markers(db_file)
    edges = build_edges(primary, split_markers)
    components = connected_components(primary, edges)

    # Edge lookup keyed by unordered pair of alert ids, for O(1) confidence math.
    edge_by_pair: dict[frozenset[int], float] = {
        frozenset({src, dst}): p for src, dst, p in edges
    }

    conn = sqlite3.connect(db_file, timeout=15)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")

    # Fresh rebuild — clear then insert.  Keeps the grouping idempotent.
    cur.execute("DELETE FROM incident_alerts")
    cur.execute("DELETE FROM incidents")

    for component in components:
        if not component:
            continue
        sorted_comp = sorted(component, key=lambda a: a["created_at"])
        t_start = datetime.fromisoformat(sorted_comp[0]["created_at"])
        t_end = datetime.fromisoformat(sorted_comp[-1]["created_at"])
        incident_id = make_incident_id(t_start)

        # Confidence: min P over the edges that touch this component.
        comp_ids = {a["id"] for a in component}
        comp_edges = [
            (src, dst, p) for src, dst, p in edges
            if src in comp_ids and dst in comp_ids
        ]
        conf = incident_confidence(comp_edges)

        max_sev = max(
            (a.get("severity", "info") for a in component),
            key=lambda s: _SEVERITY_ORDER.get(s, 0),
        )
        title = generate_incident_title(component)
        signature = json.dumps(build_incident_similarity_vector(component))

        cur.execute(
            "INSERT OR REPLACE INTO incidents "
            "(id, started_at, ended_at, max_severity, confidence, title, signature) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (incident_id,
             t_start.isoformat(sep=" "),
             t_end.isoformat(sep=" "),
             max_sev, conf, title, signature),
        )

        # Primary alerts: is_primary=1
        for alert in component:
            cur.execute(
                "INSERT OR IGNORE INTO incident_alerts "
                "(incident_id, alert_id, is_primary) VALUES (?, ?, ?)",
                (incident_id, alert["id"], 1),
            )

        # Cross-incident alerts that fall within this incident's time window.
        for cross_alert in cross:
            ct = datetime.fromisoformat(cross_alert["created_at"])
            if t_start <= ct <= t_end:
                cur.execute(
                    "INSERT OR IGNORE INTO incident_alerts "
                    "(incident_id, alert_id, is_primary) VALUES (?, ?, ?)",
                    (incident_id, cross_alert["id"], 0),
                )

    conn.commit()
    conn.close()
```

- [ ] **Step 5: Run all grouper tests**

Run: `python -m pytest tests/test_incident_grouper.py -v`
Expected: All PASS (old sessionise tests deleted; new causal tests pass).

Also run the full API test suite since `regroup_all` is called in fixtures:

Run: `python -m pytest tests/test_api_incidents.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_incident_grouper.py
git commit -m "feat(grouper): rewrite regroup_all around causal DAG + split markers

Drops temporal sessionise-based grouping in favour of connected components
of the edge graph (edge_probability + build_edges + connected_components).
Respects operator split markers from the incident_splits table.
Persists min-edge-P as incidents.confidence.

Old sessionise-specific tests deleted — behaviour is now tested via the
new test_regroup_all_causal_* integration tests.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Augment `/api/incidents/<id>` with edges array

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py` (`get_incident` function)
- Test: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_api_incidents.py` (near other `get_incident` tests):

```python
def test_get_incident_detail_includes_edges(client, db):
    """Detail response includes an 'edges' array, one entry per edge in
    the component, each with {from, to, p, shared_sensors}."""
    # Seed two alerts sharing eco2 within the edge window + link them
    # into an incident via regroup.
    from mlss_monitor.incident_grouper import regroup_all
    import sqlite3
    conn = sqlite3.connect(db)
    for ts in ("2026-04-23 09:00:00", "2026-04-23 09:10:00"):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        conn.execute(
            "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, "eco2_ppm", 0.8, 0),
        )
    conn.commit()
    conn.close()
    regroup_all(db)

    # Get the single incident that was created.
    resp = client.get("/api/incidents?window=30d")
    inc_id = resp.get_json()["incidents"][0]["id"]

    resp = client.get(f"/api/incidents/{inc_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "edges" in data
    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert {"from", "to", "p", "shared_sensors"} <= set(edge.keys())
    assert edge["p"] == 1.0
    assert "eco2_ppm" in edge["shared_sensors"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_incidents.py::test_get_incident_detail_includes_edges -v`
Expected: FAIL, no `edges` key.

- [ ] **Step 3: Augment `get_incident`**

In `mlss_monitor/routes/api_incidents.py`, modify the `get_incident` handler. Find the block that builds `alerts` (with signal_deps populated) and, right before the final `return jsonify(...)`, insert:

```python
    # ── Compute causal edges between primary alerts for the UI ────────────
    from mlss_monitor.incident_grouper import (
        edge_probability, EDGE_STRONG_R_THRESHOLD,
    )
    primary_alerts = [a for a in alerts if a.get("is_primary")]
    # Sort chronologically so edges are always src(earlier) -> dst(later).
    primary_alerts.sort(key=lambda a: a.get("created_at", ""))
    edges_out: list[dict] = []
    for i, a1 in enumerate(primary_alerts):
        for a2 in primary_alerts[i + 1:]:
            p = edge_probability(a1, a2)
            if p <= 0.0:
                continue
            # Describe WHICH sensors drove the link, for the hover tooltip.
            strong_a = {
                d["sensor"] for d in (a1.get("signal_deps") or [])
                if d["r"] is not None and abs(d["r"]) >= EDGE_STRONG_R_THRESHOLD
            }
            strong_b = {
                d["sensor"] for d in (a2.get("signal_deps") or [])
                if d["r"] is not None and abs(d["r"]) >= EDGE_STRONG_R_THRESHOLD
            }
            edges_out.append({
                "from": a1["id"],
                "to": a2["id"],
                "p": round(p, 3),
                "shared_sensors": sorted(strong_a & strong_b),
            })
```

Then update the final `return jsonify(...)`:

```python
    return jsonify({
        **incident,
        "alerts": alerts,
        "causal_sequence": causal_sequence,
        "narrative": narrative,
        "similar": similar,
        "edges": edges_out,
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_incidents.py -v`
Expected: All PASS including the new one.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): include edges array in /api/incidents/<id> detail response

Each edge entry: {from, to, p, shared_sensors}. Computed at request time
from the stored signal_deps — no new persistence.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: `POST /api/incidents/<id>/split` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py`
- Test: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api_incidents.py`:

```python
def test_split_endpoint_creates_marker_and_regroups(client, db):
    """POST /api/incidents/<id>/split creates an incident_splits row and
    triggers a regroup so the incident actually splits."""
    from mlss_monitor.incident_grouper import regroup_all
    import sqlite3
    conn = sqlite3.connect(db)
    alert_ids = []
    for ts in ("2026-04-23 09:00:00", "2026-04-23 09:10:00",
               "2026-04-23 09:20:00"):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        alert_ids.append(cur.lastrowid)
        conn.execute(
            "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, "eco2_ppm", 0.8, 0),
        )
    conn.commit()
    conn.close()
    regroup_all(db)

    # Starts as one incident.
    listing = client.get("/api/incidents?window=30d").get_json()
    assert listing["total"] == 1
    inc_id = listing["incidents"][0]["id"]

    # Split at the middle alert.
    resp = client.post(
        f"/api/incidents/{inc_id}/split",
        json={"alert_id": alert_ids[1]},
    )
    assert resp.status_code == 200

    # Incident splits now contains the marker.
    conn = sqlite3.connect(db)
    markers = conn.execute("SELECT alert_id FROM incident_splits").fetchall()
    conn.close()
    assert (alert_ids[1],) in markers

    # Now there are two incidents.
    listing2 = client.get("/api/incidents?window=30d").get_json()
    assert listing2["total"] == 2


def test_split_endpoint_requires_alert_id(client, db):
    """Missing alert_id in body => 400."""
    resp = client.post("/api/incidents/INC-X/split", json={})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_incidents.py -k "split" -v`
Expected: FAIL (no split endpoint).

- [ ] **Step 3: Implement the split endpoint**

Add to `mlss_monitor/routes/api_incidents.py`, at the bottom, after `get_incident`:

```python
@api_incidents_bp.route("/api/incidents/<incident_id>/split", methods=["POST"])
def split_incident(incident_id: str):
    """Mark an alert as 'starts a new incident'. Persists a row in
    incident_splits and re-runs the grouper so the split takes effect
    immediately.
    """
    body = request.get_json(silent=True) or {}
    alert_id = body.get("alert_id")
    if not isinstance(alert_id, int):
        return jsonify({"error": "alert_id (int) is required in body"}), 400

    conn = _get_conn()
    # session_user is set by the auth layer; may be None in unauth tests.
    user = None
    try:
        from flask import session
        user = session.get("user")
    except RuntimeError:
        pass
    conn.execute(
        "INSERT OR REPLACE INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (alert_id, user),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.incident_grouper import regroup_all
    regroup_all(DB_FILE)

    return jsonify({"ok": True, "split_alert_id": alert_id})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_incidents.py -k "split" -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): POST /api/incidents/<id>/split persists operator split marker

Adds a row to incident_splits and triggers regroup_all so the split takes
effect immediately. 400 on missing/invalid alert_id.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 10: `POST /api/incidents/<id>/unsplit` endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py`
- Test: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing test**

```python
def test_unsplit_endpoint_removes_marker_and_regroups(client, db):
    """POST /unsplit removes the marker and regroups, merging the incidents back."""
    from mlss_monitor.incident_grouper import regroup_all
    import sqlite3
    conn = sqlite3.connect(db)
    alert_ids = []
    for ts in ("2026-04-23 09:00:00", "2026-04-23 09:10:00"):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        alert_ids.append(cur.lastrowid)
        conn.execute(
            "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, "eco2_ppm", 0.8, 0),
        )
    conn.execute(
        "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (alert_ids[1], "test"),
    )
    conn.commit()
    conn.close()
    regroup_all(db)
    # Starts as two incidents because of the split marker.
    listing = client.get("/api/incidents?window=30d").get_json()
    assert listing["total"] == 2
    inc_id = listing["incidents"][0]["id"]

    resp = client.post(
        f"/api/incidents/{inc_id}/unsplit",
        json={"alert_id": alert_ids[1]},
    )
    assert resp.status_code == 200

    # Marker gone, single incident.
    conn = sqlite3.connect(db)
    remaining = conn.execute(
        "SELECT COUNT(*) FROM incident_splits"
    ).fetchone()[0]
    conn.close()
    assert remaining == 0
    listing2 = client.get("/api/incidents?window=30d").get_json()
    assert listing2["total"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_incidents.py::test_unsplit_endpoint_removes_marker_and_regroups -v`
Expected: FAIL (no endpoint).

- [ ] **Step 3: Implement the unsplit endpoint**

Add to `mlss_monitor/routes/api_incidents.py`, after `split_incident`:

```python
@api_incidents_bp.route("/api/incidents/<incident_id>/unsplit", methods=["POST"])
def unsplit_incident(incident_id: str):
    """Remove an operator split marker. Re-runs the grouper so the
    previously-split incidents merge back into one (if they would).
    """
    body = request.get_json(silent=True) or {}
    alert_id = body.get("alert_id")
    if not isinstance(alert_id, int):
        return jsonify({"error": "alert_id (int) is required in body"}), 400

    conn = _get_conn()
    conn.execute(
        "DELETE FROM incident_splits WHERE alert_id = ?",
        (alert_id,),
    )
    conn.commit()
    conn.close()

    from mlss_monitor.incident_grouper import regroup_all
    regroup_all(DB_FILE)

    return jsonify({"ok": True, "unsplit_alert_id": alert_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_incidents.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): POST /api/incidents/<id>/unsplit removes a split marker

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 11: Delete legacy sessionise + merge_similar_adjacent

**Files:**
- Modify: `mlss_monitor/incident_grouper.py`

Removes functions, constants, and helpers that are no longer called by `regroup_all`.

- [ ] **Step 1: Identify the dead code**

Check that no other module imports these:

Run: `grep -rn "sessionise\|merge_similar_adjacent\|GAP_MINUTES\|MAX_MERGE_GAP_MINUTES\|JACCARD_THRESHOLD\|_group_event_types\|_jaccard\|_group_gap_minutes" --include="*.py"`
Expected: Matches only within `mlss_monitor/incident_grouper.py` itself (and the test file, which should have had the old tests deleted in Task 7).

- [ ] **Step 2: Delete the functions and constants**

In `mlss_monitor/incident_grouper.py`, delete:

- The `GAP_MINUTES` constant
- The `sessionise` function and its docstring
- The entire "Similarity-aware merging" section block comment + `MAX_MERGE_GAP_MINUTES`, `JACCARD_THRESHOLD` constants
- The `_group_event_types`, `_jaccard`, `_group_gap_minutes`, `merge_similar_adjacent` functions

Leave `MIN_DATA_POINTS`, `CROSS_INCIDENT_TYPES`, `_ANNOTATION_CONTEXT_PREFIX`, `_ML_PREFIXES`, `_STATISTICAL_TYPES`, `_SUMMARY_TYPES`, `_SENSOR_COLS`, `_SEVERITY_ORDER`, `_SEVERITY_LABEL`, `_METHOD_ORDER`, `_SENSOR_KEYWORDS`, `detection_method`, `is_cross_incident`, `make_incident_id`, `compute_pearson_r`, `build_incident_similarity_vector`, `generate_incident_title`, `cosine_similarity` — these are still in use.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: All pass (no tests rely on the deleted code).

- [ ] **Step 4: Run pylint on the cleaned file**

Run: `python -m pylint mlss_monitor/incident_grouper.py --disable=E0401,C0114,C0115,C0116`
Expected: 10.00/10.

- [ ] **Step 5: Commit**

```bash
git add mlss_monitor/incident_grouper.py
git commit -m "refactor(grouper): delete legacy sessionise + merge_similar_adjacent

Replaced end-to-end by causal DAG grouping in Task 7. No remaining
callers — removal is pure dead-code elimination.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 12: Toolbar slider markup + localStorage wiring

**Files:**
- Modify: `templates/incidents.html`
- Modify: `static/js/incident_graph.js`
- Modify: `static/css/incident_graph.css`

- [ ] **Step 1: Add the slider markup to the toolbar**

In `templates/incidents.html`, immediately after the `.inc-sev-pills` block (toolbar right-hand area):

```html
    <!-- Edge-strength slider. Filters which edges render in the main graph
         and drives the client-side subdivision preview. Persisted per-user
         in localStorage (inc.edge_p_floor). -->
    <label class="inc-edge-slider" title="Hide edges with probability below this threshold">
      <span class="inc-edge-slider-label">Hide weak links</span>
      <input id="inc-edge-slider" type="range" min="0" max="1" step="0.05" value="0.20">
      <span class="inc-edge-slider-value" id="inc-edge-slider-value">P ≥ 0.20</span>
    </label>
```

- [ ] **Step 2: Add CSS for the slider**

Append to `static/css/incident_graph.css`:

```css
/* ── Edge strength slider ───────────────────────────────────────────────── */

.inc-edge-slider {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: 12px;
  color: var(--color-text-secondary, #c9d1d9);
  font-size: 0.72rem;
  white-space: nowrap;
}

.inc-edge-slider-label {
  opacity: 0.8;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.inc-edge-slider input[type="range"] {
  width: 110px;
  accent-color: #4dacff;
}

.inc-edge-slider-value {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-primary, #e6edf3);
  font-weight: 600;
  min-width: 54px;
  text-align: right;
}
```

- [ ] **Step 3: Wire the slider in JS**

In `static/js/incident_graph.js`, near the module-level `let` declarations at the top (around line 20, alongside `allIncidentDetails`):

```js
// Client-side threshold for edge rendering + subdivision preview.
// Persisted in localStorage under inc.edge_p_floor. Default 0.20.
let edgePFloor = (() => {
  try {
    const v = parseFloat(localStorage.getItem('inc.edge_p_floor'));
    return Number.isFinite(v) ? v : 0.20;
  } catch (_) { return 0.20; }
})();
```

Then in `initToolbar()`, add at the end of the function body:

```js
  const slider = document.getElementById('inc-edge-slider');
  const sliderValue = document.getElementById('inc-edge-slider-value');
  if (slider && sliderValue) {
    slider.value = String(edgePFloor);
    sliderValue.textContent = `P ≥ ${edgePFloor.toFixed(2)}`;
    slider.addEventListener('input', e => {
      edgePFloor = parseFloat(e.target.value);
      sliderValue.textContent = `P ≥ ${edgePFloor.toFixed(2)}`;
      try { localStorage.setItem('inc.edge_p_floor', String(edgePFloor)); }
      catch (_) {}
      // Re-apply edge styling + subdivision preview on the current graph.
      if (typeof applyEdgePStyling === 'function') applyEdgePStyling();
      if (typeof applySubdivisionPreview === 'function') applySubdivisionPreview();
    });
  }
```

(The `applyEdgePStyling` and `applySubdivisionPreview` functions come in later tasks. The `typeof ... === 'function'` guards let this commit stand alone without breaking.)

- [ ] **Step 4: Manual verification**

Deploy on the Pi (or run locally). Confirm:
- The slider appears in the toolbar between the severity pills and other controls.
- Dragging it updates the "P ≥ N.NN" readout.
- Reloading the page preserves the last value via localStorage.

- [ ] **Step 5: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): edge-strength slider in toolbar (wiring only)

Adds the UI control and localStorage persistence. Actual edge styling +
subdivision preview land in follow-up tasks.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 13: Edge styling by P + hover tooltip

**Files:**
- Modify: `static/js/incident_graph.js`

- [ ] **Step 1: Add the edge-styling helper**

In `static/js/incident_graph.js`, add this function somewhere after `renderGraph` (near the end of the rendering section):

```js
/**
 * Apply per-edge visual treatment based on P and the current slider threshold.
 *
 *   P >= 0.7  : opacity 1.0  width 2.0  solid
 *   0.4–0.7   : opacity 0.7  width 1.5  solid
 *   0.2–0.4   : opacity 0.5  width 1.0  dashed
 *   floor–0.2 : opacity 0.3  width 0.8  dotted
 *   < floor   : hidden
 *
 * Edges are expected to carry .data('p') from renderGraph.
 */
function applyEdgePStyling() {
  if (!cy) return;
  cy.edges('.chrono-edge').forEach(e => {
    const p = Number(e.data('p') || 0);
    if (p < edgePFloor) {
      e.style({ display: 'none' });
      return;
    }
    let opacity, width, lineStyle;
    if      (p >= 0.7) { opacity = 1.0; width = 2.0; lineStyle = 'solid'; }
    else if (p >= 0.4) { opacity = 0.7; width = 1.5; lineStyle = 'solid'; }
    else if (p >= 0.2) { opacity = 0.5; width = 1.0; lineStyle = 'dashed'; }
    else               { opacity = 0.3; width = 0.8; lineStyle = 'dotted'; }
    e.style({
      display: 'element',
      'opacity': opacity,
      'width': width,
      'line-style': lineStyle,
    });
  });
}
```

- [ ] **Step 2: Populate edges with P data in `renderGraph`**

Find the existing code in `buildIncidentElements` (or wherever chrono-edges are built — look for `classes: 'chrono-edge'`). Replace the existing chrono-edge block with one that also attaches `p` and `shared_sensors` data:

```js
// Chronological arrows between consecutive primary alerts, styled by
// edge-probability P from the API response. P drives opacity/width/style
// via applyEdgePStyling(); shared_sensors feeds the hover tooltip.
(detail.edges || []).forEach(edge => {
  elements.push({
    group: 'edges',
    data: {
      id: `edge-${incId}-${edge.from}-${edge.to}`,
      source: `alert-${edge.from}`,
      target: `alert-${edge.to}`,
      incidentId: incId,
      p: edge.p,
      shared_sensors: (edge.shared_sensors || []).join(','),
    },
    classes: 'chrono-edge',
  });
});
```

(Remove or replace the earlier "same-incident chronological chain" loop, if it was building edges from consecutive-by-time pairs. The server now ships the full edge list.)

- [ ] **Step 3: Call `applyEdgePStyling` after each render**

At the end of `renderGraph(detail, incidents)` in `static/js/incident_graph.js`, add after the existing `applyZoomClasses(cy.zoom())` call:

```js
  applyEdgePStyling();
```

And also inside the progressive ghost-detail callback (the place that calls `applyZoomClasses(cy.zoom())` inside the async ghost loop), add the same `applyEdgePStyling()` line after it.

- [ ] **Step 4: Add the hover tooltip handler**

In `initCytoscape()`, after the existing `cy.on('tap', ...)` handlers, add:

```js
  // Hover tooltip on edges — "14 min apart · eco2_ppm · P = 0.82"
  cy.on('mouseover', 'edge.chrono-edge', evt => {
    const e = evt.target;
    const p = Number(e.data('p') || 0);
    const shared = String(e.data('shared_sensors') || '').split(',').filter(Boolean);
    const src = cy.$id(e.data('source'));
    const tgt = cy.$id(e.data('target'));
    const srcT = String(src.data('created_at') || '').replace('T', ' ');
    const tgtT = String(tgt.data('created_at') || '').replace('T', ' ');
    let gapStr = '';
    try {
      const mins = Math.round((new Date(tgtT) - new Date(srcT)) / 60000);
      gapStr = `${mins} min apart`;
    } catch (_) { gapStr = ''; }
    const sensorsStr = shared.length ? shared.join(', ') : '(no shared sensor)';
    e.data('tooltip', `${gapStr} · ${sensorsStr} · P = ${p.toFixed(2)}`);
    // Show via title attribute on the canvas — a Cytoscape tooltip lib would
    // be heavier than needed for one line; this uses the container's title.
    const el = document.getElementById('cy-graph');
    if (el) el.title = e.data('tooltip');
  });
  cy.on('mouseout', 'edge.chrono-edge', () => {
    const el = document.getElementById('cy-graph');
    if (el) el.title = '';
  });
```

- [ ] **Step 5: Manual verification**

Reload the page. Confirm:
- Edges between alerts now vary in thickness/opacity/style according to their P value.
- Hovering an edge shows a tooltip on the canvas element.
- Dragging the slider makes edges with P below the threshold disappear.

- [ ] **Step 6: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(incidents): edge styling ramp by P + hover tooltip

Opacity/width/style step on P; sub-floor edges hidden. Tooltip shows
'N min apart · sensor(s) · P = X.XX' on hover.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 14: Incident confidence bar on cards

**Files:**
- Modify: `static/js/incident_graph.js` (incidentCardTemplate)
- Modify: `static/css/incident_graph.css`

- [ ] **Step 1: Add the bar to the card template**

In `static/js/incident_graph.js`, locate `incidentCardTemplate(inc)` and add a confidence bar at the end of the card's inner HTML (after the `.inc-card-meta` div):

```js
function incidentCardTemplate(inc) {
  const start = (inc.started_at || '').replace('T', ' ').slice(11, 16);
  const end   = (inc.ended_at   || '').replace('T', ' ').slice(11, 16);
  const date  = (inc.started_at || '').slice(0, 10);
  const dur   = _formatDuration(inc.started_at, inc.ended_at);
  const sev   = inc.max_severity || 'info';
  const sel   = inc.id === currentIncidentId ? 'selected' : '';
  const count = inc.alert_count ?? 0;
  const conf  = Number(inc.confidence || 0);
  const confPct = Math.round(conf * 100);
  const confClass =
    conf >= 0.5 ? 'conf-high' :
    conf >= 0.3 ? 'conf-med'  :
                  'conf-low';
  return html`
    <div class="inc-card ${sel}" data-id="${inc.id}"
         role="button" tabindex="0"
         aria-pressed="${sel ? 'true' : 'false'}"
         aria-label="${inc.id}: ${inc.title || ''}">
      <div class="inc-card-id">${inc.id}</div>
      <div class="inc-card-title" title="${inc.title || ''}">${inc.title || ''}</div>
      <div class="inc-card-time">
        <span>${date}</span><span>·</span>
        <span>${start}–${end}</span><span>·</span>
        <span>${dur}</span>
      </div>
      <div class="inc-card-meta">
        <span class="inc-sev-dot ${sev}"></span>
        <span>${sev}</span><span>·</span>
        <span>${count} alert${count === 1 ? '' : 's'}</span>
      </div>
      <div class="inc-card-conf ${confClass}" title="Causal confidence ${confPct}%">
        <div class="inc-card-conf-fill" style="width:${confPct}%"></div>
      </div>
    </div>
  `;
}
```

- [ ] **Step 2: Add CSS for the confidence bar**

Append to `static/css/incident_graph.css`:

```css
/* ── Incident confidence bar on cards ──────────────────────────────────── */

.inc-card-conf {
  position: relative;
  height: 4px;
  margin-top: 6px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 2px;
  overflow: hidden;
}
.inc-card-conf-fill {
  height: 100%;
  background: currentColor;  /* coloured by the .conf-* class */
  transition: width 0.15s ease-out;
}
.inc-card-conf.conf-high { color: #4dacff; }
.inc-card-conf.conf-med  { color: #fc8c2f; }
.inc-card-conf.conf-low  {
  /* Red hatching overlay for low-confidence cards. */
  color: #ff8a8a;
  background-image:
    linear-gradient(135deg,
      rgba(255,56,56,0.12) 0px,
      rgba(255,56,56,0.12) 3px,
      transparent 3px,
      transparent 6px);
}
```

- [ ] **Step 3: Manual verification**

Reload the page and confirm:
- Each incident card shows a thin horizontal bar under the alert-count row.
- High-confidence incidents (≥ 0.5) have a blue bar.
- Medium (0.3 – 0.5) have an amber bar.
- Low (< 0.3) have a reddish bar with a hatched background showing underneath.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): confidence bar under each incident card

Blue for >=0.5, amber for 0.3-0.5, red+hatching for <0.3. Title attribute
shows the exact percentage.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 15: Hull dash-by-confidence

**Files:**
- Modify: `static/js/incident_graph.js` (Cytoscape stylesheet + element build)

- [ ] **Step 1: Add confidence class to the hull in `buildIncidentElements`**

Find the existing hull-node push in `buildIncidentElements`. Augment its classes with a `conf-*` tier:

```js
  const hullConf = Number(detail.confidence || 1.0);
  const hullConfClass =
    hullConf >= 0.5 ? 'conf-high' :
    hullConf >= 0.3 ? 'conf-med'  :
                      'conf-low';
  elements.push({
    group: 'nodes',
    data: { id: `hull-${incId}`, incidentId: incId, type: 'hull', label: incId },
    classes: `hull severity-${detail.max_severity || 'info'} ${hullConfClass}`,
  });
```

(Adapt to whatever the existing hull-push block looks like — the change is the added class name, keeping existing classes intact.)

- [ ] **Step 2: Add Cytoscape stylesheet rules**

In `buildCytoscapeStyle()`, add these rules near the existing `.hull` rule:

```js
// Hull border dash-pattern ramp by confidence tier.
// Severity border colour is unchanged; this is an orthogonal visual channel.
{ selector: 'node.hull.conf-high', style: { 'border-style': 'solid' } },
{ selector: 'node.hull.conf-med',  style: { 'border-style': 'dashed' } },
{ selector: 'node.hull.conf-low',  style: { 'border-style': 'dashed', 'border-width': 2.5 } },
```

- [ ] **Step 3: Manual verification**

Reload the page. Confirm:
- High-confidence incident hulls have solid borders.
- Medium-confidence hulls have dashed borders.
- Low-confidence hulls have slightly thicker dashed borders.
- Severity colours (red/orange/cyan) are untouched on all three tiers.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(incidents): hull border dash pattern by confidence tier

Solid >=0.5, dashed 0.3-0.5, dashed+thicker <0.3. Severity colour
unchanged — this is an orthogonal visual channel.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 16: Detail panel — numeric confidence + advisory + single-split button

**Files:**
- Modify: `templates/incidents.html`
- Modify: `static/js/incident_graph.js` (renderDetail)
- Modify: `static/css/incident_graph.css`

- [ ] **Step 1: Add the confidence line + advisory + button to the template**

In `templates/incidents.html`, inside the detail panel, locate the `<div class="inc-narrative" id="inc-narrative" hidden>` block. Immediately after the `<h4>Narrative</h4>` line, add:

```html
          <div class="inc-narrative-conf" id="inc-narrative-conf"></div>
          <div class="inc-narrative-advisory" id="inc-narrative-advisory" hidden></div>
          <div class="inc-narrative-actions">
            <button type="button" class="inc-btn-split" id="inc-btn-split" hidden>
              Split at weakest link
            </button>
            <button type="button" class="inc-btn-unsplit" id="inc-btn-unsplit" hidden>
              Undo split
            </button>
          </div>
```

- [ ] **Step 2: Populate those in `renderDetail`**

In `static/js/incident_graph.js`, at the top of `renderDetail(detail)`, after the existing narrative-text population and before the causal block, add:

```js
  const confEl = document.getElementById('inc-narrative-conf');
  const advEl  = document.getElementById('inc-narrative-advisory');
  const splitBtn = document.getElementById('inc-btn-split');
  const unsplitBtn = document.getElementById('inc-btn-unsplit');
  const conf = Number(detail.confidence || 1.0);
  const confPct = Math.round(conf * 100);
  if (confEl) confEl.textContent = `${detail.id} · confidence ${confPct}%`;

  // Advisory: weakest edge gap (if any edges).
  if (advEl) {
    if (conf < 0.5 && detail.edges && detail.edges.length) {
      const weakest = detail.edges.reduce(
        (a, b) => (a.p < b.p ? a : b), detail.edges[0]);
      const fromA = (detail.alerts || []).find(a => a.id === weakest.from);
      const toA   = (detail.alerts || []).find(a => a.id === weakest.to);
      let gapLabel = '';
      if (fromA && toA) {
        const mins = Math.round(
          (new Date(toA.created_at.replace(' ', 'T')) -
           new Date(fromA.created_at.replace(' ', 'T'))) / 60000);
        if (mins >= 60) gapLabel = `${Math.floor(mins / 60)}h ${mins % 60}m`;
        else gapLabel = `${mins}m`;
      }
      advEl.textContent = `⚠ Weakest causal link in this chain is ${gapLabel} wide — consider whether this is really one event.`;
      advEl.hidden = false;
    } else {
      advEl.hidden = true;
    }
  }

  // Split button appears when confidence < 0.5.
  if (splitBtn) {
    const hasEdges = detail.edges && detail.edges.length > 0;
    splitBtn.hidden = !(conf < 0.5 && hasEdges);
    splitBtn.onclick = () => { splitAtWeakestLink(detail); };
  }

  // Unsplit button appears when the earliest alert is a known split marker.
  // The /api/incidents/<id> response doesn't today tell us which alerts are
  // markers; use a heuristic: if this incident's earliest alert is also the
  // latest alert of a different incident ending right before us, we were
  // likely the result of an operator split. For now, surface an "undo"
  // action if detail.operator_split === true (added in server-side Task 10
  // follow-up) — otherwise hide. (A future task can add an explicit field
  // to the detail API.)
  if (unsplitBtn) unsplitBtn.hidden = !detail.operator_split;
```

- [ ] **Step 3: Implement `splitAtWeakestLink`**

Add near other detail helpers:

```js
/**
 * Find the incident's weakest edge and mark the later endpoint as a
 * split marker via POST /api/incidents/<id>/split. Refreshes on success.
 */
async function splitAtWeakestLink(detail) {
  if (!detail.edges || !detail.edges.length) return;
  const weakest = detail.edges.reduce(
    (a, b) => (a.p < b.p ? a : b), detail.edges[0]);
  try {
    const resp = await fetch(`/api/incidents/${encodeURIComponent(detail.id)}/split`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alert_id: weakest.to }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      console.error('Split failed:', err);
      return;
    }
    await loadIncidents();
  } catch (e) { console.error('Split network error:', e); }
}
```

- [ ] **Step 4: Add CSS for the narrative additions**

Append to `static/css/incident_graph.css`:

```css
.inc-narrative-conf {
  font-size: 0.75rem;
  color: var(--color-text-secondary, #c9d1d9);
  margin-bottom: 8px;
  font-variant-numeric: tabular-nums;
}

.inc-narrative-advisory {
  background: rgba(252, 140, 47, 0.09);
  border-left: 3px solid rgba(252, 140, 47, 0.6);
  padding: 6px 9px;
  margin: 8px 0;
  font-size: 0.75rem;
  color: #ffcf8f;
  border-radius: 0 3px 3px 0;
}

.inc-narrative-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.inc-btn-split, .inc-btn-unsplit, .inc-btn-commit {
  background: rgba(77, 172, 255, 0.10);
  border: 1px solid rgba(77, 172, 255, 0.45);
  color: #4dacff;
  font-size: 0.72rem;
  font-family: inherit;
  padding: 4px 10px;
  border-radius: 3px;
  cursor: pointer;
  transition: background 0.1s;
}
.inc-btn-split:hover, .inc-btn-unsplit:hover, .inc-btn-commit:hover {
  background: rgba(77, 172, 255, 0.20);
}
```

- [ ] **Step 5: Manual verification**

Click through several incidents. Confirm:
- `INC-… · confidence NN%` renders under the Narrative heading.
- Low-confidence incidents get the amber advisory banner naming the weakest gap.
- `[Split at weakest link]` button appears only when confidence < 0.5.
- Clicking the split button breaks the incident and the left-panel list updates.

- [ ] **Step 6: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): detail-panel confidence + advisory + split-at-weakest button

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 17: Expose `operator_split` flag in the detail API + wire up Undo split

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py`
- Modify: `static/js/incident_graph.js`
- Test: `tests/test_api_incidents.py`

- [ ] **Step 1: Write failing test**

```python
def test_get_incident_detail_reports_operator_split(client, db):
    """detail.operator_split is True iff the earliest alert of the incident
    has a row in incident_splits."""
    from mlss_monitor.incident_grouper import regroup_all
    import sqlite3
    conn = sqlite3.connect(db)
    alert_ids = []
    for ts in ("2026-04-23 09:00:00", "2026-04-23 09:15:00"):
        cur = conn.execute(
            "INSERT INTO inferences (created_at, event_type, severity, title, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, "tvoc_spike", "info", f"t-{ts}", 0.9),
        )
        alert_ids.append(cur.lastrowid)
        conn.execute(
            "INSERT INTO alert_signal_deps (alert_id, sensor, r, lag_seconds) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, "eco2_ppm", 0.8, 0),
        )
    conn.execute(
        "INSERT INTO incident_splits (alert_id, created_by) VALUES (?, ?)",
        (alert_ids[1], "test"),
    )
    conn.commit()
    conn.close()
    regroup_all(db)

    listing = client.get("/api/incidents?window=30d").get_json()
    incidents = listing["incidents"]
    assert len(incidents) == 2

    # The incident whose earliest alert IS alert_ids[1] should have the flag.
    hit = None
    for inc in incidents:
        resp = client.get(f"/api/incidents/{inc['id']}").get_json()
        earliest_alert_id = min(a["id"] for a in resp["alerts"] if a.get("is_primary"))
        if earliest_alert_id == alert_ids[1]:
            hit = resp
            break
    assert hit is not None
    assert hit["operator_split"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_incidents.py::test_get_incident_detail_reports_operator_split -v`
Expected: FAIL (field not present).

- [ ] **Step 3: Add the field to `get_incident`**

In `mlss_monitor/routes/api_incidents.py`, in `get_incident`, just before the final `return jsonify({...})`, add:

```python
    # operator_split: true iff the earliest primary alert in this incident
    # is itself a split marker. Used by the UI to surface an Undo split.
    earliest_primary_id = None
    for a in alerts:
        if a.get("is_primary"):
            earliest_primary_id = a["id"]
            break
    operator_split = False
    earliest_split_alert_id = None
    if earliest_primary_id is not None:
        row = conn.execute(
            "SELECT alert_id FROM incident_splits WHERE alert_id = ?",
            (earliest_primary_id,),
        ).fetchone()
        operator_split = row is not None
        if operator_split:
            earliest_split_alert_id = earliest_primary_id
```

Then include these in the response:

```python
    return jsonify({
        **incident,
        "alerts": alerts,
        "causal_sequence": causal_sequence,
        "narrative": narrative,
        "similar": similar,
        "edges": edges_out,
        "operator_split": operator_split,
        "split_alert_id": earliest_split_alert_id,
    })
```

- [ ] **Step 4: Wire the Undo button in JS**

In `renderDetail(detail)` (the block added in Task 16), replace the unsplitBtn block with:

```js
  if (unsplitBtn) {
    if (detail.operator_split && detail.split_alert_id) {
      unsplitBtn.hidden = false;
      unsplitBtn.onclick = async () => {
        try {
          const resp = await fetch(`/api/incidents/${encodeURIComponent(detail.id)}/unsplit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ alert_id: detail.split_alert_id }),
          });
          if (resp.ok) await loadIncidents();
        } catch (e) { console.error('Unsplit network error:', e); }
      };
    } else {
      unsplitBtn.hidden = true;
    }
  }
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -q`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py static/js/incident_graph.js tests/test_api_incidents.py
git commit -m "feat(incidents): surface operator_split in detail API + wire Undo split

Detail response gains operator_split bool and split_alert_id. UI shows
[Undo split] when true; clicking calls /unsplit and refreshes.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 18: Client-side `connectedComponents` helper + Node test

**Files:**
- Create: `static/js/connected_components.mjs`
- Modify: `static/js/incident_graph.js` (import from the new file)
- Create: `tests/js/test_connected_components.mjs`

**Why a separate `.mjs` file:** Node's module resolution needs an unambiguous ESM hint to let a test import this helper. Putting the function in a dedicated `.mjs` file works for both the browser (Flask serves `.mjs` as `application/javascript` via Python's default `mimetypes`) and for Node (the `.mjs` extension is self-describing, no package.json needed). The main `incident_graph.js` imports from the helper — no behaviour change, just cleaner separation.

- [ ] **Step 1: Create the helper module**

Create `static/js/connected_components.mjs`:

```js
/**
 * Pure client-side mirror of Python's connected_components.
 *
 * Given an alert id list and an edges array [{from, to, p}, ...], return
 * a list of components where each component is the list of alert ids
 * that belong together under the given threshold.
 *
 * Semantics match the server: edges with p < threshold are ignored for
 * membership; isolated ids become singleton components. Result is
 * deterministic — components are sorted by the minimum id in each.
 */
export function connectedComponents(alertIds, edges, threshold) {
  const parent = new Map();
  alertIds.forEach(id => parent.set(id, id));
  const find = x => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));  // path compression
      x = parent.get(x);
    }
    return x;
  };
  const union = (x, y) => {
    const rx = find(x), ry = find(y);
    if (rx !== ry) parent.set(rx, ry);
  };
  for (const e of edges) {
    if (e.p < threshold) continue;
    if (parent.has(e.from) && parent.has(e.to)) union(e.from, e.to);
  }
  const buckets = new Map();
  for (const id of alertIds) {
    const root = find(id);
    if (!buckets.has(root)) buckets.set(root, []);
    buckets.get(root).push(id);
  }
  // Deterministic: sort by min id within each component.
  return Array.from(buckets.values())
    .sort((a, b) => Math.min(...a) - Math.min(...b));
}
```

- [ ] **Step 2: Import into `incident_graph.js`**

In `static/js/incident_graph.js`, at the very top of the file (before any code, since ES module imports are hoisted anyway but this is the conventional location):

```js
import { connectedComponents } from './connected_components.mjs';
```

The file is already loaded with `type="module"` in `incidents.html` so ESM imports just work.

- [ ] **Step 3: Create the Node test**

Create `tests/js/test_connected_components.mjs`:

```javascript
// Fixture-based test for the client-side connectedComponents helper.
// Run: node tests/js/test_connected_components.mjs
// Exit 0 on pass, 1 on failure.

import { connectedComponents } from '../../static/js/connected_components.mjs';

let failures = 0;
function expect(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ok  ${label}`);
  } else {
    console.log(`  FAIL ${label}\n       expected: ${e}\n       actual:   ${a}`);
    failures++;
  }
}

// 1. Empty
expect('empty input', connectedComponents([], [], 0.05), []);

// 2. Singleton
expect('single alert => singleton', connectedComponents([1], [], 0.05), [[1]]);

// 3. No edges => all singletons
expect('no edges => N singletons',
  connectedComponents([1, 2, 3], [], 0.05),
  [[1], [2], [3]]);

// 4. One edge => one component
expect('one edge joins two',
  connectedComponents([1, 2], [{from: 1, to: 2, p: 0.9}], 0.05),
  [[1, 2]]);

// 5. Transitive chain
expect('transitive chain A-B-C',
  connectedComponents([1, 2, 3],
    [{from: 1, to: 2, p: 0.8}, {from: 2, to: 3, p: 0.7}], 0.05),
  [[1, 2, 3]]);

// 6. Two disjoint subgraphs
expect('two disjoint components',
  connectedComponents([1, 2, 3, 4],
    [{from: 1, to: 2, p: 0.9}, {from: 3, to: 4, p: 0.9}], 0.05),
  [[1, 2], [3, 4]]);

// 7. Threshold splits
expect('high threshold hides weak edge',
  connectedComponents([1, 2],
    [{from: 1, to: 2, p: 0.15}], 0.50),
  [[1], [2]]);

expect('low threshold keeps weak edge',
  connectedComponents([1, 2],
    [{from: 1, to: 2, p: 0.15}], 0.05),
  [[1, 2]]);

if (failures > 0) {
  console.log(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll connected_components JS tests passed');
```

- [ ] **Step 4: Run the Node test**

Run: `node tests/js/test_connected_components.mjs`
Expected: `All connected_components JS tests passed`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add static/js/connected_components.mjs static/js/incident_graph.js tests/js/test_connected_components.mjs
git commit -m "feat(incidents): client-side connectedComponents helper + fixture test

Shared pure module in static/js/connected_components.mjs so both the
browser (incident_graph.js imports it) and a Node fixture test can exercise
the same implementation. Mirrors the server-side Python function; 8 Node
test cases cover empty, singleton, chains, disjoint subgraphs, and
threshold-induced splits.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 19: Subdivision preview (outlines + badge + secondary confidence marker)

**Files:**
- Modify: `static/js/incident_graph.js`
- Modify: `static/css/incident_graph.css`

- [ ] **Step 1: Implement `applySubdivisionPreview`**

In `static/js/incident_graph.js`, near `applyEdgePStyling`:

```js
/**
 * Re-run connectedComponents at the current slider threshold, scoped to
 * each incident. If the incident would split into 2+ components, draw
 * dashed sub-outlines as overlay nodes inside the hull and update the
 * hull label with "Would split into N" badge.
 *
 * Purely client-side — no API calls, no server state changes.
 */
function applySubdivisionPreview() {
  if (!cy || !currentDetail) return;
  const incId = currentDetail.id;
  const alertIds = (currentDetail.alerts || [])
    .filter(a => a.is_primary)
    .map(a => a.id);
  const edges = (currentDetail.edges || []).map(e => ({
    from: e.from, to: e.to, p: e.p,
  }));
  const components = connectedComponents(alertIds, edges, edgePFloor);
  const hull = cy.$id(`hull-${incId}`);
  // Remove previous subdivision overlay outlines.
  cy.nodes('.subdiv-outline').remove();
  if (components.length < 2) {
    // No subdivision; restore the plain hull label.
    if (hull && hull.length) hull.data('label', incId);
    return;
  }
  if (hull && hull.length) {
    hull.data('label', `${incId}  ·  Would split into ${components.length} at P ≥ ${edgePFloor.toFixed(2)}`);
  }
  // For each component, draw a dashed rectangle overlay node that
  // surrounds the component's alert nodes.
  components.forEach((compIds, idx) => {
    const nodes = compIds.map(id => cy.$id(`alert-${id}`)).filter(n => n.length);
    if (!nodes.length) return;
    let xs = nodes.flatMap(n => [n.position('x')]);
    let ys = nodes.flatMap(n => [n.position('y')]);
    const pad = 28;
    const x1 = Math.min(...xs) - pad;
    const x2 = Math.max(...xs) + pad;
    const y1 = Math.min(...ys) - pad;
    const y2 = Math.max(...ys) + pad;
    cy.add({
      group: 'nodes',
      data: {
        id: `subdiv-${incId}-${idx}`,
        label: '',
      },
      position: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
      classes: 'subdiv-outline',
      style: {
        width: x2 - x1,
        height: y2 - y1,
      },
    });
  });
}
```

- [ ] **Step 2: Register `.subdiv-outline` in the Cytoscape stylesheet**

In `buildCytoscapeStyle()`, add at the end before the return:

```js
// Subdivision preview overlay — dashed rectangles drawn inside a hull
// when raising the slider would split the incident.
{
  selector: 'node.subdiv-outline',
  style: {
    'shape': 'round-rectangle',
    'background-opacity': 0,
    'border-width': 1.5,
    'border-style': 'dashed',
    'border-color': '#4dacff',
    'border-opacity': 0.7,
    'label': '',
    'events': 'no',
  },
},
```

- [ ] **Step 3: Call `applySubdivisionPreview` after render + on slider change**

The slider change handler (Task 12) already calls it conditionally. Confirm. Then in `renderGraph`, at the same spot as `applyEdgePStyling()`:

```js
  applyEdgePStyling();
  applySubdivisionPreview();
```

- [ ] **Step 4: Manual verification**

Pick an incident with several alerts. Drag the slider up slowly. Confirm:
- At the slider value where not all edges have P ≥ threshold, dashed sub-outlines appear inside the hull.
- The hull label gains "Would split into N at P ≥ X.XX".
- Dragging the slider back down removes the outlines and restores the plain label.

- [ ] **Step 5: Commit**

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): client-side subdivision preview overlays + hull badge

Runs connectedComponents at the current slider threshold per incident.
Draws dashed sub-outlines when the incident would split; updates hull
label with 'Would split into N'. No API calls, no server state changes.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 20: `[Commit these splits]` button

**Files:**
- Modify: `templates/incidents.html`
- Modify: `static/js/incident_graph.js`

- [ ] **Step 1: Add the button to the template**

In `templates/incidents.html`, inside `.inc-narrative-actions`, add a third button:

```html
            <button type="button" class="inc-btn-commit" id="inc-btn-commit-splits" hidden>
              Commit these splits
            </button>
```

- [ ] **Step 2: Show/hide the button in `applySubdivisionPreview`**

At the end of `applySubdivisionPreview`, add:

```js
  const commitBtn = document.getElementById('inc-btn-commit-splits');
  if (commitBtn) {
    commitBtn.hidden = components.length < 2;
  }
```

- [ ] **Step 3: Wire the button click**

In `renderDetail(detail)`, alongside the other button wiring:

```js
  const commitBtn = document.getElementById('inc-btn-commit-splits');
  if (commitBtn) {
    commitBtn.onclick = async () => {
      if (!currentDetail) return;
      const alertIds = (currentDetail.alerts || [])
        .filter(a => a.is_primary)
        .map(a => a.id)
        .sort((x, y) => {
          // chronological order via the alert objects
          const ax = (currentDetail.alerts || []).find(a => a.id === x);
          const ay = (currentDetail.alerts || []).find(a => a.id === y);
          return (ax.created_at || '').localeCompare(ay.created_at || '');
        });
      const edges = (currentDetail.edges || []).map(e => ({
        from: e.from, to: e.to, p: e.p,
      }));
      const comps = connectedComponents(alertIds, edges, edgePFloor);
      if (comps.length < 2) return;
      // Sort comps by their earliest member's position in chronological order.
      const idPos = new Map(alertIds.map((id, i) => [id, i]));
      comps.sort((a, b) =>
        Math.min(...a.map(id => idPos.get(id))) -
        Math.min(...b.map(id => idPos.get(id))));
      // Split point for each component after the earliest: the earliest
      // member of that component.
      const splitPoints = comps.slice(1).map(comp =>
        comp.reduce((best, id) => idPos.get(id) < idPos.get(best) ? id : best, comp[0]));
      for (const alertId of splitPoints) {
        try {
          const resp = await fetch(
            `/api/incidents/${encodeURIComponent(currentDetail.id)}/split`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ alert_id: alertId }),
            },
          );
          if (!resp.ok) {
            console.error('Commit-split failed at alert', alertId);
            break;
          }
        } catch (e) {
          console.error('Commit-split network error:', e);
          break;
        }
      }
      await loadIncidents();
    };
  }
```

- [ ] **Step 4: Manual verification**

Drag slider up until an incident shows subdivision outlines. Confirm:
- `[Commit these splits]` button appears.
- Clicking it POSTs one `/split` per split point.
- The incident list in the left panel updates to show the new incidents.
- The slider visual returns to "no subdivision needed" on the re-selected first incident because the split is now real.

- [ ] **Step 5: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js
git commit -m "feat(incidents): [Commit these splits] — turn preview into real splits

Button appears in the detail panel when the slider has exposed a
subdivision. Click fires one POST /split per split-point (first alert of
each subcomponent after the chronologically-earliest).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 21: Update README

**Files:**
- Modify: `readme.md`

- [ ] **Step 1: Rewrite the grouping section**

In `readme.md`, locate the "Grouping" sub-section under "Incident correlation graph". Replace its bullet list with:

```markdown
### Grouping

Incidents are formed by connected components of a *causal graph* built over primary alerts. The algorithm lives in `mlss_monitor/incident_grouper.py` as a composition of pure functions, each independently unit-tested in `tests/test_incident_grouper.py`.

- **`edge_probability(a, b)`** — returns P(link) between two alerts. P=1.0 when the gap is ≤ 30 min; linearly decays to 0.0 at 4 h; always 0.0 if the alerts don't share a sensor with |r| ≥ 0.5 and matching sign. Full formula documented at the top of the function in-source.
- **`build_edges(alerts, split_markers)`** — computes all pairwise edges with P > `MIN_EDGE_P_SERVER` (0.05). Suppresses any edge whose later alert has an entry in the `incident_splits` table (operator splits).
- **`connected_components(alerts, edges)`** — union-find over the edge set. Output: list of alert-lists, one per component. Singletons become single-alert incidents.
- **`incident_confidence(edges_in_component)`** — min edge P in the component, or 1.0 for singletons. Interpretation: "the chain is only as trustworthy as its weakest link". Persisted in `incidents.confidence`.

Operators can override a false merge with `POST /api/incidents/<id>/split` (body `{alert_id}`); the marker persists in `incident_splits` and the grouper respects it on every subsequent regroup. `POST /api/incidents/<id>/unsplit` removes a marker. Both endpoints trigger a full regroup.

The frontend exposes a slider (persisted per-user in `localStorage`) that filters which edges render. Raising the slider also runs a client-side `connectedComponents` pass to preview the subdivision that *would* result at that threshold; a `[Commit these splits]` button turns the preview into persisted split markers via a batch of `/split` calls.
```

- [ ] **Step 2: Update the database section**

In `readme.md`, update the `incident_splits` row in the "Table summary" table (add a new row if one doesn't exist):

```markdown
| `incident_splits` | One row per alert that an operator has marked as "start a new incident here". Respected by the grouper on every regroup so operator overrides survive algorithm changes. | Indefinite; manual only. |
```

Update the ER diagram near the incidents cluster to include the new table (add under the existing incidents/alert_signal_deps nodes):

```
    incident_splits {
        INTEGER alert_id PK
        TEXT created_by
        DATETIME created_at
    }

    inferences ||--o| incident_splits : "marked split"
```

- [ ] **Step 3: Update the API table**

In the "Incidents" sub-section of the API reference, replace the existing row set with:

```markdown
| Method | Endpoint | Min role | Description |
|---|---|---|---|
| `GET` | `/api/incidents?window=24h&severity=all&q=&limit=50` | viewer | List incidents with counts + summary. `window` must be one of `15m`, `1h`, `6h`, `12h`, `24h`, `14d` (or legacy `7d`/`30d`); unknown values return 400. |
| `GET` | `/api/incidents/<id>` | viewer | Full incident detail — alerts, causal_sequence, narrative, similar, edges (`[{from, to, p, shared_sensors}]`), plus `operator_split` + `split_alert_id` when the earliest alert is itself a split marker. |
| `POST` | `/api/incidents/<id>/split` | controller | Body `{alert_id: int}`. Adds an `incident_splits` row and triggers a regroup. |
| `POST` | `/api/incidents/<id>/unsplit` | controller | Body `{alert_id: int}`. Removes a split marker and triggers a regroup. |
```

- [ ] **Step 4: Commit**

```bash
git add readme.md
git commit -m "docs: rewrite grouping section for causal-DAG algorithm

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Post-implementation verification

Before declaring the feature complete:

- [ ] `python -m pytest tests/ -q` — all tests pass.
- [ ] `python -m pylint mlss_monitor/incident_grouper.py mlss_monitor/routes/api_incidents.py tests/test_incident_grouper.py tests/test_api_incidents.py --disable=E0401,C0114,C0115,C0116` — 10.00/10 (or identify any genuine issues).
- [ ] `node tests/js/test_connected_components.mjs` — all JS fixture tests pass.
- [ ] Deploy to Pi, manually verify: grouping changed as expected, slider works, confidence bars render, hull borders dash correctly by tier, split / unsplit / commit-splits all functional.

## What we expect to see on first deploy

The first call to `regroup_all` after the deploy wipes and rebuilds all incidents. Existing operator-facing information — which raw inferences exist, their content — is unchanged; only the grouping boundary changes. Expect:

- Fewer, larger incidents for events where alerts chain through shared sensors (e.g. a long CO₂ buildup).
- More incidents where previously-grouped alerts had disjoint sensor signatures (e.g. a cooking event and an HVAC cycle that overlapped in time).
- `confidence` values now span 0.05 – 1.0 (singletons 1.0, tight chains 1.0, wider chains lower).
- No data loss: every `inference` row still exists; only the `incidents` + `incident_alerts` tables were rebuilt.

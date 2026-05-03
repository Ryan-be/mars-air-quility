# Incidents Tab Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `/inferences` 404 bug and close the gap between what the Incidents tab *tries* to illustrate (a timestamped forensic story of correlated sensor alerts) and what it currently shows (a generic hub-and-spoke graph with template text).

**Architecture:** Changes are split into three phases that can each be stopped cleanly:
- **Phase 0** — one critical bug fix (broken "View full inference" link).
- **Phase 1** — content & polish improvements inside the existing visual structure: prose narrative, timestamped causal chips, severity counts, card metadata, node collapse. Low risk, high readability win.
- **Phase 2** — structural redesign: timeline layout within clusters, cross-incident band, summary strip, similarity explanation. Each task is independently valuable if Phase 2 is only partially completed.

All logic changes are covered by unit tests. Pure functions live in `mlss_monitor/incidents_narrative.py` so they can be tested without a Flask/SQLite fixture.

**Tech Stack:**
- Backend: Python 3.11+, Flask, SQLite, stdlib `datetime`/`statistics`
- Frontend: Vanilla JS module, Cytoscape.js v3, AstroUXDS web components
- Tests: pytest

---

## File Structure

### New files

- `mlss_monitor/incidents_narrative.py` — Pure functions that convert an alert list into `{observed, inferred, impact}` English prose strings with real timestamps and values. No DB access. No Flask. Only stdlib + the types it's passed.
- `tests/test_incidents_narrative.py` — Unit tests for the new module.

### Modified files

- `mlss_monitor/routes/api_incidents.py` — Delete inline `_build_narrative()`. Call the new module. Add severity-count aggregation to the list endpoint.
- `tests/test_api_incidents.py` — Update narrative assertions to match new prose shape. Add assertions for severity counts in list response.
- `static/js/incident_graph.js` — UI polish (chips with timestamps, severity pills, card time ranges, ghost summary labels, node collapse, expanded node overlay).
- `static/css/incident_graph.css` — Styles for all new UI elements.
- `templates/incidents.html` — Remove broken inference link. Add severity pills container to toolbar. Add summary strip container (Task 2.3).

---

## Phase 0 — Critical Bug Fix

### Task 0.1: Remove broken "View full inference" link, expand node overlay inline

**Context:** Clicking an alert node shows an overlay in the right panel with a link `View full inference →` that points to `/inferences?id=N`. That page route does not exist — it returns 404. The alert detail is already fetched via `/api/incidents/<id>/alert/<aid>` and rendered in a table. Fix by removing the link entirely and expanding the inline view to show everything.

**Files:**
- Modify: `templates/incidents.html:99-104`
- Modify: `static/js/incident_graph.js` (constants around lines 35–38 and `renderAlertTable` + `showNodeOverlay`)
- Modify: `static/css/incident_graph.css` (overlay styles)

---

- [ ] **Step 1: Remove the anchor element from the template**

In `templates/incidents.html`, find the node overlay section (around line 100) and replace:
```html
<div class="inc-node-overlay-header">
  <span id="inc-node-title"></span>
  <a id="inc-node-view-link" href="#" class="inc-node-view-btn">View full inference →</a>
</div>
<div id="inc-node-body"></div>
```

with:
```html
<div class="inc-node-overlay-header">
  <span id="inc-node-title"></span>
  <button type="button" id="inc-node-close" class="inc-node-close-btn" aria-label="Close">×</button>
</div>
<div id="inc-node-body"></div>
```

- [ ] **Step 2: Remove `elNodeLink` reference and wire up the close button in JS**

In `static/js/incident_graph.js`, delete the `elNodeLink` constant (was around line 37):
```js
const elNodeLink    = document.getElementById('inc-node-view-link');
```

Replace it with:
```js
const elNodeClose   = document.getElementById('inc-node-close');
```

Then, inside the `DOMContentLoaded` handler (near `initToolbar()`), add:
```js
if (elNodeClose) {
  elNodeClose.addEventListener('click', () => {
    if (elNodeOverlay) elNodeOverlay.hidden = true;
  });
}
```

And inside `showNodeOverlay()`, delete the line:
```js
if (elNodeLink) elNodeLink.href = `/inferences?id=${alert.id}`;
```

- [ ] **Step 3: Source alert detail from `currentDetail` instead of the alert endpoint**

`currentDetail.alerts` (fetched by `selectIncident`) already carries every field we need *plus* `signal_deps`, which the per-alert endpoint omits. Use the cached copy and skip the extra fetch.

Replace the entire body of `showNodeOverlay(nodeData)` with:
```js
async function showNodeOverlay(nodeData) {
  if (!elNodeOverlay) return;
  if (elNodeTitle) elNodeTitle.textContent = nodeData.title || nodeData.id;
  elNodeOverlay.hidden = false;

  if (!(nodeData.type === 'alert' && nodeData.alertId && currentDetail)) {
    if (elNodeBody) elNodeBody.innerHTML = '';
    return;
  }

  const alert = (currentDetail.alerts || []).find(a => a.id === nodeData.alertId);
  if (!alert) {
    if (elNodeBody) elNodeBody.textContent = 'Alert not found in current incident.';
    return;
  }
  if (elNodeBody) elNodeBody.innerHTML = renderAlertTable(alert);
}
```

- [ ] **Step 4: Expand `renderAlertTable()` to show every useful alert field + correlation deps**

Replace the entire `renderAlertTable(alert)` function with:
```js
function renderAlertTable(alert) {
  const pct = (x) => `${((x || 0) * 100).toFixed(0)}%`;
  const ts  = (s) => (s || '').replace('T', ' ').slice(0, 19);
  const rows = [
    ['ID',         `#${alert.id}`],
    ['Time',       escHtml(ts(alert.created_at))],
    ['Type',       escHtml(alert.event_type || '')],
    ['Severity',   escHtml(alert.severity || '')],
    ['Method',     escHtml(alert.detection_method || '')],
    ['Confidence', pct(alert.confidence)],
  ];
  if (alert.description) {
    rows.push(['Detail', escHtml(alert.description)]);
  }
  // Signal correlations (Pearson r per sensor) — only show |r| >= 0.3
  const strongDeps = (alert.signal_deps || [])
    .filter(d => d.r !== null && Math.abs(d.r) >= 0.3)
    .sort((a, b) => Math.abs(b.r) - Math.abs(a.r))
    .slice(0, 6);
  if (strongDeps.length) {
    const depsHtml = strongDeps.map(d => {
      const sign = d.r >= 0 ? '+' : '';
      const colour = d.r >= 0 ? '#4dacff' : '#ff8a8a';
      return `<div class="dep-row"><span>${escHtml(d.sensor)}</span><span style="color:${colour}">r = ${sign}${d.r.toFixed(2)}</span></div>`;
    }).join('');
    rows.push(['Correlates', `<div class="evidence-block">${depsHtml}</div>`]);
  }
  return '<table>' + rows.map(([k, v]) =>
    `<tr><td>${k}</td><td>${v}</td></tr>`
  ).join('') + '</table>';
}
```

Note: `alert.signal_deps` comes from `/api/incidents/<id>` (already populated in `currentDetail`). This replaces the role the removed dep-edges played in Task 1.8 — correlation data surfaces in the overlay now instead of as edges.

- [ ] **Step 5: Add CSS for the close button, correlation block, and dep rows**

Append to `static/css/incident_graph.css`:
```css
.inc-node-close-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}
.inc-node-close-btn:hover { color: var(--text); }

.evidence-block {
  max-height: 140px;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.7rem;
  background: rgba(13,17,23,0.5);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 4px 6px;
}

.dep-row {
  display: flex;
  justify-content: space-between;
  padding: 1px 0;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.dep-row span:first-child { color: var(--text-muted); }
```

- [ ] **Step 6: Verify manually in browser**

Expected: clicking a non-ghost alert node opens the overlay with all fields visible (id, time, type, severity, method, confidence, detail, correlates). No `View full inference →` link. The `×` button closes the overlay. DevTools Network tab shows NO request to `/api/incidents/*/alert/*` (the overlay now uses cached data) and no 404s.

- [ ] **Step 7: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js static/css/incident_graph.css
git commit -m "fix(incidents): remove broken /inferences link, expand inline overlay

- /inferences page route does not exist; the link returned 404
- Replace the anchor with a × close button
- Source alert detail from currentDetail.alerts instead of refetching per-
  alert — the cached object already has signal_deps which the /alert endpoint
  omits
- Expand renderAlertTable to show id, timestamp, type, severity, method,
  confidence, description, and strongest Pearson r correlations per sensor
  (coloured blue/red by sign)
- No more network request to the missing route

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Phase 1 — Narrative and polish (big-win quick fixes)

### Task 1.1: Scaffold the narrative module with failing tests

**Context:** The current narrative is generated inline in `api_incidents.py:_build_narrative()` and produces placeholder text. We'll extract narrative logic to a pure module that takes a list of alert dicts and returns three English-prose strings. Pure functions make TDD trivial and keep Flask/SQLite out of the test.

**Files:**
- Create: `mlss_monitor/incidents_narrative.py`
- Create: `tests/test_incidents_narrative.py`

---

- [ ] **Step 1: Create the empty module with the public signature**

Create `mlss_monitor/incidents_narrative.py`:
```python
"""Generate English-prose incident narratives from alert sequences.

Pure functions only — no DB, no Flask. Given a list of alert dicts and the
incident record, return ``{observed, inferred, impact}`` strings suitable for
direct rendering in the UI.

The narrative is deliberately *timestamped* and references specific events
rather than emitting template text like "N event(s) detected".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["build_narrative"]


def build_narrative(incident: dict[str, Any], alerts: list[dict[str, Any]]) -> dict[str, str]:
    """Return {observed, inferred, impact} for the given incident + alerts.

    ``alerts`` are expected to come from the API with keys:
    id, created_at, event_type, severity, title, description, confidence,
    detection_method, is_primary, signal_deps.
    """
    return {"observed": "", "inferred": "", "impact": ""}
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_incidents_narrative.py`:
```python
"""Unit tests for mlss_monitor.incidents_narrative."""
from mlss_monitor.incidents_narrative import build_narrative


def _alert(**over):
    base = {
        "id": 1,
        "created_at": "2026-04-23 09:28:00",
        "event_type": "eco2_elevated",
        "severity": "warning",
        "title": "CO\u2082 elevated",
        "description": "CO\u2082 at 994 ppm",
        "confidence": 0.92,
        "detection_method": "threshold",
        "is_primary": 1,
        "signal_deps": [],
    }
    base.update(over)
    return base


def test_observed_references_first_alert_and_duration():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [_alert()]
    out = build_narrative(inc, alerts)
    assert "09:28" in out["observed"]
    assert "32 min" in out["observed"] or "32min" in out["observed"]


def test_inferred_mentions_specific_event_types_not_placeholder():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, event_type="eco2_elevated", title="CO\u2082 elevated — 994 ppm"),
        _alert(id=2, event_type="anomaly_tvoc_ppb", created_at="2026-04-23 09:36:00",
               title="Anomaly: TVOC", severity="info", detection_method="ml"),
    ]
    out = build_narrative(inc, alerts)
    # Should mention CO2 by name (not generic "N events")
    assert "CO" in out["inferred"] or "994" in out["inferred"] or "TVOC" in out["inferred"]
    # Should NOT use the template wording
    assert "Dominant detection type" not in out["inferred"]


def test_inferred_mentions_time_gap_between_events():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, created_at="2026-04-23 09:28:00", event_type="eco2_elevated",
               title="CO\u2082 elevated"),
        _alert(id=2, created_at="2026-04-23 09:36:00", event_type="anomaly_tvoc_ppb",
               title="TVOC anomaly"),
    ]
    out = build_narrative(inc, alerts)
    # 8 minutes between the two events
    assert "8 min" in out["inferred"] or "8min" in out["inferred"]


def test_impact_reflects_max_severity():
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [_alert(severity="critical")]
    out = build_narrative(inc, alerts)
    assert out["impact"] != ""
    assert "critical" in out["impact"].lower() or "immediate" in out["impact"].lower()


def test_empty_alerts_returns_safe_strings():
    inc = {"id": "INC-EMPTY", "started_at": "", "ended_at": ""}
    out = build_narrative(inc, [])
    assert isinstance(out["observed"], str)
    assert isinstance(out["inferred"], str)
    assert isinstance(out["impact"], str)


def test_primary_alerts_only_in_prose():
    """Cross-incident alerts (is_primary=0) should not dominate the narrative."""
    inc = {"id": "INC-1", "started_at": "2026-04-23 09:28:00", "ended_at": "2026-04-23 10:00:00"}
    alerts = [
        _alert(id=1, event_type="eco2_elevated", title="CO\u2082 elevated", is_primary=1),
        _alert(id=2, event_type="hourly_summary", title="Hourly summary", is_primary=0),
    ]
    out = build_narrative(inc, alerts)
    assert "CO" in out["inferred"] or "994" in out["inferred"]
    assert "hourly" not in out["inferred"].lower()
```

- [ ] **Step 3: Run tests to confirm they fail**

Run: `pytest tests/test_incidents_narrative.py -v`
Expected: 6 tests, all FAILED (function returns empty strings).

- [ ] **Step 4: Commit (red step)**

```bash
git add mlss_monitor/incidents_narrative.py tests/test_incidents_narrative.py
git commit -m "test(incidents): red tests for prose narrative builder

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.2: Implement the prose narrative

**Files:**
- Modify: `mlss_monitor/incidents_narrative.py`
- Test: `tests/test_incidents_narrative.py` (already written in Task 1.1)

---

- [ ] **Step 1: Implement `build_narrative` to satisfy the failing tests**

Replace the body of `mlss_monitor/incidents_narrative.py` with:
```python
"""Generate English-prose incident narratives from alert sequences.

Pure functions only — no DB, no Flask. Given a list of alert dicts and the
incident record, return ``{observed, inferred, impact}`` strings suitable for
direct rendering in the UI.

The narrative is deliberately *timestamped* and references specific events
rather than emitting template text like "N event(s) detected".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["build_narrative"]

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

_IMPACT_BY_SEV = {
    "critical": "Immediate attention required — critical air-quality event.",
    "warning":  "Elevated readings — monitor conditions and consider ventilation.",
    "info":     "Informational event — conditions within acceptable range.",
}


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("T", " "))
    except ValueError:
        return None


def _fmt_hhmm(s: str) -> str:
    dt = _parse_ts(s)
    return dt.strftime("%H:%M") if dt else ""


def _minutes_between(a: str, b: str) -> int | None:
    da, db = _parse_ts(a), _parse_ts(b)
    if not da or not db:
        return None
    return int((db - da).total_seconds() / 60)


def _primary(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in alerts if a.get("is_primary")]


def build_narrative(
    incident: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> dict[str, str]:
    """Return {observed, inferred, impact} for the given incident + alerts."""
    if not alerts:
        return {
            "observed": "No events recorded for this incident.",
            "inferred": "",
            "impact": "",
        }

    primary = _primary(alerts) or alerts
    primary = sorted(primary, key=lambda a: a.get("created_at", ""))
    first = primary[0]
    last = primary[-1]

    duration_min = _minutes_between(
        incident.get("started_at", "") or first.get("created_at", ""),
        incident.get("ended_at", "") or last.get("created_at", ""),
    )

    # ── Observed ───────────────────────────────────────────────────────
    start_hhmm = _fmt_hhmm(incident.get("started_at", "") or first.get("created_at", ""))
    if duration_min is None or duration_min <= 0:
        observed = f"Event recorded at {start_hhmm}."
    else:
        observed = (
            f"{len(primary)} correlated event(s) starting {start_hhmm}, "
            f"spanning {duration_min} min."
        )

    # ── Inferred — name the first two events and the gap between them ─
    def _describe(a: dict[str, Any]) -> str:
        return (a.get("title") or a.get("event_type") or "event").strip()

    parts: list[str] = []
    parts.append(f"{_describe(first)} at {_fmt_hhmm(first.get('created_at', ''))}.")
    if len(primary) >= 2:
        gap = _minutes_between(first.get("created_at", ""), primary[1].get("created_at", ""))
        gap_phrase = f"{gap} min later" if gap is not None and gap > 0 else "Concurrently"
        parts.append(f"{gap_phrase}, {_describe(primary[1])}.")
    if len(primary) >= 3:
        tail_count = len(primary) - 2
        parts.append(f"{tail_count} further event(s) through {_fmt_hhmm(last.get('created_at', ''))}.")

    inferred = " ".join(parts)

    # ── Impact — map from max severity ────────────────────────────────
    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="info",
    )
    impact = _IMPACT_BY_SEV.get(max_sev, "")

    return {"observed": observed, "inferred": inferred, "impact": impact}
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_incidents_narrative.py -v`
Expected: 6 tests, all PASS.

- [ ] **Step 3: Commit**

```bash
git add mlss_monitor/incidents_narrative.py
git commit -m "feat(incidents): prose narrative builder (timestamped, event-specific)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.3: Wire the narrative module into the API

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py` (remove `_build_narrative`, import from new module)
- Modify: `tests/test_api_incidents.py` (update narrative assertion to match new shape)

---

- [ ] **Step 1: Update the API to use the new module**

In `mlss_monitor/routes/api_incidents.py`:

Replace the import block (around lines 16–20) with:
```python
from mlss_monitor.incident_grouper import (
    cosine_similarity,
    detection_method,
    is_cross_incident,
)
from mlss_monitor.incidents_narrative import build_narrative
```

Delete the entire `_build_narrative()` function (lines 57–77).

In `get_incident()`, replace:
```python
narrative = _build_narrative(incident, alerts)
```
with:
```python
narrative = build_narrative(incident, alerts)
```

- [ ] **Step 2: Update API test expectations**

Open `tests/test_api_incidents.py`. Find the test that asserts narrative content (search for `narrative`). Update the assertion to expect the new prose style. Example change:

Old assertion:
```python
assert "event(s) detected between" in resp.json["narrative"]["observed"]
```

New assertion:
```python
assert "correlated event" in resp.json["narrative"]["observed"] \
    or "Event recorded" in resp.json["narrative"]["observed"]
```

Also search for `"Dominant detection"` and remove/replace any assertion that references the old wording.

- [ ] **Step 3: Run the full api_incidents test suite**

Run: `pytest tests/test_api_incidents.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "refactor(incidents): use incidents_narrative module in API

- Delete inline _build_narrative template
- Import build_narrative from new pure module
- Update test assertions to match new prose shape

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.4: Add delta timestamps to causal sequence chips

**Files:**
- Modify: `static/js/incident_graph.js` (`renderDetail`)
- Modify: `static/css/incident_graph.css` (chip group style)

---

- [ ] **Step 1: Update chip rendering to include time**

In `static/js/incident_graph.js`, locate the causal rendering block inside `renderDetail()`:
```js
elCausalItems.innerHTML = '<div class="inc-causal-ribbon">'
  + causal.map((a, i) =>
      (i > 0 ? '<span class="inc-causal-arrow">→</span>' : '')
      + `<span class="inc-causal-chip sev-chip-${escHtml(a.severity || 'info')}" title="${escHtml(a.title || a.event_type)}">${escHtml(a.title || a.event_type)}</span>`
    ).join('')
  + '</div>';
```

Replace it with:
```js
const startTs = causal[0] ? new Date(causal[0].created_at.replace(' ', 'T')) : null;
const fmtDelta = (iso) => {
  if (!startTs) return '';
  const t = new Date(iso.replace(' ', 'T'));
  const mins = Math.round((t - startTs) / 60000);
  return mins === 0 ? 'start' : `+${mins}m`;
};
const fmtClock = (iso) => (iso || '').slice(11, 16);

elCausalItems.innerHTML = '<div class="inc-causal-ribbon">'
  + causal.map((a, i) =>
      (i > 0 ? '<span class="inc-causal-arrow">→</span>' : '')
      + `<span class="inc-causal-chip-group" title="${escHtml(fmtClock(a.created_at))} — ${escHtml(a.title || a.event_type)}">`
      +   `<span class="inc-causal-chip sev-chip-${escHtml(a.severity || 'info')}">${escHtml(a.title || a.event_type)}</span>`
      +   `<span class="inc-causal-chip-time">${escHtml(fmtDelta(a.created_at))}</span>`
      + `</span>`
    ).join('')
  + '</div>';
```

- [ ] **Step 2: Add CSS for the chip group and time label**

Add to `static/css/incident_graph.css` (right after the `.sev-chip-info` rule):
```css
.inc-causal-chip-group {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  flex-shrink: 0;
}

.inc-causal-chip-time {
  font-size: 0.63rem;
  color: var(--text-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  padding-left: 2px;
}
```

- [ ] **Step 3: Verify manually**

Expected: each causal chip now shows a small `+8m` / `+14m` / `start` label underneath it. Tooltip on the group shows the clock time.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): timestamped delta labels on causal sequence chips

Each chip now shows +Nm (minutes from incident start) and a tooltip with the
wall-clock time. Makes the sequence actually read as a sequence rather than
an unordered list.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.5: Add severity counts to the list endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py` (`list_incidents`)
- Modify: `tests/test_api_incidents.py`

---

- [ ] **Step 1: Write the failing test first**

In `tests/test_api_incidents.py`, add the following test (choose a unique name; place near other list endpoint tests):
```python
def test_list_incidents_includes_severity_counts(client, seed_three_incidents):
    """GET /api/incidents returns a counts dict alongside the incidents array."""
    resp = client.get("/api/incidents?window=30d")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "counts" in data
    counts = data["counts"]
    assert set(counts.keys()) >= {"critical", "warning", "info"}
    assert counts["critical"] + counts["warning"] + counts["info"] == data["total"]
```

You will need a `seed_three_incidents` fixture that inserts three incidents with distinct severities. If a similar fixture already exists in the file, reuse/parameterise it. Otherwise add:
```python
import pytest
from datetime import datetime, timedelta

@pytest.fixture
def seed_three_incidents(tmp_db):
    """Insert 1 critical, 1 warning, 1 info incident within the last 24h."""
    import sqlite3, json
    conn = sqlite3.connect(tmp_db)
    now = datetime.utcnow()
    rows = [
        ("INC-A", "critical"),
        ("INC-B", "warning"),
        ("INC-C", "info"),
    ]
    for inc_id, sev in rows:
        conn.execute(
            "INSERT INTO incidents (id, started_at, ended_at, max_severity, "
            "confidence, title, signature) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (inc_id, (now - timedelta(hours=1)).isoformat(sep=" "),
             now.isoformat(sep=" "), sev, 0.9, f"Test {inc_id}", json.dumps([0.0] * 32)),
        )
    conn.commit()
    conn.close()
    return rows
```

Run: `pytest tests/test_api_incidents.py::test_list_incidents_includes_severity_counts -v`
Expected: FAIL — `'counts' not in data`.

- [ ] **Step 2: Update `list_incidents` to aggregate counts**

In `mlss_monitor/routes/api_incidents.py`, find the `list_incidents` function. Just before the `return jsonify(...)` at the end, replace:
```python
conn.close()
return jsonify({"incidents": incidents, "total": len(incidents)})
```

with:
```python
counts = {"critical": 0, "warning": 0, "info": 0}
for inc in incidents:
    sev = inc.get("max_severity", "info")
    if sev in counts:
        counts[sev] += 1

conn.close()
return jsonify({
    "incidents": incidents,
    "total": len(incidents),
    "counts": counts,
})
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_api_incidents.py -v`
Expected: All tests PASS (including the new one).

- [ ] **Step 4: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(incidents): include severity counts in list endpoint

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.6: Render severity count pills in the toolbar

**Files:**
- Modify: `templates/incidents.html` (toolbar HTML)
- Modify: `static/js/incident_graph.js` (render pills from API response)
- Modify: `static/css/incident_graph.css` (pill styles)

---

- [ ] **Step 1: Add the pills container to the toolbar**

In `templates/incidents.html`, inside the `<div class="inc-toolbar">`, after the last `<rux-segmented-button>` (around line 40), add:
```html
<div class="inc-sev-pills" id="inc-sev-pills">
  <span class="inc-sev-pill pill-critical"><span class="inc-sev-pill-count" id="pill-critical-count">0</span> Critical</span>
  <span class="inc-sev-pill pill-warning"><span class="inc-sev-pill-count" id="pill-warning-count">0</span> Warning</span>
  <span class="inc-sev-pill pill-info"><span class="inc-sev-pill-count" id="pill-info-count">0</span> Info</span>
</div>
```

- [ ] **Step 2: Populate the pills from the API response**

In `static/js/incident_graph.js`, find `loadIncidents()`. It currently does:
```js
const data = await resp.json();
allIncidents = data.incidents || [];
```

Immediately after that, add:
```js
const counts = data.counts || { critical: 0, warning: 0, info: 0 };
const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
set('pill-critical-count', counts.critical || 0);
set('pill-warning-count',  counts.warning  || 0);
set('pill-info-count',     counts.info     || 0);
```

- [ ] **Step 3: Add pill styles to CSS**

Append to `static/css/incident_graph.css`:
```css
/* ── Severity pills in toolbar ─────────────────────────────────────────── */

.inc-sev-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;  /* push to the right of the toolbar */
}

.inc-sev-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 11px;
  font-size: 0.7rem;
  font-weight: 500;
  border: 1px solid;
}

.inc-sev-pill-count { font-weight: 700; font-variant-numeric: tabular-nums; }

.pill-critical { color: #ff8a8a; background: rgba(255,56,56,0.12);  border-color: rgba(255,56,56,0.35); }
.pill-warning  { color: #fc8c2f; background: rgba(252,140,47,0.12); border-color: rgba(252,140,47,0.35); }
.pill-info     { color: #2dccff; background: rgba(45,204,255,0.1);  border-color: rgba(45,204,255,0.3); }
```

- [ ] **Step 4: Verify manually**

Expected: three pills at the right edge of the toolbar, each showing a count and the severity label, colour-coded.

- [ ] **Step 5: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): severity count pills in toolbar

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.7: Show time range + duration on incident cards

**Files:**
- Modify: `static/js/incident_graph.js` (`renderList`)
- Modify: `static/css/incident_graph.css` (card meta styles)

---

- [ ] **Step 1: Add time range to card template**

In `static/js/incident_graph.js`, locate `renderList(incidents)`. Replace the card template literal with:
```js
elList.innerHTML = incidents.map(inc => {
  const start = (inc.started_at || '').replace('T', ' ').slice(11, 16);
  const end   = (inc.ended_at   || '').replace('T', ' ').slice(11, 16);
  const date  = (inc.started_at || '').slice(0, 10);
  const durMin = (() => {
    if (!inc.started_at || !inc.ended_at) return '';
    const a = new Date(inc.started_at.replace(' ', 'T'));
    const b = new Date(inc.ended_at.replace(' ', 'T'));
    const m = Math.round((b - a) / 60000);
    return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
  })();
  return `
    <div class="inc-card${inc.id === currentIncidentId ? ' selected' : ''}"
         data-id="${escHtml(inc.id)}">
      <div class="inc-card-id">${escHtml(inc.id)}</div>
      <div class="inc-card-title" title="${escHtml(inc.title || '')}">${escHtml(inc.title || '')}</div>
      <div class="inc-card-time">
        <span>${escHtml(date)}</span>
        <span>·</span>
        <span>${escHtml(start)}–${escHtml(end)}</span>
        <span>·</span>
        <span>${escHtml(durMin)}</span>
      </div>
      <div class="inc-card-meta">
        <span class="inc-sev-dot ${escHtml(inc.max_severity || 'info')}"></span>
        <span>${escHtml(inc.max_severity || 'info')}</span>
        <span>·</span>
        <span>${inc.alert_count ?? 0} alert${inc.alert_count === 1 ? '' : 's'}</span>
      </div>
    </div>
  `;
}).join('');
```

- [ ] **Step 2: Add CSS for the new time row**

Add to `static/css/incident_graph.css` (right after the existing `.inc-card-title` rule):
```css
.inc-card-time {
  font-size: 0.68rem;
  color: var(--text-muted);
  display: flex;
  gap: 5px;
  align-items: center;
  margin: 2px 0 3px;
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 3: Verify manually**

Expected: every incident card now shows `YYYY-MM-DD · HH:MM–HH:MM · Nm` between title and meta row.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): show date, time range, and duration on incident cards

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.8: Collapse root-signal + alert into a single node; chronological arrows

**Context:** Currently each alert has a tiny "root-signal" child node plus an intra-edge connecting them. It's a 1:1 pair that adds noise with no information. Collapse to one node per alert. Replace the intra-edge with a chronological arrow from each alert to the next alert by `created_at` (primary alerts only).

**Files:**
- Modify: `static/js/incident_graph.js` (`buildIncidentElements`, `buildCytoscapeStyle`, `applyZoomClasses`)

---

- [ ] **Step 1: Delete root-signal node creation in `buildIncidentElements`**

In `static/js/incident_graph.js`, find the `primaryAlerts.forEach((alert, i) => { ... })` block. Remove the root-signal push block (the first `elements.push({...})` inside the loop that creates `root-${alert.id}`). Also remove the intra-edge push (`e-root-${alert.id}`) and the dep-edge push block that references `root-${alert.id}` as source.

The alertPos calculation using radius 140 can now be simpler — place alerts evenly around the centre at a single radius. Replace the entire `primaryAlerts.forEach` block with:
```js
primaryAlerts.forEach((alert, i) => {
  const angle = (2 * Math.PI * i) / rootCount - Math.PI / 2;
  const alertPos = {
    x: centre.x + 110 * Math.cos(angle),
    y: centre.y + 110 * Math.sin(angle),
  };

  elements.push({
    group: 'nodes',
    data: {
      id: `alert-${alert.id}`,
      label: alert.title || alert.event_type || '',
      type: 'alert',
      alertId: alert.id,
      incidentId: incId,
      parent: `hull-${incId}`,
      severity: alert.severity,
      method: alert.detection_method,
      title: alert.title || '',
      created_at: (alert.created_at || '').slice(0, 16),
    },
    position: loadSavedPosition(`${incId}::alert-${alert.id}`) || alertPos,
    classes: `alert-node${isGhost ? ' ghost' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'threshold'}`,
  });
});
```

- [ ] **Step 2: Add chronological arrows between consecutive primary alerts**

Immediately after the `primaryAlerts.forEach` block, add:
```js
// Chronological arrows between consecutive primary alerts (by created_at).
// Only on non-ghost incidents to keep ghost clusters uncluttered.
if (!isGhost) {
  const chronological = [...primaryAlerts].sort(
    (a, b) => (a.created_at || '').localeCompare(b.created_at || '')
  );
  for (let j = 0; j < chronological.length - 1; j++) {
    const src = chronological[j];
    const tgt = chronological[j + 1];
    elements.push({
      group: 'edges',
      data: { id: `chrono-${src.id}-${tgt.id}`, source: `alert-${src.id}`, target: `alert-${tgt.id}` },
      classes: 'chrono-edge',
    });
  }
}
```

- [ ] **Step 3: Update `buildCytoscapeStyle`: remove root-signal rules, add chrono-edge rule**

In `buildCytoscapeStyle()`, delete these rules:
```js
{ selector: 'node.root-signal', style: { ... } },
{ selector: 'node.root-signal.labels-full', style: { 'label': 'data(label)' } },
{ selector: 'edge.intra-edge', style: { ... } },
{ selector: 'edge.dep-edge',   style: { ... } },
```

Add in place of the intra-edge rule:
```js
{
  selector: 'edge.chrono-edge',
  style: {
    'width': 1.2,
    'line-color': '#4dacff',
    'opacity': 0.55,
    'curve-style': 'bezier',
    'target-arrow-shape': 'triangle',
    'target-arrow-color': '#4dacff',
    'arrow-scale': 0.8,
  },
},
```

- [ ] **Step 4: Update `applyZoomClasses` — remove `.root-signal` selector**

Replace:
```js
cy.nodes('.alert-node, .root-signal').forEach(n => { ... });
```
with:
```js
cy.nodes('.alert-node').forEach(n => { ... });
```

- [ ] **Step 5: Verify manually**

Expected: each alert is now a single node (no tiny satellite dot). The nodes are still positioned radially but only one ring. Arrows link consecutive alerts in chronological order. Ghost clusters show alerts without arrows.

- [ ] **Step 6: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "refactor(incidents): collapse root-signal+alert, add chronological arrows

- Remove redundant root-signal satellite node and its intra-edge (was a 1:1
  pair carrying no information).
- Remove dep-edge between root-signal and alert (same pair, no extra signal).
- Replace with chronological arrows between consecutive primary alerts sorted
  by created_at. Makes time ordering visible without a full layout change.
- Ghost clusters keep the simpler look (no arrows).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 1.9: Ghost cluster summary labels always visible

**Context:** Ghost hulls currently show only the incident ID (`INC-20260423-0928`). The user has to click to see what happened. Expose `N alerts · top sensor/type` on the hull label so ghost clusters are scannable at default zoom.

**Files:**
- Modify: `static/js/incident_graph.js` (`renderGraph` ghost placeholder + `buildIncidentElements` ghost label)

---

- [ ] **Step 1: Compute a summary label from alert data**

In `static/js/incident_graph.js`, add this helper near the top of the `// ── Graph element builder` section:
```js
/** Build a short one-line summary ("INC-...-0928 · 7 alerts · CO₂") used as
 *  the ghost hull label. Falls back gracefully if alerts are missing. */
function ghostSummaryLabel(incId, alerts, alertCount) {
  const count = (alerts && alerts.length) || alertCount || 0;
  const primary = (alerts || []).filter(a => a.is_primary);
  const topEvent = primary[0]
    ? (primary[0].title || primary[0].event_type || '').split(' ').slice(0, 3).join(' ')
    : '';
  const parts = [incId];
  if (count) parts.push(`${count} alert${count === 1 ? '' : 's'}`);
  if (topEvent) parts.push(topEvent);
  return parts.join(' · ');
}
```

- [ ] **Step 2: Use the helper when pushing the hull for ghost incidents**

In `buildIncidentElements`, find the hull push. Replace:
```js
elements.push({
  group: 'nodes',
  data: { id: `hull-${incId}`, label: incId, type: 'hull', incidentId: incId },
  classes: `hull${isGhost ? ' ghost' : ''} severity-${detail.max_severity || 'info'}`,
});
```
with:
```js
const hullLabel = isGhost
  ? ghostSummaryLabel(incId, detail.alerts || [], (detail.alerts || []).length)
  : incId;
elements.push({
  group: 'nodes',
  data: { id: `hull-${incId}`, label: hullLabel, type: 'hull', incidentId: incId },
  classes: `hull${isGhost ? ' ghost' : ''} severity-${detail.max_severity || 'info'}`,
});
```

And in the placeholder-hull branch inside `renderGraph` (before the detail has been fetched), replace:
```js
cy.add([{
  group: 'nodes',
  data: { id: `hull-${inc.id}`, label: inc.id, type: 'hull', incidentId: inc.id },
  position: centroids[inc.id] || { x: 0, y: 0 },
  classes: `hull ghost severity-${inc.max_severity || 'info'}`,
}]);
```
with:
```js
cy.add([{
  group: 'nodes',
  data: {
    id: `hull-${inc.id}`,
    label: ghostSummaryLabel(inc.id, null, inc.alert_count),
    type: 'hull',
    incidentId: inc.id,
  },
  position: centroids[inc.id] || { x: 0, y: 0 },
  classes: `hull ghost severity-${inc.max_severity || 'info'}`,
}]);
```

- [ ] **Step 3: Verify manually**

Expected: ghost hulls now read e.g. `INC-20260423-0841 · 3 alerts · Info Rapid humidity`. Placeholder (pre-fetch) reads `INC-... · N alerts`.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(incidents): ghost clusters show alert count + top event in label

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Phase 2 — Structural changes (optional / can be split)

### Task 2.1: Timeline layout inside each cluster

**Context:** Within a single incident, replace the radial hub-and-spoke layout with a horizontal timeline: x = minutes from incident start, y = a lane determined by severity (critical = 0, warning = 1, info = 2). The chronological arrows from Task 1.8 now become actual left-to-right arrows representing elapsed time.

**Files:**
- Modify: `static/js/incident_graph.js` (`buildIncidentElements` — replace `alertPos` computation)
- Modify: `static/css/incident_graph.css` (hull aspect ratio)

---

- [ ] **Step 1: Replace radial positioning with timeline positioning**

In `static/js/incident_graph.js:buildIncidentElements`, replace the `primaryAlerts.forEach((alert, i) => { const angle = ...; const alertPos = {...}; ...})` block with:
```js
// ── Timeline layout: x = minutes from incident start, y = severity lane ──
const TIMELINE_WIDTH_PX = 360;   // px allocated to the time axis per cluster
const LANE_HEIGHT_PX    = 40;
const LANE_BY_SEVERITY  = { critical: 0, warning: 1, info: 2 };

const startMs = new Date((detail.started_at || '').replace(' ', 'T')).getTime();
const endMs   = new Date((detail.ended_at   || '').replace(' ', 'T')).getTime();
const spanMs  = Math.max(endMs - startMs, 60_000);  // min 1 min to avoid /0

primaryAlerts.forEach((alert) => {
  const alertMs = new Date((alert.created_at || '').replace(' ', 'T')).getTime();
  const t = Math.max(0, Math.min(1, (alertMs - startMs) / spanMs));
  const lane = LANE_BY_SEVERITY[alert.severity] ?? 2;
  const alertPos = {
    x: centre.x - TIMELINE_WIDTH_PX / 2 + t * TIMELINE_WIDTH_PX,
    y: centre.y - LANE_HEIGHT_PX + lane * LANE_HEIGHT_PX,
  };
  elements.push({
    group: 'nodes',
    data: {
      id: `alert-${alert.id}`,
      label: alert.title || alert.event_type || '',
      type: 'alert',
      alertId: alert.id,
      incidentId: incId,
      parent: `hull-${incId}`,
      severity: alert.severity,
      method: alert.detection_method,
      title: alert.title || '',
      created_at: (alert.created_at || '').slice(0, 16),
    },
    position: loadSavedPosition(`${incId}::alert-${alert.id}`) || alertPos,
    classes: `alert-node${isGhost ? ' ghost' : ''} severity-${alert.severity || 'info'} method-${alert.detection_method || 'threshold'}`,
  });
});
```

Note: the chronological arrows from Task 1.8 still work — they now traverse the timeline left-to-right automatically.

- [ ] **Step 2: Widen the hull's aspect ratio to fit the timeline**

In `buildCytoscapeStyle()`, update the `node.hull` rule — change `'padding': '22px'` to `'padding': '30px 40px 30px 40px'`. This gives more horizontal padding so the timeline has room.

- [ ] **Step 3: Verify manually**

Expected: alerts inside the selected incident are arranged left-to-right by time. Critical alerts sit in the top lane, info in the bottom. Chronological arrows sweep rightward.

- [ ] **Step 4: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(incidents): timeline layout within each cluster (time x severity lane)

X-axis = minutes from incident start, Y-axis = severity lane. Turns the
cluster from a generic hub into an actual timeline of correlated events.
Ghost clusters use the same layout for consistency.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2.2: Cross-incident nodes in a dedicated band

**Context:** `hourly_summary`, `daily_summary`, and `annotation_context_*` alerts span multiple incidents. Currently they render as floating dashed circles with a single edge going far off-screen. Move them to a dedicated horizontal strip below the main cluster grid so they read as "context that applies globally" rather than "stray graph nodes".

**Files:**
- Modify: `static/js/incident_graph.js` (`buildCentroids`, `buildIncidentElements` cross section, `computeCrossIncidentPosition`)

---

- [ ] **Step 1: Compute a cross-band Y offset in `buildCentroids`**

In `buildCentroids(incidents)`, at the end (before `return centroids`), add:
```js
// Expose the band Y position below the grid for cross-incident nodes.
const rows = Math.ceil(incidents.length / cols);
centroids.__crossBandY = rows * GRID_SPACING + 120;
```

- [ ] **Step 2: Place cross-incident nodes on the band**

Replace the entire `computeCrossIncidentPosition` function with:
```js
/** Place cross-incident nodes on a horizontal band below the cluster grid.
 *  Each ID gets a deterministic x based on a hash of (incId, alertId) so
 *  nodes don't overlap. */
function computeCrossIncidentPosition(incId, alertId, centroids) {
  const bandY = centroids.__crossBandY || 800;
  // Simple deterministic hash → x spread across the band
  const key = `${incId}-${alertId}`;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  const x = (Math.abs(h) % 1400) - 700;
  return { x, y: bandY };
}
```

- [ ] **Step 3: Update the crossAlerts callsite**

In `buildIncidentElements`, find `const pos = computeCrossIncidentPosition(incId, allIncidents, centroids);` and replace with:
```js
const pos = computeCrossIncidentPosition(incId, alert.id, centroids);
```

- [ ] **Step 4: Verify manually**

Expected: cross-incident dashed-circle nodes now sit in a horizontal row below all the cluster boxes. Dashed edges connect each cluster to its cross node(s) without crossing over other clusters.

- [ ] **Step 5: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "feat(incidents): cross-incident nodes pinned to a band below cluster grid

Reads as 'ambient context that applies to multiple incidents' rather than
stray disconnected circles.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2.3: Summary strip above the graph

**Context:** Add a strip between the controls bar and the canvas that gives at-a-glance stats for the currently loaded window: total count, severity mix, top 3 firing sensors, and the time-of-day distribution. Depends on a small API addition.

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py` (extend list response with `summary`)
- Modify: `tests/test_api_incidents.py` (assert summary fields)
- Modify: `templates/incidents.html` (add summary strip container)
- Modify: `static/js/incident_graph.js` (render summary from API)
- Modify: `static/css/incident_graph.css` (summary strip styles)

---

- [ ] **Step 1: Write the failing test for the summary fields**

In `tests/test_api_incidents.py`, add:
```python
def test_list_incidents_includes_summary(client, seed_three_incidents):
    """List response includes top_sensors and hour_histogram summaries."""
    resp = client.get("/api/incidents?window=30d")
    data = resp.get_json()
    assert "summary" in data
    s = data["summary"]
    assert "top_sensors" in s and isinstance(s["top_sensors"], list)
    assert "hour_histogram" in s and isinstance(s["hour_histogram"], list)
    assert len(s["hour_histogram"]) == 24  # one bucket per hour of day
```

Run: `pytest tests/test_api_incidents.py::test_list_incidents_includes_summary -v`
Expected: FAIL — `'summary' not in data`.

- [ ] **Step 2: Compute summary inside `list_incidents`**

In `mlss_monitor/routes/api_incidents.py:list_incidents`, before the final `return jsonify(...)`, add:
```python
# Top 3 sensors + 24-bucket hour-of-day histogram across this window.
inc_ids = [i["id"] for i in incidents]
top_sensors: list[dict] = []
hour_histogram: list[int] = [0] * 24

if inc_ids:
    placeholders = ",".join("?" * len(inc_ids))
    sensor_rows = conn.execute(
        f"SELECT d.sensor, COUNT(*) AS n FROM alert_signal_deps d "
        f"JOIN incident_alerts ia ON ia.alert_id = d.alert_id "
        f"WHERE ia.incident_id IN ({placeholders}) "
        f"GROUP BY d.sensor ORDER BY n DESC LIMIT 3",
        inc_ids,
    ).fetchall()
    top_sensors = [{"sensor": r["sensor"], "n": r["n"]} for r in sensor_rows]

    for inc in incidents:
        started = inc.get("started_at", "")
        if len(started) >= 13:
            try:
                hour = int(started[11:13])
                hour_histogram[hour] += 1
            except ValueError:
                pass
```

Then replace the final `return jsonify(...)` with:
```python
return jsonify({
    "incidents": incidents,
    "total": len(incidents),
    "counts": counts,
    "summary": {
        "top_sensors": top_sensors,
        "hour_histogram": hour_histogram,
    },
})
```

Run tests — expected PASS.

- [ ] **Step 3: Add the summary strip container to the template**

In `templates/incidents.html`, inside the `.inc-graph-panel`, insert between the graph-controls `</div>` and the `<div id="cy-graph">` line:
```html
<div class="inc-graph-summary" id="inc-graph-summary">
  <div class="inc-summary-section">
    <span class="inc-summary-label">Top sensors</span>
    <span class="inc-summary-sensors" id="inc-summary-sensors">—</span>
  </div>
  <div class="inc-summary-section inc-summary-hist">
    <span class="inc-summary-label">By hour</span>
    <span class="inc-summary-hist-bars" id="inc-summary-hist-bars"></span>
  </div>
</div>
```

- [ ] **Step 4: Update `#cy-graph` height and add summary strip CSS**

In `static/css/incident_graph.css`:

Change the `#cy-graph` rule:
```css
#cy-graph {
  width: 100%;
  height: calc(100% - 108px); /* controls (36) + summary (36) + key (36) */
}
```

Append at end:
```css
.inc-graph-summary {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 5px 12px;
  background: rgba(13,17,23,0.85);
  border-bottom: 1px solid rgba(45,60,90,0.4);
  font-size: 0.7rem;
  color: var(--text-muted);
  height: 36px;
  flex-shrink: 0;
}

.inc-summary-section {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inc-summary-label {
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
  font-size: 0.63rem;
  color: var(--text-muted);
}

.inc-summary-sensors { color: var(--text-secondary); font-variant-numeric: tabular-nums; }

.inc-summary-hist-bars {
  display: inline-flex;
  align-items: flex-end;
  gap: 1px;
  height: 20px;
}

.inc-summary-hist-bars .bar {
  width: 4px;
  background: #2a5fa5;
  min-height: 1px;
  border-radius: 1px;
}
```

- [ ] **Step 5: Populate summary from API response**

In `static/js/incident_graph.js:loadIncidents`, after setting the pill counts (from Task 1.6), add:
```js
const summary = data.summary || { top_sensors: [], hour_histogram: [] };
const sensorsEl = document.getElementById('inc-summary-sensors');
if (sensorsEl) {
  sensorsEl.textContent = summary.top_sensors.length
    ? summary.top_sensors.map(s => `${s.sensor} (${s.n})`).join(' · ')
    : '—';
}
const histEl = document.getElementById('inc-summary-hist-bars');
if (histEl) {
  const max = Math.max(1, ...summary.hour_histogram);
  histEl.innerHTML = summary.hour_histogram
    .map(n => `<span class="bar" style="height:${(n / max * 100).toFixed(0)}%" title="${n}"></span>`)
    .join('');
}
```

- [ ] **Step 6: Verify manually**

Expected: a thin strip between controls and canvas showing "Top sensors: co_ppb (14) · tvoc_ppb (9) · pm25_ug_m3 (6)" and 24 vertical bars representing hour-of-day activity.

- [ ] **Step 7: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py templates/incidents.html static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): summary strip — top sensors + hour-of-day histogram

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2.4: "Why similar?" explanation on similar-incident rows

**Context:** The Similar Past Incidents list shows a percent score but no reason. Each incident has a 32-dimensional signature (indices documented in `incident_grouper.py:build_incident_similarity_vector`). Expose which axes contributed most to the similarity so the operator can trust or dismiss the match.

**Files:**
- Create: `tests/test_similarity_explain.py`
- Modify: `mlss_monitor/incident_grouper.py` (add `explain_similarity`)
- Modify: `mlss_monitor/routes/api_incidents.py` (`_find_similar` — include explanation)
- Modify: `static/js/incident_graph.js` (render explanation)
- Modify: `static/css/incident_graph.css` (explanation styling)

---

- [ ] **Step 1: Write failing test for `explain_similarity`**

Create `tests/test_similarity_explain.py`:
```python
from mlss_monitor.incident_grouper import explain_similarity


def test_explain_returns_top_matching_axes():
    # Two vectors that agree strongly on severity-critical (idx 28) + ML method (idx 21)
    a = [0.0] * 32
    b = [0.0] * 32
    a[28] = b[28] = 1.0  # both critical
    a[21] = b[21] = 1.0  # both ML-detected
    explanation = explain_similarity(a, b)
    assert "severity" in explanation.lower() or "critical" in explanation.lower()
    assert "ml" in explanation.lower() or "method" in explanation.lower()


def test_explain_empty_vectors_returns_fallback():
    assert explain_similarity([], []) == "No comparable signal."


def test_explain_unequal_lengths_returns_fallback():
    assert explain_similarity([1.0] * 32, [1.0] * 31) == "No comparable signal."
```

Run: `pytest tests/test_similarity_explain.py -v`
Expected: FAIL — ImportError, function doesn't exist.

- [ ] **Step 2: Implement `explain_similarity` in the grouper**

In `mlss_monitor/incident_grouper.py`, append:
```python
# Human-readable labels for the similarity vector axes.
_VECTOR_AXIS_LABELS: dict[int, str] = {
    10: "TVOC", 11: "eCO2", 12: "temperature", 13: "humidity",
    14: "PM1", 15: "PM2.5", 16: "PM10",
    17: "CO", 18: "NO2", 19: "NH3",
    20: "method:threshold", 21: "method:ml", 22: "method:fingerprint",
    23: "method:summary", 24: "method:statistical",
    26: "severity:info", 27: "severity:warning", 28: "severity:critical",
    29: "duration", 30: "confidence", 31: "time-of-day",
}


def explain_similarity(a: list[float], b: list[float], top_n: int = 3) -> str:
    """Human-readable explanation of which axes dominate the similarity.

    Returns a short comma-separated phrase naming the top ``top_n`` matching
    labelled axes where both vectors have nonzero values.  Used by the API to
    tell the UI *why* two incidents are considered similar.
    """
    if not a or not b or len(a) != len(b):
        return "No comparable signal."
    matches: list[tuple[int, float]] = []
    for i, label in _VECTOR_AXIS_LABELS.items():
        if i >= len(a):
            continue
        contribution = a[i] * b[i]
        if contribution > 0:
            matches.append((i, contribution))
    if not matches:
        return "Low-level similarity."
    matches.sort(key=lambda x: -x[1])
    labels = [_VECTOR_AXIS_LABELS[i] for i, _ in matches[:top_n]]
    return "Matches on: " + ", ".join(labels) + "."
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_similarity_explain.py -v`
Expected: 3 PASS.

- [ ] **Step 4: Include the explanation in the API response**

In `mlss_monitor/routes/api_incidents.py`:

Update the import:
```python
from mlss_monitor.incident_grouper import (
    cosine_similarity,
    detection_method,
    explain_similarity,
    is_cross_incident,
)
```

Inside `_find_similar`, replace the scored-dict construction with:
```python
scored.append({
    "id": row["id"],
    "title": row["title"],
    "started_at": row["started_at"],
    "max_severity": row["max_severity"],
    "confidence": row["confidence"],
    "similarity": round(score, 3),
    "why": explain_similarity(signature, other_sig),
})
```

- [ ] **Step 5: Show explanation in the UI**

In `static/js/incident_graph.js`, find the similar-items render inside `renderDetail`. Replace the template literal with:
```js
elSimilarItems.innerHTML = similar.map(s => `
  <div class="inc-similar-item" data-similar-id="${escHtml(s.id)}">
    <div class="inc-similar-main">
      <div style="font-size:0.75rem;font-weight:700;color:var(--text-muted)">${escHtml(s.id)}</div>
      <div style="font-size:0.8rem">${escHtml(s.title || '')}</div>
      <div class="inc-similar-why">${escHtml(s.why || '')}</div>
    </div>
    <div style="text-align:right">
      <div class="inc-similar-score">${(s.similarity * 100).toFixed(0)}% similar</div>
      <span class="inc-similar-nav">›</span>
    </div>
  </div>
`).join('');
```

- [ ] **Step 6: Add CSS for the explanation line**

Append to `static/css/incident_graph.css`:
```css
.inc-similar-main { flex: 1; min-width: 0; }
.inc-similar-why {
  font-size: 0.68rem;
  color: var(--text-muted);
  font-style: italic;
  margin-top: 2px;
}
```

- [ ] **Step 7: Verify manually**

Expected: each similar-incident row now shows one italic grey line like `Matches on: severity:critical, method:ml, eCO2.`

- [ ] **Step 8: Commit**

```bash
git add mlss_monitor/incident_grouper.py tests/test_similarity_explain.py mlss_monitor/routes/api_incidents.py static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): explain why past incidents are similar

Adds explain_similarity() that names the top-3 matching axes of the 32-dim
signature (e.g. 'severity:critical, method:ml, eCO2'). Surfaced on every
similar-incident row as italic grey subtext.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Rollout

After each task's commit, deploy to the Pi with:
```bash
git push origin feature/incident-correlation-graph
# On the Pi:
git pull && sudo systemctl restart mlss-monitor
```

Phases can be merged to main individually. Suggested order:
1. Phase 0 (one commit) — ship immediately
2. Phase 1 Tasks 1.1–1.4 (narrative + timestamps) — ship as a bundle
3. Phase 1 Tasks 1.5–1.7 (pills + card meta) — ship as a bundle
4. Phase 1 Tasks 1.8–1.9 (node collapse + ghost labels) — ship as a bundle
5. Phase 2 — optional, based on whether Phase 1 alone feels sufficient after use

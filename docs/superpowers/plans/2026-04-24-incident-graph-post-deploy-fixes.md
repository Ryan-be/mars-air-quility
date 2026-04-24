# Incident Graph Post-Deploy Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix hull overlap, add tagging control on alert nodes, and replace the five broken Cytoscape alt layouts with two purpose-built view modes.

**Architecture:** Extract centroid math into a pure ES module so the overlap fix is testable in Node. Add one backend field (`primary_count`) so the frontend can size rows correctly. Reuse existing `/api/tags` and `/api/inferences/<id>/tags` endpoints (already RBAC-guarded). Toolbar swaps the wide `<select>` for two extra `.inc-layout-btn`s wired to a `viewMode` state persisted in `localStorage`.

**Tech Stack:** Flask/SQLite backend, vanilla JS ES modules, Cytoscape.js (MIT, CDN), `node` for fixture-based frontend tests, `pytest` for backend.

**Spec:** `docs/superpowers/specs/2026-04-24-incident-graph-post-deploy-fixes-design.md`

---

## File Structure

Files created / modified during this plan:

- `mlss_monitor/routes/api_incidents.py` — add `primary_count` to list response
- `tests/test_api_incidents.py` — assert `primary_count` present + correct
- `static/js/compute_centroids.mjs` **(new)** — pure centroid math, view-mode-aware
- `tests/js/test_compute_centroids.mjs` **(new)** — Node fixture tests
- `static/js/incident_graph.js` — import `computeCentroids`, delete alt-layout branches, add view-mode state + persistence, tagging section in node overlay
- `templates/incidents.html` — remove `<select id="inc-layout-alt">`, add Compact + Chronological buttons
- `static/css/incident_graph.css` — view-mode button active state, tag section styles

No new routes. No new DB tables. One small additive field on an existing endpoint.

---

## Task 1: Backend — add `primary_count` to incidents list endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_incidents.py:70-82` (`_alert_counts_by_incident` helper) and `:120-214` (`list_incidents`)
- Modify: `tests/test_api_incidents.py:107-112` (extend existing `test_get_incidents_includes_alert_count`) + add one focused test

- [ ] **Step 1: Write failing test for `primary_count` presence**

Append to `tests/test_api_incidents.py` after `test_get_incidents_includes_alert_count`:

```python
def test_get_incidents_includes_primary_count(client, db):
    """Listing must include primary_count alongside alert_count so the
    frontend can size incident rows by how many primary alerts stack."""
    _seed_incident(db, "INC-20260424-1100")
    # Seed one primary alert + one cross-incident alert on this incident.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO inferences (id, created_at, event_type, severity, "
        "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
        (901, "2026-04-24 11:00:00", "high_tvoc", "warning", "t", 0.8),
    )
    conn.execute(
        "INSERT INTO inferences (id, created_at, event_type, severity, "
        "title, confidence) VALUES (?, ?, ?, ?, ?, ?)",
        (902, "2026-04-24 11:05:00", "hourly_summary", "info", "h", 0.8),
    )
    conn.execute(
        "INSERT INTO incident_alerts (incident_id, alert_id, is_primary) "
        "VALUES (?, ?, ?)", ("INC-20260424-1100", 901, 1))
    conn.execute(
        "INSERT INTO incident_alerts (incident_id, alert_id, is_primary) "
        "VALUES (?, ?, ?)", ("INC-20260424-1100", 902, 0))
    conn.commit()
    conn.close()

    rv = client.get("/api/incidents")
    data = rv.get_json()
    incident = next(i for i in data["incidents"] if i["id"] == "INC-20260424-1100")
    assert incident["alert_count"] == 2
    assert incident["primary_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_incidents.py::test_get_incidents_includes_primary_count -v`
Expected: FAIL with `KeyError: 'primary_count'` or `assert ... == 1` with missing key.

- [ ] **Step 3: Extend `_alert_counts_by_incident` to return both counts**

In `mlss_monitor/routes/api_incidents.py`, replace the `_alert_counts_by_incident` helper (lines 70-82):

```python
def _alert_counts_by_incident(
    conn, incident_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Single GROUP BY query returning
    ``{incident_id: {"total": N, "primary": P}}``.
    ``primary`` is the count of rows with ``is_primary = 1`` and is
    consumed by the frontend to size incident rows on the canvas by how
    many primary alerts stack in a severity lane.
    """
    if not incident_ids:
        return {}
    placeholders = ",".join("?" * len(incident_ids))
    rows = conn.execute(
        f"SELECT incident_id, "
        f"       COUNT(*) AS total, "
        f"       SUM(CASE WHEN is_primary = 1 THEN 1 ELSE 0 END) AS primary_n "
        f"FROM incident_alerts "
        f"WHERE incident_id IN ({placeholders}) "
        f"GROUP BY incident_id",
        incident_ids,
    ).fetchall()
    return {
        r["incident_id"]: {"total": r["total"], "primary": r["primary_n"] or 0}
        for r in rows
    }
```

- [ ] **Step 4: Update `list_incidents` to consume the new shape**

In `mlss_monitor/routes/api_incidents.py`, replace the loop at lines 166-168 (`for inc in incidents: inc["alert_count"] = count_by_id.get(inc["id"], 0)`) with:

```python
    count_by_id = _alert_counts_by_incident(conn, [i["id"] for i in incidents])
    for inc in incidents:
        counts_row = count_by_id.get(inc["id"], {"total": 0, "primary": 0})
        inc["alert_count"] = counts_row["total"]
        inc["primary_count"] = counts_row["primary"]
```

- [ ] **Step 5: Run failing test + existing test; both must pass**

Run: `python -m pytest tests/test_api_incidents.py -v`
Expected: all incidents tests pass including the new one.

- [ ] **Step 6: Commit**

```bash
git add mlss_monitor/routes/api_incidents.py tests/test_api_incidents.py
git commit -m "feat(api): include primary_count in incidents list response

Frontend needs primary_count (not total alert_count) to compute the
stack-depth-aware row height that fixes hull overlap. Adds one SUM()
to the existing GROUP BY — no extra query."
```

---

## Task 2: Extract pure `computeCentroids` module

**Files:**
- Create: `static/js/compute_centroids.mjs`
- Create: `tests/js/test_compute_centroids.mjs`

- [ ] **Step 1: Write failing Node fixture test**

Create `tests/js/test_compute_centroids.mjs`:

```javascript
// Fixture-based test for the client-side computeCentroids helper.
// Run: node tests/js/test_compute_centroids.mjs
// Exit 0 on pass, 1 on failure.

import { computeCentroids } from '../../static/js/compute_centroids.mjs';

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

// --- Manual mode ---------------------------------------------------------
const two = [
  { id: 'A', alert_count: 2, primary_count: 2 },
  { id: 'B', alert_count: 2, primary_count: 2 },
];
const c1 = computeCentroids(two, 'manual');
expect('manual: 2 incidents fit on one row (y=0)',
  [c1.A.y, c1.B.y], [0, 0]);
expect('manual: A left of B',
  c1.A.x < c1.B.x, true);

// Four incidents → 2 rows of 2.
const four = [
  { id: 'A', alert_count: 2, primary_count: 2 },
  { id: 'B', alert_count: 2, primary_count: 2 },
  { id: 'C', alert_count: 2, primary_count: 2 },
  { id: 'D', alert_count: 2, primary_count: 2 },
];
const c2 = computeCentroids(four, 'manual');
expect('manual: row 1 centre y > 0', c2.C.y > 0, true);
expect('manual: rows A and C share x lane', c2.A.x === c2.C.x, true);

// --- Row height scales with stack depth ----------------------------------
// 30 primary alerts → full stack (primary/3 >= 5) → half_h capped at 124.
// 2 primary alerts → shallow stack (ceil(2/3) = 1) → half_h = 44 + 16 = 60.
// Two rows of deep incidents must have MORE vertical gap than two rows of
// shallow incidents.
const deep = [
  { id: 'D1', alert_count: 30, primary_count: 30 },
  { id: 'D2', alert_count: 30, primary_count: 30 },
  { id: 'D3', alert_count: 30, primary_count: 30 },
  { id: 'D4', alert_count: 30, primary_count: 30 },
];
const shallow = [
  { id: 'S1', alert_count: 2, primary_count: 2 },
  { id: 'S2', alert_count: 2, primary_count: 2 },
  { id: 'S3', alert_count: 2, primary_count: 2 },
  { id: 'S4', alert_count: 2, primary_count: 2 },
];
const cd = computeCentroids(deep, 'manual');
const cs = computeCentroids(shallow, 'manual');
expect('deep rows get more vertical gap than shallow rows',
  cd.D3.y > cs.S3.y, true);

// No-overlap invariant: adjacent rows' clusters must not overlap
// vertically. row_gap >= half_h(r) + half_h(r+1).
// For 30-alert rows: half_h = 124. Two rows: delta >= 248 + INTER_ROW_GAP.
expect('deep rows: row1_y - row0_y >= 248',
  cd.D3.y - cd.D1.y >= 248, true);

// --- Chronological mode: all clusters on row 0 ---------------------------
const chronoIn = [
  { id: 'A', alert_count: 2, primary_count: 2, started_at: '2026-04-24 10:00:00' },
  { id: 'B', alert_count: 2, primary_count: 2, started_at: '2026-04-24 09:00:00' },
  { id: 'C', alert_count: 2, primary_count: 2, started_at: '2026-04-24 11:00:00' },
];
const cc = computeCentroids(chronoIn, 'chronological');
expect('chronological: all y = 0',
  [cc.A.y, cc.B.y, cc.C.y], [0, 0, 0]);
expect('chronological: sorted by started_at ascending (B<A<C)',
  cc.B.x < cc.A.x && cc.A.x < cc.C.x, true);

// --- Compact mode: narrower widths than manual ---------------------------
const ck = computeCentroids(two, 'compact');
expect('compact: B.x < manual B.x (tighter packing)',
  ck.B.x < c1.B.x, true);

if (failures) {
  console.error(`\n${failures} test(s) failed`);
  process.exit(1);
}
console.log('\nAll computeCentroids tests passed');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_compute_centroids.mjs`
Expected: FAIL with `ERR_MODULE_NOT_FOUND` — `compute_centroids.mjs` doesn't exist yet.

- [ ] **Step 3: Create `static/js/compute_centroids.mjs`**

```javascript
// Pure centroid math for the incidents graph. Kept as an ES module so it
// can be fixture-tested in Node without a DOM.
//
// Three view modes share the same timeline-in-hull geometry; only the
// packing/sizing constants differ:
//   manual        — default, roomy, matches the hull padding in CSS
//   compact       — denser for "scan many incidents" use
//   chronological — single row, clusters ordered by started_at ascending
//
// Contract: given a list of incidents with { id, alert_count, primary_count,
// started_at? } and a view mode, return { [id]: {x, y}, __crossBandY }.

const MODES = {
  manual: {
    MIN_WIDTH_PX:       360,
    PX_PER_ALERT:       32,
    HULL_PADDING_PX:    80,
    INTER_CLUSTER_GAP:  70,
    LANE_HEIGHT_PX:     44,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,
  },
  compact: {
    MIN_WIDTH_PX:       240,
    PX_PER_ALERT:       20,
    HULL_PADDING_PX:    60,
    INTER_CLUSTER_GAP:  40,
    LANE_HEIGHT_PX:     32,
    STACK_DY_PX:        14,
    INTER_ROW_GAP:      40,
  },
  chronological: {
    MIN_WIDTH_PX:       300,
    PX_PER_ALERT:       28,
    HULL_PADDING_PX:    70,
    INTER_CLUSTER_GAP:  50,
    LANE_HEIGHT_PX:     40,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,  // unused in single-row mode
  },
};

// Max STACK_STEPS depth (from incident_graph.js STACK_STEPS array = 11 slots
// = 5 steps each side). A cluster's stack depth is bounded by how many
// alerts share a severity lane; with 3 lanes and even distribution, the
// worst-case slot used ≈ ceil(primary_count / 3), capped at 5.
const MAX_STACK_STEPS = 5;

function clusterHalfHeight(primaryCount, c) {
  const stackSlots = Math.min(MAX_STACK_STEPS, Math.ceil(Math.max(1, primaryCount) / 3));
  return c.LANE_HEIGHT_PX + stackSlots * c.STACK_DY_PX;
}

function clusterWidth(alertCount, c) {
  const count = Math.max(1, alertCount || 0);
  return Math.max(c.MIN_WIDTH_PX, count * c.PX_PER_ALERT) + 2 * c.HULL_PADDING_PX;
}

export function computeCentroids(incidents, viewMode = 'manual') {
  const c = MODES[viewMode] || MODES.manual;
  const n = incidents.length;
  if (n === 0) return { __crossBandY: 0 };

  // Chronological: single row, sorted by started_at ascending.
  if (viewMode === 'chronological') {
    const sorted = [...incidents].sort(
      (a, b) => String(a.started_at || '').localeCompare(String(b.started_at || ''))
    );
    const centroids = {};
    let cursor = 0;
    let maxHalfH = 0;
    for (const inc of sorted) {
      const w = clusterWidth(inc.alert_count, c);
      const halfH = clusterHalfHeight(inc.primary_count, c);
      maxHalfH = Math.max(maxHalfH, halfH);
      centroids[inc.id] = { x: cursor + w / 2, y: 0 };
      cursor += w + c.INTER_CLUSTER_GAP;
    }
    centroids.__crossBandY = maxHalfH + 140;
    return centroids;
  }

  // Grid modes: sqrt(n) columns, dynamic per-row height from max stack depth.
  const cols = Math.ceil(Math.sqrt(Math.max(n, 1)));
  const widths  = incidents.map(i => clusterWidth(i.alert_count, c));
  const halfHs  = incidents.map(i => clusterHalfHeight(i.primary_count, c));

  // Row-wise max half-height.
  const rows = Math.ceil(n / cols);
  const rowHalfH = [];
  for (let r = 0; r < rows; r++) {
    let m = 0;
    for (let cc = 0; cc < cols && r * cols + cc < n; cc++) {
      m = Math.max(m, halfHs[r * cols + cc]);
    }
    rowHalfH.push(m);
  }

  // Row centre Y: row 0 at y=0; subsequent rows at prev_centre + prev_half_h +
  // this_half_h + INTER_ROW_GAP.
  const rowCentreY = [0];
  for (let r = 1; r < rows; r++) {
    rowCentreY.push(
      rowCentreY[r - 1] + rowHalfH[r - 1] + rowHalfH[r] + c.INTER_ROW_GAP
    );
  }

  const centroids = {};
  for (let r = 0; r < rows; r++) {
    let cursor = 0;
    for (let cc = 0; cc < cols && r * cols + cc < n; cc++) {
      const idx = r * cols + cc;
      centroids[incidents[idx].id] = {
        x: cursor + widths[idx] / 2,
        y: rowCentreY[r],
      };
      cursor += widths[idx] + c.INTER_CLUSTER_GAP;
    }
  }

  centroids.__crossBandY =
    rowCentreY[rows - 1] + rowHalfH[rows - 1] + 140;
  return centroids;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/js/test_compute_centroids.mjs`
Expected: all 9 `ok` lines, exit 0.

- [ ] **Step 5: Commit**

```bash
git add static/js/compute_centroids.mjs tests/js/test_compute_centroids.mjs
git commit -m "feat(incidents): pure computeCentroids module + fixture tests

Extracts the centroid-placement math from incident_graph.js so it can
be Node-tested. Adds stack-depth-aware dynamic row height (fixes hull
overlap) and view-mode-aware constants (manual/compact/chronological)."
```

---

## Task 3: Wire `computeCentroids` into `incident_graph.js`

**Files:**
- Modify: `static/js/incident_graph.js` top-of-file imports (~line 5), `buildCentroids` (line 1145), and any internal callers of the old function

- [ ] **Step 1: Import `computeCentroids` at the top of `incident_graph.js`**

Near the existing `import { connectedComponents } from './connected_components.mjs';` line:

```javascript
import { computeCentroids } from './compute_centroids.mjs';
```

- [ ] **Step 2: Add module-level `viewMode` state + persistence**

Near the existing `edgePFloor` module state (search the file for `edgePFloor`), add:

```javascript
// Current view mode. Persisted per-user; defaults to 'manual'.
// Valid: 'manual' | 'compact' | 'chronological'.
let viewMode = (() => {
  try {
    const v = localStorage.getItem('inc.view_mode');
    return (v === 'compact' || v === 'chronological') ? v : 'manual';
  } catch (_) { return 'manual'; }
})();

function setViewMode(mode) {
  if (mode !== 'manual' && mode !== 'compact' && mode !== 'chronological') return;
  viewMode = mode;
  try { localStorage.setItem('inc.view_mode', mode); } catch (_) {}
  if (currentDetail) renderGraph(currentDetail, allIncidents);
}
```

- [ ] **Step 3: Replace the local `buildCentroids` function**

In `static/js/incident_graph.js`, replace the entire `buildCentroids` function (line 1145 to its closing `}`) with a thin wrapper that delegates to the imported pure function:

```javascript
function buildCentroids(incidents) {
  return computeCentroids(incidents, viewMode);
}
```

- [ ] **Step 4: Run the full test suite to confirm nothing regressed**

Run: `python -m pytest tests/ -q` and `node tests/js/test_compute_centroids.mjs` and `node tests/js/test_connected_components.mjs`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add static/js/incident_graph.js
git commit -m "refactor(incidents): buildCentroids delegates to pure module

Thin wrapper so view-mode state drives the centroid math. No behavior
change in 'manual' mode yet — compact and chronological are unreachable
until Task 4 adds the UI controls."
```

---

## Task 4: Template + toolbar — remove dropdown, add view-mode buttons

**Files:**
- Modify: `templates/incidents.html:67-87` (the `.inc-graph-controls` block)
- Modify: `static/js/incident_graph.js` `initToolbar` (around line 164-212) — the Layout controls section

- [ ] **Step 1: Replace the Manual-button + dropdown in the template**

In `templates/incidents.html`, replace lines ~74-86 (from the `<!-- Manual ... -->` comment through the closing `</select>`) with:

```html
      <button class="inc-ctrl-btn inc-layout-btn active" data-view="manual"
              title="Roomy timeline grid (default)">Manual</button>
      <button class="inc-ctrl-btn inc-layout-btn" data-view="compact"
              title="Tighter packing — scan many incidents at once">Compact</button>
      <button class="inc-ctrl-btn inc-layout-btn" data-view="chronological"
              title="Single row, ordered by start time">Chronological</button>
```

- [ ] **Step 2: Replace the layout-controls block in `initToolbar`**

In `static/js/incident_graph.js`, replace lines ~164-212 (the "Layout controls" block — everything from the comment `// ── Layout controls ──` down to the closing `}` of the `altSelect` listener) with:

```javascript
  // ── View-mode controls ────────────────────────────────────────────
  // Three buttons pick between the three purpose-built view modes. All
  // three honor incident hulls; the alt Cytoscape layouts were removed
  // because they scattered nodes and broke the mental model.
  const viewBtns = document.querySelectorAll('.inc-layout-btn');
  // Ensure the button matching the persisted mode is active on load.
  viewBtns.forEach(b => {
    b.classList.toggle('active', b.dataset.view === viewMode);
    b.addEventListener('click', () => {
      viewBtns.forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      setViewMode(b.dataset.view);
    });
  });
```

- [ ] **Step 3: Delete the dead `runLayout` function**

Still in `static/js/incident_graph.js`, `runLayout` at lines ~170-196 is now dead code (no callers after Step 2). Delete the entire function.

- [ ] **Step 4: Manual smoke test (no JS tests cover DOM wiring)**

Run: `python -m pytest tests/ -q && node tests/js/test_compute_centroids.mjs && node tests/js/test_connected_components.mjs`
Expected: all tests still pass.

Reload `/incidents` in a browser; verify:
- Three buttons (Manual, Compact, Chronological) appear instead of a dropdown.
- Clicking Compact re-renders clusters tighter.
- Clicking Chronological lays out clusters on a single row.
- Refresh page; last-clicked mode is restored.

- [ ] **Step 5: Commit**

```bash
git add templates/incidents.html static/js/incident_graph.js
git commit -m "feat(incidents): replace alt-layout dropdown with view modes

Removes the wide <select> with 5 Cytoscape layouts (cose, breadthfirst,
circle, grid, concentric) that scatter nodes and break incident hulls.
Replaces with three buttons (Manual/Compact/Chronological) that all
share the timeline-in-hull geometry; only packing constants differ.
Persists choice in localStorage (inc.view_mode)."
```

---

## Task 5: Tagging controls in node overlay

**Files:**
- Modify: `static/js/incident_graph.js` around `showNodeOverlay` (line 624) and `renderAlertTable` (line 642)
- Modify: `static/css/incident_graph.css` — add tag section styles near the existing `.inc-node-overlay` block (search for `inc-node-overlay` in the CSS)

- [ ] **Step 1: Add tag-related module state + EMOJI map**

Near the top of `static/js/incident_graph.js` (after the imports, before `initToolbar`):

```javascript
// Controlled-vocabulary tags fetched from /api/tags once per page load.
// Cache to avoid re-fetching on every overlay open.
let tagVocab = null;
const TAG_EMOJI = {
  cooking: '🍳',
  external_pollution: '🌫️',
  vehicle_exhaust: '🚗',
  biological_offgas: '🧬',
  chemical_offgassing: '🧪',
  combustion: '🔥',
  cleaning_products: '🧹',
  human_activity: '👤',
  mould_voc: '🍄',
  personal_care: '🧴',
};

async function fetchTagVocab() {
  if (tagVocab) return tagVocab;
  try {
    const resp = await fetch('/api/tags');
    if (resp.ok) {
      const data = await resp.json();
      tagVocab = data.tags || [];
    } else {
      tagVocab = [];
    }
  } catch (_) { tagVocab = []; }
  return tagVocab;
}
```

- [ ] **Step 2: Extend `showNodeOverlay` to render tags section for alert nodes**

In `static/js/incident_graph.js`, replace the current `showNodeOverlay` body (line 624-640) with:

```javascript
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

  // Cross-incident alerts (hourly/daily/annotation) aren't root causes, so
  // we don't surface the tagging UI on them.
  const taggable = !alert.is_cross_incident;

  // Render the metadata table immediately; the tags section appears once
  // the async fetches settle.
  if (elNodeBody) {
    elNodeBody.innerHTML = renderAlertTable(alert)
      + (taggable ? renderTagsShell() : '');
  }
  if (taggable) await populateTagsSection(alert.id);
}
```

- [ ] **Step 3: Add the tag-section render + populate helpers**

Append these helpers directly after `renderAlertTable` in `static/js/incident_graph.js`. The file already has an `html` tagged template literal (defined near line 1613) that auto-escapes interpolations — reuse it rather than introducing a separate escape helper.

```javascript
// Renders an empty tag section. populateTagsSection() fills it once the
// async fetches return. No interpolation here — the literal shell is safe.
function renderTagsShell() {
  return html`
    <div class="inc-tags-section" id="inc-tags-section">
      <div class="inc-tags-header">
        Tags <span class="inc-tags-help"
          title="Tags record what caused this event. Feedback trains the attribution engine.">ⓘ</span>
      </div>
      <div class="inc-tags-list" id="inc-tags-list">
        <span class="inc-tags-loading">Loading tags…</span>
      </div>
      <div class="inc-tag-controls">
        <select id="inc-tag-select" class="inc-tag-select">
          <option value="">Select a tag…</option>
        </select>
        <button type="button" id="inc-tag-add" class="inc-tag-add-btn">Add Tag</button>
      </div>
      <div class="inc-tag-status" id="inc-tag-status"></div>
    </div>
  `;
}

// Monotonic token — if the user has clicked a different node while we
// awaited the network, the token no longer matches and we bail out.
let lastTagFetchToken = 0;

async function populateTagsSection(alertId) {
  const myToken = ++lastTagFetchToken;
  const vocab = await fetchTagVocab();
  if (myToken !== lastTagFetchToken) return;  // superseded

  // Fetch current tags on this alert.
  let current = [];
  try {
    const resp = await fetch(`/api/inferences/${alertId}/tags`);
    if (resp.ok) current = await resp.json();
  } catch (_) { /* leave empty */ }
  if (myToken !== lastTagFetchToken) return;

  const listEl = document.getElementById('inc-tags-list');
  const selectEl = document.getElementById('inc-tag-select');
  const addBtn = document.getElementById('inc-tag-add');
  const statusEl = document.getElementById('inc-tag-status');
  if (!listEl || !selectEl || !addBtn) return;

  // Render existing tags as pills via the `html` tagged template so the
  // label text is auto-escaped.
  if (current.length === 0) {
    listEl.innerHTML = html`<span class="inc-tags-empty">No tags yet.</span>`;
  } else {
    listEl.innerHTML = html`${current.map(t => {
      const label = (vocab.find(v => v.id === t.tag) || {}).label || t.tag;
      const emoji = TAG_EMOJI[t.tag] || '';
      return html`<span class="inc-tag-pill">${emoji} ${label}</span>`;
    })}`;
  }

  // Populate the select with vocab, skipping tags already applied. Using
  // DOM APIs (not innerHTML) so we don't have to think about escaping the
  // option values.
  const appliedIds = new Set(current.map(t => t.tag));
  vocab.forEach(({ id, label }) => {
    if (appliedIds.has(id)) return;
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${TAG_EMOJI[id] || ''} ${label}`.trim();
    selectEl.appendChild(opt);
  });

  addBtn.onclick = async () => {
    const chosen = selectEl.value;
    if (!chosen) return;
    statusEl.textContent = 'Saving…';
    try {
      const resp = await fetch(`/api/inferences/${alertId}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag: chosen, confidence: 1.0 }),
      });
      if (!resp.ok) {
        statusEl.textContent = `Save failed (${resp.status})`;
        return;
      }
      statusEl.textContent = 'Saved';
      // Re-populate to reflect the new pill and drop the used option.
      await populateTagsSection(alertId);
    } catch (e) {
      statusEl.textContent = 'Network error — try again.';
    }
  };
}
```

- [ ] **Step 4: Add CSS for the tag section**

Append to `static/css/incident_graph.css` (near the bottom, or adjacent to the existing `.inc-node-overlay` rules):

```css
.inc-tags-section {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(90, 120, 170, 0.25);
}
.inc-tags-header {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-secondary, #9aa5bd);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.inc-tags-help {
  cursor: help;
  opacity: 0.7;
}
.inc-tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
  min-height: 20px;
}
.inc-tags-loading, .inc-tags-empty {
  font-size: 0.72rem;
  color: var(--color-text-muted, #7a8497);
  font-style: italic;
}
.inc-tag-pill {
  background: rgba(45, 204, 255, 0.15);
  border: 1px solid rgba(45, 204, 255, 0.35);
  color: #cfe8ff;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 0.72rem;
}
.inc-tag-controls {
  display: flex;
  gap: 6px;
  align-items: center;
}
.inc-tag-select {
  flex: 1;
  background: transparent;
  border: 1px solid rgba(90, 120, 170, 0.55);
  color: var(--color-text-secondary, #c9d1d9);
  font-size: 0.72rem;
  padding: 3px 6px;
  border-radius: 3px;
}
.inc-tag-add-btn {
  background: rgba(77, 172, 255, 0.15);
  border: 1px solid rgba(77, 172, 255, 0.55);
  color: #cfe8ff;
  font-size: 0.72rem;
  padding: 3px 10px;
  border-radius: 3px;
  cursor: pointer;
}
.inc-tag-add-btn:hover {
  background: rgba(77, 172, 255, 0.28);
}
.inc-tag-status {
  font-size: 0.7rem;
  color: var(--color-text-muted, #7a8497);
  margin-top: 4px;
  min-height: 14px;
}
```

- [ ] **Step 5: Run full verification**

Run: `python -m pytest tests/ -q && node tests/js/test_compute_centroids.mjs && node tests/js/test_connected_components.mjs`
Expected: all pass.

Browser smoke test on the Pi / local dev:
- Click an alert node. Overlay opens, metadata table renders immediately, tags section shows "Loading…" then resolves to current tags + dropdown + Add Tag button.
- Add a tag; pill appears, option disappears from select, "Saved" status.
- Click a different node mid-load; no tags-section ghosting from the previous click.
- Click a cross-incident summary node; no tags section (intended).

- [ ] **Step 6: Commit**

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): tag-for-ML control in alert node overlay

Mirrors the history-page tagging pattern (select + Add Tag button,
pills for existing tags) inside #inc-node-overlay. Reuses /api/tags
and /api/inferences/<id>/tags — no backend changes. Cross-incident
summary nodes aren't taggable (not root causes). Race-safe via a
fetch-token check so clicking multiple nodes quickly doesn't leak
stale data."
```

---

## Verification (after all tasks)

Run from repo root:

```bash
python -m pytest tests/ -q
node tests/js/test_compute_centroids.mjs
node tests/js/test_connected_components.mjs
python -m pylint --disable=import-error,no-name-in-module \
  mlss_monitor/routes/api_incidents.py \
  tests/test_api_incidents.py
```

Expected:
- pytest: 731 passed, 1 skipped (the new `primary_count` test adds one)
- Node: both green
- pylint: 10.00/10 (no regression from current)

Browser (on Pi `https://192.168.0.203:5000/incidents`):
- Hulls never cross each other vertically, even with deep-stack incidents.
- Toolbar shows Manual / Compact / Chronological buttons; Compact packs tighter; Chronological uses a single row.
- Clicking an alert node opens the overlay with a Tags section; adding a tag persists and re-renders without reload.
- Cross-incident summary nodes have no tags section.

# Overlap True-Fix + Cyto Padding Sync + Remove-Tag Affordance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the still-reported hull-overlap regression by fixing two compounding bugs in the centroid math, then add a remove-tag affordance to the incident alert overlay.

**Architecture:**
1. **Overlap true-fix** — `clusterHalfHeight` currently assumes even lane distribution (`ceil(primary/3)`) and ignores Cytoscape compound-node padding. Both assumptions are wrong in production. Replace the formula with the one-lane worst case (`ceil((primary-1)/2)`, capped at `MAX_STACK_STEPS`) and add `CYTO_HULL_PADDING_Y` to each mode's constants so hull math and hull paint agree.
2. **Cytoscape stylesheet sync** — the Cytoscape `'padding'` on `node.hull` is a hardcoded string; replace with a dynamic value from the active mode so padding can't drift from the math.
3. **Remove-tag** — add a DELETE endpoint, a × button on each tag pill, and one new DB helper. Follow the existing RBAC/vocab pattern.

**Tech Stack:** Flask/SQLite backend, pure-JS ES modules with Node fixture tests, Cytoscape.js (MIT, CDN), `html` tagged template for auto-escape.

**Spec dependencies:**
- Previous spec: `docs/superpowers/specs/2026-04-24-incident-graph-post-deploy-fixes-design.md`
- No new spec — this plan addresses a regression and a deferred feature from the same spec.

---

## File Structure

- `static/js/compute_centroids.mjs` — fix `clusterHalfHeight` formula, add `CYTO_HULL_PADDING_Y` per mode, export it
- `tests/js/test_compute_centroids.mjs` — update assertions with new half-height values + add one-lane worst case
- `static/js/incident_graph.js` — import `CYTO_HULL_PADDING_Y`, derive hull `padding` style string at runtime; remove-tag button on pills; race-safe DELETE
- `static/css/incident_graph.css` — new `.inc-tag-pill-remove` style
- `database/db_logger.py` — new `remove_inference_tag(inference_id, tag)` helper
- `mlss_monitor/routes/api_inferences.py` — extend existing `tags(inference_id)` route to handle `DELETE`
- `tests/test_event_tags.py` — add `test_remove_inference_tag` + `test_remove_inference_tag_idempotent`
- `tests/test_api_inferences.py` *(check if exists; if not, add delete tests to a relevant existing test file)* — add endpoint test

No DB schema change (table already exists, no migration needed).

---

## Task 1: Centroid math — correct the half-height formula

**Files:**
- Modify: `static/js/compute_centroids.mjs`
- Modify: `tests/js/test_compute_centroids.mjs`

### Step 1.1: Update fixture tests to match the new formula

In `tests/js/test_compute_centroids.mjs`, find the deep-rows test block. The current expectation `cd.D3.y - cd.D1.y >= 248` assumes the old (buggy) half-height 124. Update and add new assertions.

**Replace** the deep-rows block with:

```javascript
// --- Row height scales with stack depth (one-lane worst case) ------------
// clusterHalfHeight(primary, manual) =
//   LANE_HEIGHT_PX(44) + stackSlots * STACK_DY_PX(16) + CYTO_HULL_PADDING_Y(30)
// where stackSlots = min(MAX_STACK_STEPS, max(1, ceil((primary-1)/2)))
// For primary=30: stackSlots=min(5, ceil(29/2))=5, halfH = 44 + 80 + 30 = 154
// For primary=2:  stackSlots=min(5, ceil(1/2))=1,  halfH = 44 + 16 + 30 = 90
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

// No-overlap invariant — row spacing must accommodate full hull extent of
// both rows: delta >= half_h(r) + half_h(r+1).
// For deep rows half_h = 154: delta >= 308.
expect('deep rows: row1_y - row0_y >= 308 (2 * 154)',
  cd.D3.y - cd.D1.y >= 308, true);

// For shallow rows half_h = 90: delta >= 180.
expect('shallow rows: row1_y - row0_y >= 180 (2 * 90)',
  cs.S3.y - cs.S1.y >= 180, true);

// One-lane worst case — ten primaries all-same-severity pile into one
// lane and reach step ±5. halfHeight must match deep rows.
const oneLane = [
  { id: 'L1', alert_count: 10, primary_count: 10 },
  { id: 'L2', alert_count: 10, primary_count: 10 },
  { id: 'L3', alert_count: 10, primary_count: 10 },
  { id: 'L4', alert_count: 10, primary_count: 10 },
];
const cl = computeCentroids(oneLane, 'manual');
expect('one-lane 10 primaries: row1_y - row0_y >= 308',
  cl.L3.y - cl.L1.y >= 308, true);
```

### Step 1.2: Run the updated tests to verify RED

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_compute_centroids.mjs`

Expected: at least the `>= 308` assertions FAIL (current half-height = 108 for 10 primaries → gap ~216).

### Step 1.3: Update `compute_centroids.mjs`

In `static/js/compute_centroids.mjs`:

**A. Add `CYTO_HULL_PADDING_Y` to each MODES entry.** The three entries currently end in `INTER_ROW_GAP: 60` etc. Append one more field to each:

```javascript
const MODES = {
  manual: {
    MIN_WIDTH_PX:       360,
    PX_PER_ALERT:       32,
    HULL_PADDING_PX:    80,
    INTER_CLUSTER_GAP:  70,
    LANE_HEIGHT_PX:     44,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,
    CYTO_HULL_PADDING_Y: 30,
  },
  compact: {
    MIN_WIDTH_PX:       240,
    PX_PER_ALERT:       20,
    HULL_PADDING_PX:    60,
    INTER_CLUSTER_GAP:  40,
    LANE_HEIGHT_PX:     32,
    STACK_DY_PX:        14,
    INTER_ROW_GAP:      40,
    CYTO_HULL_PADDING_Y: 22,
  },
  chronological: {
    MIN_WIDTH_PX:       300,
    PX_PER_ALERT:       28,
    HULL_PADDING_PX:    70,
    INTER_CLUSTER_GAP:  50,
    LANE_HEIGHT_PX:     40,
    STACK_DY_PX:        16,
    INTER_ROW_GAP:      60,
    CYTO_HULL_PADDING_Y: 26,
  },
};
```

**B. Replace `clusterHalfHeight` body.** Find the current:

```javascript
function clusterHalfHeight(primaryCount, c) {
  const primary = Math.max(1, primaryCount || 0);
  const stackSlots = Math.min(MAX_STACK_STEPS, Math.ceil(primary / 3));
  return c.LANE_HEIGHT_PX + stackSlots * c.STACK_DY_PX;
}
```

Replace with:

```javascript
function clusterHalfHeight(primaryCount, c) {
  const primary = Math.max(1, primaryCount || 0);
  // Worst case: all alerts land in one severity lane. The stacker walks
  // STACK_STEPS [0, ±1, ±2, ...], so N alerts in one lane reach step
  // ±ceil((N-1)/2). Capped at MAX_STACK_STEPS. The previous ceil(N/3)
  // estimator assumed even 3-lane distribution and under-reserved by up
  // to 16px for same-severity cascades.
  const stackSlots = Math.min(
    MAX_STACK_STEPS,
    Math.max(1, Math.ceil((primary - 1) / 2)),
  );
  const contentHalf = c.LANE_HEIGHT_PX + stackSlots * c.STACK_DY_PX;
  // Cytoscape compound-node padding extends the hull beyond the child
  // bounding box. Include it so row spacing leaves room for the full
  // VISUAL hull, not just the placed alerts.
  return contentHalf + c.CYTO_HULL_PADDING_Y;
}
```

**C. Export `CYTO_HULL_PADDING_Y` per mode.** Replace the `export const MODES = ...` line to include `CYTO_HULL_PADDING_Y` constants directly accessible, and ALSO keep `MODES` (which already has each mode's `CYTO_HULL_PADDING_Y`).

No additional code needed — `MODES` is already exported and each entry now includes `CYTO_HULL_PADDING_Y`. Callers read it via `MODES[mode].CYTO_HULL_PADDING_Y`.

### Step 1.4: Run tests — expect GREEN

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_compute_centroids.mjs`

Expected: all tests pass (10 or 11 assertions, depending on count after edits).

### Step 1.5: Commit

```bash
git add static/js/compute_centroids.mjs tests/js/test_compute_centroids.mjs
git commit -m "fix(incidents): clusterHalfHeight covers one-lane stacks + cyto padding

Two compounding bugs caused the still-reported hull overlap on the Pi:

 1. clusterHalfHeight assumed even distribution across 3 severity lanes
    (ceil(primary/3)). Real incidents often stack into ONE lane (e.g. a
    TVOC cascade → all warning). For 10 same-severity primaries the
    stacker reaches step ±5 (80px offset), but the estimator said only
    ±4 (64px). Adjacent rows overlapped by 32px.

 2. Cytoscape compound-node 'padding: 30px 40px ...' extends the hull
    beyond the child bounding box. clusterHalfHeight never included
    this, so hull-to-hull gap could be 0 even when centroid math was
    'correct'.

Fix: one-lane worst-case slot estimator (ceil((primary-1)/2)), plus a
per-mode CYTO_HULL_PADDING_Y added to halfHeight so math and paint
agree."
```

---

## Task 2: Sync Cytoscape hull `padding` to the active mode

**Files:**
- Modify: `static/js/incident_graph.js` (the Cytoscape stylesheet function)

### Step 2.1: Import `MODES` already present — confirm

Grep: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && grep -n "import.*compute_centroids" static/js/incident_graph.js`
Expected: shows `MODES` already imported from Task 3.5 earlier work.

### Step 2.2: Update hull style to use mode-driven padding

In `static/js/incident_graph.js`, find the `buildCytoscapeStyle` function (starts around line 692). At the top of the function body, add a local accessor that reads the active mode's padding:

```javascript
function buildCytoscapeStyle() {
  const mode = MODES[viewMode] || MODES.manual;
  // Cytoscape's `padding` is "top|bottom horizontal" — mirror the two
  // MODES fields so padding paint matches clusterHalfHeight math.
  const hullPadding =
    `${mode.CYTO_HULL_PADDING_Y}px ${Math.round(mode.HULL_PADDING_PX / 2)}px`;
  return [
    // ... existing base node style and rest of the array ...
```

Then inside the `selector: 'node.hull'` rule (currently around lines 854-877), replace the hardcoded line:

```javascript
'padding': '30px 40px 30px 40px',
```

with:

```javascript
'padding': hullPadding,
```

(For manual mode this evaluates to `'30px 40px'`, matching the original visual. For compact it becomes `'22px 30px'`; for chronological `'26px 35px'`.)

### Step 2.3: Re-apply stylesheet when view mode changes

`setViewMode` currently calls `renderGraph`, which rebuilds elements but not styles. Update `setViewMode` in `static/js/incident_graph.js` (around line 49) so stylesheet refreshes too:

```javascript
function setViewMode(mode) {
  if (mode !== 'manual' && mode !== 'compact' && mode !== 'chronological') return;
  if (mode === viewMode) return;
  viewMode = mode;
  try { localStorage.setItem('inc.view_mode', mode); } catch (_) {}
  // Re-apply the Cytoscape stylesheet so the hull padding follows the
  // active mode, then re-render nodes/edges.
  if (cy) cy.style(buildCytoscapeStyle());
  if (currentDetail) renderGraph(currentDetail, allIncidents);
}
```

### Step 2.4: Verify

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_compute_centroids.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_connected_components.mjs
```

All green. (DOM-wiring change is not unit-testable; browser smoke test happens after deploy.)

### Step 2.5: Commit

```bash
git add static/js/incident_graph.js
git commit -m "fix(incidents): sync Cytoscape hull padding to active view mode

buildCytoscapeStyle now derives hull 'padding' from MODES[viewMode]
rather than a hardcoded '30px 40px ...' string. setViewMode re-applies
the stylesheet on mode change so the Compact mode's tighter padding
(22px) takes effect visually, matching what clusterHalfHeight budgets."
```

---

## Task 3: Remove-tag DB helper

**Files:**
- Modify: `database/db_logger.py`
- Modify: `tests/test_event_tags.py`

### Step 3.1: Write failing test

Append to `tests/test_event_tags.py`:

```python
def test_remove_inference_tag(db):
    """remove_inference_tag deletes rows matching (inference_id, tag)."""
    from database.db_logger import (
        add_inference_tag, get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", confidence=0.8, evidence={},
    )
    add_inference_tag(inf_id, "cooking")
    add_inference_tag(inf_id, "combustion")
    assert len(get_inference_tags(inf_id)) == 2

    remove_inference_tag(inf_id, "cooking")

    remaining = get_inference_tags(inf_id)
    assert len(remaining) == 1
    assert remaining[0]["tag"] == "combustion"


def test_remove_inference_tag_idempotent(db):
    """remove_inference_tag for a non-existent tag is a no-op."""
    from database.db_logger import (
        get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", confidence=0.8, evidence={},
    )
    # Should not raise
    remove_inference_tag(inf_id, "cooking")
    assert get_inference_tags(inf_id) == []


def test_remove_inference_tag_removes_all_duplicates(db):
    """If the same tag was added twice (no UNIQUE constraint), remove all."""
    from database.db_logger import (
        add_inference_tag, get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", confidence=0.8, evidence={},
    )
    add_inference_tag(inf_id, "cooking")
    add_inference_tag(inf_id, "cooking")
    assert len(get_inference_tags(inf_id)) == 2

    remove_inference_tag(inf_id, "cooking")
    assert get_inference_tags(inf_id) == []
```

### Step 3.2: Run test to verify RED

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_event_tags.py -v`
Expected: 3 new tests FAIL with `ImportError: cannot import name 'remove_inference_tag'`.

### Step 3.3: Add `remove_inference_tag` to `database/db_logger.py`

Append directly after `add_inference_tag` (around line 607 — the function ends with the ML-training `try` block).

```python
def remove_inference_tag(inference_id, tag):
    """Remove all rows matching (inference_id, tag) from event_tags.

    Idempotent — no error if nothing matches. Does NOT trigger ML
    retraining (training happens on add; removal just drops the
    supervised signal).
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM event_tags WHERE inference_id = ? AND tag = ?",
        (inference_id, tag),
    )
    conn.commit()
    conn.close()
```

### Step 3.4: Run tests — expect GREEN

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_event_tags.py -v`
Expected: all event-tags tests pass (5 total after additions).

### Step 3.5: Commit

```bash
git add database/db_logger.py tests/test_event_tags.py
git commit -m "feat(db): remove_inference_tag helper for event_tags

Removes all rows matching (inference_id, tag). Idempotent. No ML
retraining side-effect (add_inference_tag already owns that)."
```

---

## Task 4: DELETE endpoint on `/api/inferences/<id>/tags`

**Files:**
- Modify: `mlss_monitor/routes/api_inferences.py`
- Modify: `tests/test_api_inferences.py` (check existence first; if missing add here) OR `tests/test_api_history.py`

### Step 4.1: Check whether a test file exists for api_inferences

Run: `ls tests/test_api_inferences.py 2>&1`
Expected: either exists or "No such file".

If the file doesn't exist, create `tests/test_api_inferences.py` with a minimal client fixture (copy the pattern from `tests/test_api_incidents.py:35-50` which pre-authenticates as admin). If it exists, append to it.

### Step 4.2: Write failing endpoint test

Add to `tests/test_api_inferences.py`:

```python
def test_delete_inference_tag_removes_row(client, db):
    """DELETE /api/inferences/<id>/tags with body {tag: ...} removes it."""
    import sqlite3
    from database.db_logger import save_inference, add_inference_tag, get_inference_tags

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", confidence=0.8, evidence={},
    )
    add_inference_tag(inf_id, "cooking")
    add_inference_tag(inf_id, "combustion")
    assert len(get_inference_tags(inf_id)) == 2

    resp = client.delete(
        f"/api/inferences/{inf_id}/tags",
        json={"tag": "cooking"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    remaining = get_inference_tags(inf_id)
    assert len(remaining) == 1
    assert remaining[0]["tag"] == "combustion"


def test_delete_inference_tag_missing_tag_body(client, db):
    from database.db_logger import save_inference
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", confidence=0.8, evidence={},
    )
    resp = client.delete(f"/api/inferences/{inf_id}/tags", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("error") == "tag is required"
```

If creating a new file, prepend with the client fixture matching `tests/test_api_incidents.py`:

```python
import os, sqlite3, tempfile
import pytest
from mlss_monitor.app import create_app


@pytest.fixture
def db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DB_FILE", path)
    from database.init_db import init_db as _init
    _init(path)
    yield path
    os.unlink(path)


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("DB_FILE", db)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = 1
            sess["user_role"] = "admin"
        yield c
```

### Step 4.3: Run tests — expect RED

Run: `cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_inferences.py -v`
Expected: 405 Method Not Allowed (DELETE not in the route's methods list yet).

### Step 4.4: Extend the existing route to accept DELETE

In `mlss_monitor/routes/api_inferences.py`, find the `tags(inference_id)` route (line 95-124). Update the decorator and add the DELETE branch:

**A.** Add `remove_inference_tag` to the imports at the top:

```python
from database.db_logger import (
    dismiss_inference,
    get_distinct_attribution_sources,
    get_inferences,
    get_inference_by_id,
    update_inference_notes,
    get_inference_tags,
    add_inference_tag,
    remove_inference_tag,
    get_sensor_data_range,
    get_hot_tier_range,
    _normalise_ts,
)
```

**B.** Extend the methods list and add the branch. Replace the existing route decorator + function:

```python
@api_inferences_bp.route(
    "/api/inferences/<int:inference_id>/tags",
    methods=["GET", "POST", "DELETE"],
)
@require_role("controller", "admin")
def tags(inference_id):
    if request.method == "GET":
        tags_list = get_inference_tags(inference_id)
        return jsonify(tags_list)
    if request.method == "POST":
        data = request.get_json(force=True)
        tag = data.get("tag", "").strip()
        confidence = data.get("confidence", 1.0)
        if not tag:
            return jsonify({"ok": False, "error": "tag is required"}), 400

        # Validate against controlled vocabulary when engine is available.
        from mlss_monitor import state as _state  # pylint: disable=import-outside-toplevel
        _engine = _state.detection_engine
        allowed = (
            _engine._attribution_engine.valid_tags
            if _engine and _engine._attribution_engine
            else None
        )
        if allowed is not None and tag not in allowed:
            return jsonify({
                "error": "invalid_tag",
                "valid_tags": sorted(allowed),
            }), 400

        add_inference_tag(inference_id, tag, confidence, allowed_tags=allowed)
        return jsonify({"ok": True})
    if request.method == "DELETE":
        data = request.get_json(silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"error": "tag is required"}), 400
        remove_inference_tag(inference_id, tag)
        return jsonify({"ok": True})
    return jsonify({"error": "method not allowed"}), 405
```

### Step 4.5: Run tests — expect GREEN

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/test_api_inferences.py -v
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
```

Expected: all pass.

### Step 4.6: Commit

```bash
git add mlss_monitor/routes/api_inferences.py tests/test_api_inferences.py
git commit -m "feat(api): DELETE /api/inferences/<id>/tags removes a tag

Body: {tag: string}. Returns 200 with {ok: true} on success, 400 if
tag is missing. RBAC-guarded controller/admin, same as POST."
```

---

## Task 5: Remove-tag × button in incident overlay

**Files:**
- Modify: `static/js/incident_graph.js` (the `populateTagsSection` function + the pill rendering)
- Modify: `static/css/incident_graph.css` (append pill-remove button style)

### Step 5.1: Update pill rendering to include the × button

In `static/js/incident_graph.js`, find the pill-rendering block inside `populateTagsSection` (around the `listEl.innerHTML = html\`${current.map(t => { ... })}\`` call). Replace the pill render with:

```javascript
    listEl.innerHTML = html`${current.map(t => {
      const label = (vocab.find(v => v.id === t.tag) || {}).label || t.tag;
      const emoji = TAG_EMOJI[t.tag] || '';
      return html`<span class="inc-tag-pill" data-tag="${t.tag}">
        ${emoji} ${label}
        <button type="button" class="inc-tag-pill-remove"
                data-tag="${t.tag}"
                aria-label="Remove tag ${label}"
                title="Remove tag">×</button>
      </span>`;
    })}`;
```

### Step 5.2: Wire × click handlers after rendering

Directly after the `listEl.innerHTML = ...` assignment for the populated-pills case, add a click listener for the × buttons. Still inside `populateTagsSection`:

```javascript
    // Wire the × remove handlers on each pill.
    listEl.querySelectorAll('.inc-tag-pill-remove').forEach(btn => {
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const tag = btn.dataset.tag;
        if (!tag) return;
        statusEl.textContent = 'Removing…';
        try {
          const resp = await fetch(`/api/inferences/${alertId}/tags`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag }),
          });
          if (!resp.ok) {
            statusEl.textContent = `Remove failed (${resp.status})`;
            return;
          }
          statusEl.textContent = 'Removed';
          await populateTagsSection(alertId);
        } catch (e) {
          statusEl.textContent = 'Network error — try again.';
        }
      };
    });
```

The recursive `populateTagsSection(alertId)` call bumps `lastTagFetchToken` and re-reads everything — same pattern the add handler uses. Race-safe.

### Step 5.3: Append CSS

Append to `static/css/incident_graph.css`:

```css
.inc-tag-pill {
  /* Existing rule already present — extend with flex for the × button. */
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.inc-tag-pill-remove {
  background: transparent;
  border: none;
  color: rgba(207, 232, 255, 0.55);
  font-size: 0.85rem;
  line-height: 1;
  padding: 0 2px;
  margin-left: 2px;
  cursor: pointer;
  border-radius: 50%;
}
.inc-tag-pill-remove:hover {
  color: #ff7a7a;
  background: rgba(255, 122, 122, 0.15);
}
.inc-tag-pill-remove:focus {
  outline: 1px solid rgba(77, 172, 255, 0.7);
  outline-offset: 1px;
}
```

**Note:** the existing `.inc-tag-pill` rule does NOT have `display: inline-flex` yet. To avoid duplicating the selector, edit the existing `.inc-tag-pill` rule (search `static/css/incident_graph.css` for `.inc-tag-pill {`) to add the three `display`/`align-items`/`gap` lines, rather than re-declaring the selector.

### Step 5.4: Verify

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_compute_centroids.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_connected_components.mjs
```

All green. Browser smoke test on the Pi after deploy.

### Step 5.5: Commit

```bash
git add static/js/incident_graph.js static/css/incident_graph.css
git commit -m "feat(incidents): remove-tag × button on tag pills

Each pill in the node overlay now has a × button that DELETEs the
tag and re-renders the section. Uses the same race-token pattern as
add-tag and reuses the /api/inferences/<id>/tags endpoint (now also
accepting DELETE). aria-label + focus ring for a11y."
```

---

## Verification (after all tasks)

```
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pytest tests/ -q
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_compute_centroids.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && node tests/js/test_connected_components.mjs
cd /c/Users/wolfs/OneDrive/Documents/GitHub/mars-air-quility && python -m pylint --disable=import-error,no-name-in-module \
  database/db_logger.py \
  mlss_monitor/routes/api_inferences.py \
  tests/test_event_tags.py
```

Expected:
- pytest ≥ 734 passed (added 3 db tests + 2 endpoint tests)
- Both Node tests green
- pylint 10.00/10 on touched Python files (current baseline)

Browser smoke test on the Pi (`git pull && sudo systemctl restart mlss-monitor`):
- Manual view: no hull overlap even with same-severity cascades of 10+ primaries.
- Compact view: denser grid, hulls still fully separated.
- Chronological view: single row, no overlap with adjacent clusters.
- Click an alert node → overlay has tag pills with × buttons → clicking × removes the tag and the pill disappears.
- Add-tag flow still works end-to-end.

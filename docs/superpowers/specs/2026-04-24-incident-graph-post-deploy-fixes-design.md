# Incident Graph: Post-Deploy Fixes — Design

**Date:** 2026-04-24
**Branch:** `feature/incident-correlation-graph` (continues on top of the already-deployed causal-DAG work)

## Context

The causal-DAG incident grouping feature (21-task plan, 5 post-review fixes) was merged and deployed to the Pi at `https://192.168.0.203:5000/incidents`. Live testing surfaced four issues. This spec addresses all four as one cohesive follow-up change — they share files and reviewers, so bundling reduces churn.

## Goal

Make the incidents graph page usable for operators on the deployed build: hulls must not overlap, operators must be able to tag events for ML learning, and the layout controls must not hide functionality behind a wide dropdown of layouts that don't fit the causal-chain viewing task.

## Problem 1: Hull overlap

**Observed:** Incident hulls visually cross each other on the canvas.

**Root cause (from `static/js/incident_graph.js:buildCentroids` + `buildIncidentElements`):**
`GRID_SPACING_Y = 220px` between cluster centres is a fixed constant. In `buildIncidentElements`:
- Lane y-offset from centre: `-LANE_HEIGHT_PX + lane * LANE_HEIGHT_PX` → values `−44, 0, +44` for critical/warning/info.
- Stack y-offset: `step * STACK_DY_PX` where `step ∈ [−5, +5]`, `STACK_DY_PX = 16` → max `±80`.
- Worst-case alert offset from cluster centre: `44 + 80 = 124px` (info lane, deepest stack downward; symmetrically upward for critical).
- Total worst-case vertical extent: `2 × 124 = 248px`.

With only 220px between row centres, two adjacent rows with deep stacks overlap by up to **28px**. The new causal grouping produces larger incidents than the old temporal grouping (transitive chains link many alerts), so deep stacks now occur routinely.

**Fix (Option B — agreed):** Compute each row's required height dynamically from the expected stack depth of its clusters. Small rows stay tight; tall rows get the space they need.

**Approach:**
- Backend: add `primary_count` (int) to each incident in the `/api/incidents` list response. One extra column in the existing `_alert_counts_by_incident` query — counts `WHERE is_primary = 1`. Small, safe change.
- Frontend `buildCentroids`:
  - Per cluster, compute **required half-height**:
    `half_h = LANE_HEIGHT_PX + min(5, ceil(primary_count / 3)) * STACK_DY_PX`
    i.e. 44 + up-to-80 = 44..124 px.
    Rationale: stack depth is bounded by alerts per lane; with 3 lanes and even distribution, max slot used ≈ `primary_count / 3`, capped at the 5-step `STACK_STEPS` limit.
  - Per row, `row_half_h[r] = max(half_h for clusters in row r)`.
  - Row centre Y: `row_centre[r] = row_centre[r−1] + row_half_h[r−1] + row_half_h[r] + INTER_ROW_GAP`.
  - `INTER_ROW_GAP` = 60px (new constant — matches existing `INTER_CLUSTER_GAP` feel).
  - `MIN_HALF_HEIGHT` = 60px floor so single-alert incidents don't collapse the gap.

**Why this over (A):** Fixed 400px gap would waste vertical space on pages with many small incidents. Dynamic sizing keeps density + correctness.

**Why not (C):** Single-lane-per-incident changes the visual DNA and loses severity information at a glance.

## Problem 2: No tagging control on incident events

**Observed:** Operators cannot tag an alert with a feedback label (e.g. "false positive", "known cause"), so the attribution engine never learns from incident views.

**Existing pattern (from `templates/history.html`):**
- `<select id="infTagSelect">` populated at page load from `GET /api/tags`
- `<button id="infAddTag">Add Tag</button>` POSTs `{tag, confidence: 1.0}` to `/api/inferences/<alert_id>/tags`
- Existing tags render as pills in `#infTagsList`

**Endpoints (already exist, no backend change needed):**
- `GET /api/tags` — valid-tag vocabulary
- `GET /api/inferences/<id>/tags` — existing tags on this alert
- `POST /api/inferences/<id>/tags` — add tag (requires controller/admin)

**Fix:** Mirror the history-page pattern inside the node overlay (`#inc-node-overlay`). When the overlay opens for an alert node, render:
1. The existing metadata table (unchanged)
2. A "Tags" section containing: existing tags as pills + a `<select>` + "Add Tag" button

**Scope limits:**
- Tagging only. No dismiss, no notes. (Agreed with user.)
- Only shown when the overlay is for an `alert` node. Cross-incident alerts (`hourly_summary`, `daily_pattern`, `annotation_context_*`) are hidden from tagging to avoid polluting the ML signal with meta-events — operators tag root causes, not summaries.
- Tags are fetched per-alert on overlay open (small, fast — same pattern as history).

## Problem 3: "Change layout" dropdown too wide

**Observed:** The `<select id="inc-layout-alt">` dropdown at the top of the toolbar is visually dominant because the native select auto-sizes to fit its widest option ("Physics — force-directed", 22 chars).

**Fix:** Folds into Problem 4. Once the dropdown is replaced by purpose-built buttons with short labels, the visual issue disappears.

## Problem 4: Layout options don't fit causal-chain viewing

**Observed:** Every Cytoscape alt layout (`cose`, `breadthfirst`, `circle`, `grid`, `concentric`) scatters nodes algorithmically without incident-hull awareness. They destroy the mental model the manual timeline builds.

**Fix (agreed):** Remove all five alt layouts. Replace with three purpose-built **view modes** that all honor the incident-hull structure:

| Mode | Behavior |
|---|---|
| **Manual** *(default)* | Current timeline layout — `x = minutes from incident start`, `y = severity lane` |
| **Compact** | Same layout with tighter constants: `PX_PER_ALERT = 20` (was 32), `MIN_CLUSTER_WIDTH = 240` (was 360), `INTER_CLUSTER_GAP = 40` (was 70), smaller `LANE_HEIGHT_PX = 32`. For scanning many incidents at once |
| **Chronological** | One row only. Clusters sorted by `started_at`, placed left→right. Uses the current per-cluster width formula but always row 0. For "what happened when" reading |

**Implementation:**
- Delete `runLayout()`'s cose/breadthfirst/circle/grid/concentric branches.
- Delete `<select id="inc-layout-alt">` from template and its wiring in `initToolbar`.
- Add three buttons styled like the existing `.inc-layout-btn` (`data-layout="preset"`).
- View mode persists per-user in `localStorage` (`inc.view_mode`), matching how `edge_p_floor` is persisted.
- All three modes re-run the same `buildCentroids`/`buildIncidentElements` pipeline with different constant sets — no new layout engine, no scatter-node code.

## Non-goals

- No "Focus" mode (selected incident enlarged, others faded). Adds state complexity; revisit if asked.
- No change to the grouper, edges, split/unsplit, or backend APIs.
- No Cytoscape library version bump.

## Files touched

| File | Change |
|---|---|
| `mlss_monitor/routes/api_incidents.py` | Add `primary_count` to each incident in the list response (one extra COUNT) |
| `tests/test_api_incidents.py` | Extend listing tests to assert `primary_count` present and correct |
| `static/js/incident_graph.js` | `buildCentroids` dynamic row height; delete alt-layout branches in `runLayout`; add view-mode constants + persistence; render tagging section in `showNodeOverlay` |
| `static/css/incident_graph.css` | Add `.inc-tags-section`, `.inc-tag-pill`, `.inc-tag-controls` styles (or re-use history page classes if reasonable) |
| `templates/incidents.html` | Remove `<select id="inc-layout-alt">`; add 2 more `.inc-layout-btn` buttons (Compact, Chronological) |
| `tests/js/test_view_modes.mjs` *(new)* | Node fixture tests for `buildCentroids` dynamic row height — pure function, no DOM |

One small backend change (add `primary_count` to list endpoint). No new routes. No DB migrations.

## Testing strategy

- **Issue 1 (overlap):** Extract the centroid math into a pure function (`computeCentroids(incidents, viewMode) -> {id: {x,y}, __crossBandY}`) — already nearly pure, just needs the per-view-mode constants broken out. Node fixture tests verify: (a) deep-stack rows get more Y than shallow-stack rows; (b) hulls' vertical extents don't overlap at the documented worst case (primary_count = 30, half_h = 124, two rows); (c) chronological mode puts every cluster on row 0; (d) compact mode produces narrower widths than manual for the same inputs.
- **Issue 2 (tagging):** Manual test on Pi (can't unit-test DOM easily in this project's setup). A smoke test that the existing tagging endpoints still pass is already in `tests/test_api_tags.py` — no regression risk on the backend.
- **Issue 4 (view modes):** Unit test `computeCentroids` with the three different constant sets — verify compact mode narrows widths, chronological mode puts all clusters on row 0.

## Risks

- `buildCentroids` is called from `rebuildGraph` on every incident-list refresh. Making it compute per-row heights adds an O(N) scan — negligible at realistic N (<200 incidents).
- `showNodeOverlay` was previously pure-render-from-data; adding an async tag fetch introduces a race if the user clicks one node and then another before the fetch returns. Mitigation: capture the currently-expected alert ID, check on response that it still matches, drop otherwise.
- Removing alt layouts is destructive — users may have bookmarked/expected them. Acceptable: they don't work anyway.

## Rollback

All changes are frontend-only. `git revert` on the implementation commits restores prior UX.

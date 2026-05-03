# Incident Page — Multi-Section Dashboard

**Date:** 2026-04-29
**Branch:** `feature/incident-correlation-graph` (continues current branch)
**Supersedes:** the timeline-in-hull centre canvas (`compute_centroids.mjs`, view-mode buttons, grid-of-hulls layout)

## Goal

Replace the single-canvas grid-of-hulls with four time-windowed sub-sections, each surfacing a different latent signal in the data: incident-shape similarity, daily rhythm, time × sensor unfolding, and sensor co-occurrence. Every section respects the active time window and severity filter — no all-time data anywhere in the centre canvas.

## Why this is happening

Previous design assumed `alert_signal_deps` would densely populate; in production it's mostly empty. Causal edges between primary alerts therefore almost never fire, and the per-incident graph reads as "isolated dots in a hull" rather than a connected network. Live probing of the deployed Pi confirmed: ALL multi-alert incidents in the recent window returned 0 causal edges. The grid-of-hulls visualisation has no signal to display.

The four sub-sections each pull from a data source that IS populated:

| Section | Data source (already exists) |
|---|---|
| Galaxy | `incidents.signature` 32-d vectors |
| Rose | `incidents.started_at` |
| Storyline | per-incident alerts list (with `event_type` → sensor channel) |
| Co-occurrence | same alerts list, aggregated pairwise |

## Architecture

The page keeps its 3-column layout: incident list (left, 200 px), centre dashboard, detail panel (right, 260 px). The centre dashboard becomes a vertically stacked column of three sub-section bands, separated by 1-px borders:

1. **Top band** (200 px): `Galaxy` (incident similarity) + `Rose` (daily rhythm) side by side.
2. **Middle band** (240 px): `Storyline` subway map.
3. **Bottom band** (200 px): `Sensor co-occurrence network`.

All four are pure-frontend visualisations driven by the data already returned by `/api/incidents` (list view) plus a new optional batched-detail endpoint for Storyline.

The toolbar above the dashboard keeps: search, time-window pills (15m / 1h / 6h / 12h / 24h / 14d), severity pills (all / critical / warning / info), and the existing "Hide weak links" slider. The view-mode buttons (Manual / Compact / Chronological) are removed.

The right detail panel is **unchanged** — narrative + causal sequence + similar incidents + node overlay (with tagging + remove-tag). Storyline's "click a curve to select an incident" plumbs into the existing `loadIncidentDetail(id)` flow.

## Cross-section interactions

Single mental model: any click in any sub-section is a filter or selection that propagates to the others.

- **Galaxy dot click** → that incident becomes selected (right panel updates, Storyline curve goes solid blue, Rose wedge highlights, sensor nodes light up).
- **Rose wedge click** → temporary "filter chip" on the toolbar restricting all sub-sections to that hour-of-day. Click chip to clear.
- **Storyline curve hover** → mini-tooltip with incident id and start time. Click → select.
- **Co-occurrence sensor node click** → adds a "filter chip" for that sensor; Storyline lane highlights, Galaxy dots that touched the sensor remain coloured, others fade, list filters.

Filter chips live as pills next to the severity pills in the toolbar. Chips persist for the session only (not in localStorage); clearing the time-window selector clears them.

## Section detail

### ① Galaxy — incident similarity

**Plot:** 2-D scatter. Each incident in the active window is a dot. Coordinates from a PCA projection of the 32-d `incidents.signature` vectors (if vectors absent or all zeros, fall back to a small-multiples severity-vs-time grid). Dot colour = `max_severity`. Dot radius = `alert_count`. Selected incident gets a thicker stroke ring.

**Interpretation tooltip** (auto-wrap via `info-icon` title):
> "Each dot is one incident in the current window. Distance between dots = how similar their sensor signatures are. Clusters reveal recurring incident shapes (cooking, ventilation, humid drift). Outliers sit alone."

**Data:** existing `/api/incidents` returns each incident's `signature` field (currently dropped client-side in `list_incidents` line 161 — needs to be retained).

**Compute:** PCA in JS — hand-rolled power-iteration, ≤200 incidents × 32-d, sub-millisecond. ~40 lines, no new dependency. Pure ES module so it's Node-fixture-testable.

**Empty state:** if `<2` incidents in window, show "Need at least 2 incidents to compute similarity. Try a wider window."

### ② Rose — daily rhythm

**Plot:** 24-wedge polar bar chart. Wedge length = incident count for that hour-of-day; wedge colour = max severity in that hour. Two concentric rings if window > 7d (split weekday / weekend); single ring otherwise.

**Interpretation tooltip:**
> "Incidents binned by the hour they started (UTC). Long red wedges = critical-severity peaks at that hour. Use this to spot daily routines — e.g. cooking spikes around 18:00, sleep-time stillness at 03:00."

**Data:** existing `/api/incidents` already returns `summary.hour_histogram` — a 24-array of counts. Need to extend the summary to include severity-per-hour (max severity in each bucket).

**Compute:** SVG path-element-per-wedge. Standard polar transformation: angle = hour × 15°.

**Empty state:** "No incidents in this window."

### ③ Storyline — subway map

**Plot:** Wide rectangular SVG. Horizontal axis = time, spanning the active window. Six horizontal lanes, one per major sensor channel (TVOC, eCO₂, CO, PM₂.₅, Humidity, Temperature). Each primary alert renders as a dot in its sensor's lane at its created_at x-coordinate. Alerts in the same incident are joined by a smooth bezier curve weaving between lanes. Selected incident's curve is solid blue; others are dashed grey at 50% opacity.

The "Hide weak links" slider applies to the curves — same continuous-opacity formula as today (`clamp(p × (1 − 0.7 × floor), 0.10, 1.0)`). Below floor → dotted ghost.

**Interpretation tooltip:**
> "Time runs left to right. Each row is a sensor; each dot is an alert when that sensor crossed a threshold or anomaly. Curves connect alerts in the same incident — the curve's shape IS the incident's signature. Click a curve to focus that incident; its curve solidifies and others recede."

**Data:** new endpoint `/api/incidents/storyline?window=24h&severity=all` returning `{incidents: [{id, started_at, max_severity, alerts: [{id, created_at, event_type, severity}], edges: [{from, to, p, causal}]}]}`. Replaces N+1 detail fetches.

**Sensor mapping:** reuse `_RULE_CHANNEL_MAP` from `mlss_monitor/routes/api_inferences.py` (already maps `event_type → [channel, ...]`). Cross-incident alert types (hourly_summary, daily_pattern, annotation_*) hidden from Storyline — they live in the right panel only.

**Empty state:** "No primary alerts in this window."

### ④ Sensor co-occurrence network

**Plot:** Force-directed graph. Nodes = sensor channels (one per channel that has at least one alert in the active window). Edges = pairs of sensors that fired alerts within ±5 minutes of each other; edge thickness = co-fire count. Node radius = total alert count for that sensor. Gas sensors (TVOC, eCO₂, CO) on a top half-circle; environmental (Humidity, Temperature, PM, VPD) on the bottom. Edges below the slider's P-floor recede to dotted-grey.

**Interpretation tooltip:**
> "Each circle is a sensor channel; its size = how often it fired in this window. Lines join sensors that fired together (within 5 minutes). Thick lines = strong co-occurrence — e.g. TVOC + eCO₂ together usually means cooking. Drag the 'Hide weak links' slider to clarify."

**Data:** same alerts list as Storyline. Pure JS aggregate: bucket alert pairs by `(sensor_a, sensor_b, time_bucket)` where time_bucket = `floor(t / 5min)`. O(N²) over alerts in window — fine for N ≤ a few hundred.

**Compute:** layout via cytoscape `circle` algorithm (already loaded). 1 node-per-sensor max ~10, edges max ~45 — instant.

**Empty state:** "No primary alerts in this window."

## Toolbar changes

| Existing | Status |
|---|---|
| Search input | keep |
| Window pills (15m → 14d) | keep |
| Severity pills (all / crit / warn / info) | keep |
| Hide-weak-links slider | keep, drives Storyline + Co-occurrence simultaneously |
| Severity pills count badges | keep |
| Manual / Compact / Chronological view buttons | **remove** |
| Top sensors / Incidents-by-start-hour summary strip | **remove** (content lives in Galaxy + Rose now) |
| Symbol key strip | **remove** (replaced by per-section legends) |
| Custom minimap | **remove** (no full-canvas Cytoscape graph anymore) |

New: filter chips area (right of severity pills) for the cross-section drill-down. Empty by default; chips appear when user clicks a Rose wedge or Co-occurrence sensor.

## AstroUXDS tooltip pattern

Each section header gets the existing `<span class="info-icon" title="...">ⓘ</span>` pattern. The `base.html` DOMContentLoaded script already wraps these into `<rux-tooltip>` web components. No new code needed; just consistent use.

Pattern:
```html
<div class="inc-section-title">
  ① Incident similarity galaxy
  <span class="info-icon" title="Each dot is one incident...">ⓘ</span>
</div>
```

The same approach applies to legends inside each section (e.g. "Why is this dot bigger?" "Dot radius = alert count").

## Files to create / modify / delete

### Create
- `static/js/sections/galaxy.mjs` — PCA projection + render
- `static/js/sections/rose.mjs` — polar-bar render
- `static/js/sections/storyline.mjs` — subway-map render
- `static/js/sections/cooccurrence.mjs` — force-directed render
- `static/js/sections/sensor_map.mjs` — single source of truth for `event_type → sensor_channel` mapping (mirrors backend's `_RULE_CHANNEL_MAP`)
- `static/js/pca.mjs` — pure 2-D PCA helper, fixture-tested
- `tests/js/test_pca.mjs` — Node fixture tests
- `tests/js/test_sensor_map.mjs` — fixture tests for the mapping
- `mlss_monitor/routes/api_incidents.py::storyline_data` — batched detail endpoint
- `tests/test_api_incidents.py::test_storyline_endpoint_*` — new tests

### Modify
- `templates/incidents.html` — replace centre-pane HTML with the four-section layout; remove view-mode buttons / minimap / symbol key / summary strip
- `static/js/incident_graph.js` — gut the centroid / Cytoscape / view-mode plumbing; replace with section orchestration (load data once, dispatch to four section modules)
- `static/css/incident_graph.css` — new `.inc-section-*` classes, retire `.inc-graph-*` rules tied to the grid
- `mlss_monitor/routes/api_incidents.py::list_incidents` — extend `summary` block with severity-per-hour and stop dropping `signature` from the per-incident dicts
- `tests/test_api_incidents.py` — update existing list-summary tests for the extended shape

### Delete
- `static/js/compute_centroids.mjs` — orphaned after grid-of-hulls retires
- `static/js/connected_components.mjs` — only callers were Storyline-preview features inside the grid; keep for now (subdivision preview folds into Storyline as a future enhancement) **OR** delete if Storyline doesn't end up using it. Decision deferred to plan.
- `tests/js/test_compute_centroids.mjs`
- `tests/js/test_connected_components.mjs` (matching)

## Backend impact

One new GET endpoint: `/api/incidents/storyline?window=...&severity=...`. Response:

```json
{
  "incidents": [
    {
      "id": "INC-...",
      "started_at": "...",
      "max_severity": "warning",
      "alerts": [
        {"id": 123, "created_at": "...", "event_type": "anomaly_tvoc_ppb", "severity": "warning", "is_primary": 1}
      ],
      "edges": [{"from": 123, "to": 124, "p": 1.0, "causal": false}]
    }
  ]
}
```

Built from the same SQL the existing `get_incident` uses, scoped to the window+severity filter, returning ONLY the fields Storyline needs (no narrative, no similar-incidents, no signal_deps detail). Reuses the `temporal_edge_probability` we extracted previously.

`list_incidents` extension: add `severity_by_hour: int[24]` to the summary (where each value is the integer severity rank 0..2 of the most severe incident starting in that hour). Two backend changes total.

## Testing strategy

- **PCA module**: Node fixture tests with hand-checked 2×2 matrices, identity, single-component edge cases, and a known 3-cluster synthetic dataset.
- **Sensor map module**: Node fixture tests asserting every backend `_RULE_CHANNEL_MAP` event type is covered; failing tests block CI when backend adds new event types.
- **Storyline endpoint**: pytest integration tests with seeded incidents — verify shape, window filter, severity filter, edge probabilities populated.
- **Galaxy / Rose / Storyline / Co-occurrence renders**: DOM-bound, not unit-tested; manual smoke test via the live Pi after each task lands.
- **Hour-of-day severity histogram**: pytest assertion on the new field.

## Risks

- **PCA on near-zero signature vectors** — many incidents may have signatures that are mostly zeros (since the engine is still warming up). Mitigation: detect "all signatures < ε" case and fall back to a 2-D layout by `(started_at, max_severity)` instead. Visible UX state, not silent failure.
- **Storyline edge density** — with 200 incidents in 14 d and ~3 alerts each, ~600 dots and ~600 curves. SVG can handle it; canvas-render fallback only if profiling shows lag.
- **AstroUXDS rux-tooltip lazy-load** — the auto-wrap script in `base.html` runs at DOMContentLoaded; sections rendered by JS after that won't get auto-wrapped. Plan: have each section's render call a small `wrapTooltips(rootElement)` helper that runs the same logic against its own DOM subtree.
- **Removed view-mode buttons** — operators bookmarked them. Acceptable churn — they didn't provide functional value, and the new layout shows more information at once.

## Rollback

All changes confined to the `/incidents` page. Backend additions are additive (new endpoint, new field). `git revert` on the implementation commits restores the previous design without data loss.

## Non-goals

- No dashboard configurability (operators can't reorder or hide sections in v1).
- No tablet/phone-specific layout — the page already requires ≥1080 p; we don't promise narrower viewports.
- No animated transitions between selections — flat update on click.
- No long-term history view (no all-time data anywhere).
- No new chart libraries — Cytoscape stays for Co-occurrence (already loaded), everything else is hand-rolled SVG.

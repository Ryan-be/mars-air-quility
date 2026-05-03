# Causal Incident Grouping — Design

**Status:** Brainstormed 2026-04-23 with user. Ready for implementation planning.

**Supersedes:** The `sessionise` + `merge_similar_adjacent` grouping in `mlss_monitor/incident_grouper.py` (commits shipped 2026-04-23 on `feature/incident-correlation-graph`). Both functions are to be removed.

## Problem

The current grouping algorithm is purely temporal: alerts more than 30 minutes apart start a new incident (`sessionise`), with an optional refinement pass that merges adjacent time-sessions when their event-type sets overlap (`merge_similar_adjacent`). This fails the core product question: **what makes an incident meaningful to an operator?**

Under the current rules, two alerts that happen to fire close in time are grouped even if they have different causes; a single long-running event that goes quiet for 31 minutes is fragmented into two "incidents"; and the "similar past incidents" feature compares events that have nothing in common besides their time-of-day. The narrative panel, correlation explanation, and causal ribbon all operate on these time-coherent-but-cause-incoherent groups, so their outputs are shallower than they should be.

## Goals

- **Grouping by cause, not by clock.** Two alerts belong in the same incident if there is empirical evidence in the sensor data that they share a cause.
- **Cascades stay together.** Human-activity → rising CO₂ → HVAC response should be one incident, even though the HVAC alert fires 45 minutes after the human-activity signal and shows a different primary sensor.
- **Unrelated simultaneity stays apart.** A cooking event and an HVAC cycle that happen to fire within the same 30-minute window are two incidents.
- **Transparency.** An operator can see the evidence for every link and the overall confidence of the cluster. When the algorithm's judgement is wrong, the operator can split an incident with one click.

## Non-goals

- Fingerprint/attribution-based grouping. The `inferences.attribution_source` column is used as *displayed metadata* inside the narrative, not as a grouping key. Operators sometimes need to know "this incident contains alerts attributed to two different sources" (a cascade), so attribution divergence must not force a split. See the "Why not attribution-first" note at the bottom.
- Real-time regrouping on every incoming alert. Regrouping continues to run on a background-thread schedule as today, triggered by new inferences and the safety-net interval.
- Client-side recomputation of incident boundaries when the operator drags the view slider. The slider filters display only — incident membership is fixed by the server.

## The algorithm

### Step 1: pairwise edge probability

For every ordered pair of primary alerts (A, B) where A fires before B:

```
def edge_probability(A, B) -> float:
    # 1. Sensors-share-with-matching-sign (rule (c) from the brainstorm)
    strong_a = {(s, sign(r)) for (s, r) in A.signal_deps if abs(r) >= 0.5}
    strong_b = {(s, sign(r)) for (s, r) in B.signal_deps if abs(r) >= 0.5}
    if not (strong_a & strong_b):
        return 0.0

    # 2. Time-decay
    gap_min = (B.created_at - A.created_at).total_seconds() / 60
    if gap_min <= 30:
        return 1.0
    if gap_min >= 240:
        return 0.0
    return (240 - gap_min) / 210      # linear decay 1.0 -> 0.0 over 30-240 min
```

Constants (module-level, named):
- `EDGE_FULL_P_WINDOW_MINUTES = 30` — below this, probability is 1.0
- `EDGE_ZERO_P_WINDOW_MINUTES = 240` — at or above this, probability is 0.0
- `EDGE_STRONG_R_THRESHOLD = 0.5` — a sensor is "strongly involved" in an alert if |r| ≥ this

### Step 2: server-side floor

To avoid persisting near-zero chains, the grouper rejects edges below a floor:

```
MIN_EDGE_P_SERVER = 0.05          # edges below this are treated as absent
```

This is a fixed code constant, not configurable at runtime. (View-side filter gives operators the runtime control — see Step 5.)

### Step 3: operator split markers

An operator can mark an alert as "starts a new incident". Markers live in a new `incident_splits` table:

```sql
CREATE TABLE IF NOT EXISTS incident_splits (
    alert_id     INTEGER PRIMARY KEY REFERENCES inferences(id) ON DELETE CASCADE,
    created_by   TEXT,              -- session_user or NULL if unauth context
    created_at   TIMESTAMP NOT NULL
);
```

When the grouper builds edges, it suppresses any edge `(A, B)` where some split-marker `X` satisfies `A.created_at < X.created_at <= B.created_at`. Operator splits are therefore sticky across regroups — fixing a false merge once fixes it forever, until an explicit unsplit.

### Step 4: connected components

Build an undirected graph: node per primary alert, edge whenever `edge_probability > MIN_EDGE_P_SERVER` and no split marker vetoes it. Connected components of this graph are the incidents.

A single alert with no qualifying neighbours forms a singleton component — a one-alert incident. These still get their own narrative and correlation panels; the narrative just notes "Event recorded at {time}." as today.

### Step 5: per-incident confidence

For each component, `incident.confidence = min(edge_probability over all edges in the component)`. Interpretation: "the chain is only as trustworthy as its weakest link."

Singletons have no edges. Their confidence is defined as `1.0` (no chain to be weak).

Persisted in the existing `incidents.confidence` column. (The current column stores mean alert confidence — the semantics change. The previous meaning isn't used anywhere after this migration lands.)

### Step 6: cross-incident alerts

`hourly_summary`, `daily_summary`, `daily_pattern`, and `annotation_context_*` event-types are cross-incident by design. They are **not** included in the causal graph. They continue to be attached to every incident within their time window as `is_primary = 0` rows in `incident_alerts`, exactly as today. The graph views render them on the cross-incident band below the main cluster grid — no change to the existing visual treatment.

## Data flow

```
new_inference event
        │
        ▼
regroup_all(db_file)          [daemon thread — existing]
        │
        ├── load all non-dismissed inferences + split markers
        ├── classify: primary vs cross-incident
        ├── build_edges(primaries)          → list[(a_id, b_id, p)]
        │     └── apply split markers, MIN_EDGE_P_SERVER floor
        ├── connected_components(edges)     → list[set[alert_id]]
        ├── for each component:
        │     compute_confidence            → min(p over edges in component)
        │     attach cross-incident alerts  (existing logic)
        │     build signature               (existing — 32-float cosine vector)
        │     build narrative               (existing — {observed, inferred, impact, correlation})
        │     INSERT OR REPLACE into incidents
        └── DELETE incidents that no longer exist
```

## API changes

### `GET /api/incidents/<id>` — augment

Response gains an `edges` array listing every intra-incident edge with its probability:

```json
{
  "id": "INC-20260423-0928",
  "confidence": 0.31,
  "alerts": [...],
  "edges": [
    {"from": 2381, "to": 2385, "p": 1.00, "shared_sensors": ["eco2_ppm"]},
    {"from": 2385, "to": 2392, "p": 0.72, "shared_sensors": ["eco2_ppm", "tvoc_ppb"]},
    {"from": 2392, "to": 2410, "p": 0.29, "shared_sensors": ["tvoc_ppb"]}
  ],
  ...
}
```

Rendered edge opacity/width/style uses `p`; hover tooltip uses `shared_sensors` + the computed gap. Edges are computed on-the-fly from stored `alert_signal_deps` — no new persistence needed for them.

### `POST /api/incidents/<id>/split` — new

Body: `{"alert_id": <int>}`. Creates an `incident_splits` row marking the given alert as "starts a new incident — break the chain before this one". Triggers a regroup. Returns the two (or more) new incidents that resulted.

### `POST /api/incidents/<id>/unsplit` — new

Body: `{"alert_id": <int>}`. Deletes the split marker for the given alert. Triggers a regroup. Returns the merged incident.

Both write endpoints are gated behind `@require_role("controller")` — same permission level as dismissing an inference or annotating a point.

## Frontend changes

### Toolbar: view-side slider

A slim slider in the graph-controls row:

```
Hide weak links: ━━━●━━━━  P ≥ 0.20
```

Persisted in `localStorage` under key `inc.edge_p_floor`. Default `0.20`. Range `0` – `1`, step `0.05`. Changes both edge rendering *and* the client-side subdivision preview (see next section).

### Edge styling (Cytoscape)

Opacity + width + style all ramp with P. Edges below the slider floor get `display: 'none'` (removed from the canvas but still in the data so dragging the slider back reveals them).

| P range      | opacity | width | line-style |
|--------------|---------|-------|------------|
| ≥ 0.7        | 1.0     | 2.0   | solid      |
| 0.4 – 0.7    | 0.7     | 1.5   | solid      |
| 0.2 – 0.4    | 0.5     | 1.0   | dashed     |
| floor – 0.2  | 0.3     | 0.8   | dotted     |
| < floor      | hidden  | –     | –          |

### Edge hover tooltip

On edge hover: `"14 min apart · eco2_ppm (+0.82, +0.74) · P = 0.82"`.

### Client-side subdivision preview

The slider is not *only* a display filter. It also previews the grouping that *would* result at the current threshold, so an operator can drag the slider up and feel the effect of their choice before committing.

**How it works:**

1. On every slider change, the client runs a small `connected_components` pass on each incident's edge set, using `P ≥ slider_floor` as the predicate. This is the same graph algorithm the server runs (for parity of semantics), but scoped to a single incident's alert list — typically 5–40 alerts — so the cost is a few microseconds.
2. If the result is **1 component**: no visual change — the incident hull stays intact (unchanged from the current spec).
3. If the result is **2+ components**: each subcomponent is outlined with a thin dashed rectangle drawn inside the existing incident hull. Severity colour of the outline matches the subcomponent's max severity.
4. A badge on the hull label reads **"Would split into 3 at P ≥ 0.80"** (number adjusts as the slider moves).
5. The confidence bar on the incident card gains a faint secondary marker showing the min-P of the weakest subcomponent at the current threshold — so the operator can see "at this threshold my weakest sub-cluster is confidence 0.65".
6. **No API calls, no server state changes, no list mutations.** The slider preview is purely a client-side visualisation.

**Committing the subdivision:**

When 2+ subcomponents are previewed, a `[Commit these splits]` button appears in the detail panel (sibling to the existing `[Split at weakest link]` button). Clicking it:

1. Identifies the split points: for each subcomponent after the chronologically-earliest, the first alert of that subcomponent is a split marker.
2. Fires one `POST /api/incidents/<id>/split` per split point (sequentially, stopping on first error). Reuses the existing split endpoint — no new endpoint needed.
3. Triggers a regroup + graph refresh.

The existing `[Split at weakest link]` action remains — it's the one-click version for operators who just want to break the single worst link without dragging the slider.

**Why this scope:**

This gives operators a concrete tool for "I think the grouping is too aggressive" without requiring them to pick split points by eye. The visual preview IS the explanation for the commit action, so the learning curve is a single drag.

**What we do NOT do:**

- No client-side narrative re-computation. Narratives live on the server, and a subdivision preview shows only the graph subdivision — no sub-narratives. The narrative panel continues to reflect the committed (server-side) incident. An operator who drags the slider and sees "would split into 3" does not see three separate narratives — they commit the split first, then the server generates narratives for the three new incidents on the next regroup.
- No new "ghost incidents" in the incident list. The left panel still shows server-committed incidents only. Previews are in-place visual overlays on the graph canvas.

### Incident card (left panel)

Below the severity dot + alert count row, a 4px-tall bar filled left-to-right proportional to incident confidence. Hatched red overlay if confidence < 0.3.

```
INC-20260423-0928
CO₂ dangerously high — 1132 ppm
09:28–10:17 · 49m
● critical · 12 alerts
━━━━━━━━━━━━━━━━━━━  confidence 0.83
```

Low-confidence example (confidence = 0.28):
```
━━━━━░░░░░░░░░░░░░░  0.28 — includes a long-gap link
```

### Hull border (graph canvas)

Border dash pattern reflects confidence:
- `solid` if confidence ≥ 0.5
- `long-dashed` if 0.3 ≤ confidence < 0.5
- `short-dashed` with a `⚠` badge on the hull label if confidence < 0.3

Severity colour on the border is unchanged — this is an *additional* visual channel, orthogonal to severity.

### Detail panel (right)

Next to the incident title:
```
INC-20260423-0928 · confidence 0.31
```

If confidence < 0.5, an advisory line appears at the bottom of the narrative block:
```
⚠ Weakest causal link in this chain is 3h 12m wide — consider whether this is really one event.
```

A `[Split at weakest link]` button appears when confidence < 0.5. Clicking it:
1. Identifies the edge with the lowest P in the component.
2. Picks the *later* of its two endpoints as the split point (the "new incident starts here" alert).
3. POSTs to `/api/incidents/<id>/split` with that alert id.
4. Refreshes the graph.

A small `[Undo split]` action appears in the narrative panel of any incident that exists because of an operator split (detectable: the earliest alert in the incident is present in `incident_splits`). Clicking it POSTs to `/api/incidents/<id>/unsplit`.

### Minimap

No change to the minimap's render path. It draws hull rectangles, not individual edges, and the hulls haven't moved.

## Database changes

**New table:**
```sql
CREATE TABLE IF NOT EXISTS incident_splits (
    alert_id     INTEGER PRIMARY KEY REFERENCES inferences(id) ON DELETE CASCADE,
    created_by   TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Column semantics (not schema):**
- `incidents.confidence` now stores `min(edge P over the component)` or `1.0` for singletons. Previously stored mean alert confidence. No column-level migration needed; the regrouper rewrites every row.

**Tables to drop from the grouper's output logic** (not from schema):
- `merge_similar_adjacent` stops being called in `regroup_all` and is deleted from `incident_grouper.py`.
- Callers of `sessionise` elsewhere in the repo: none outside the grouper.

## Code changes — where

- **`mlss_monitor/incident_grouper.py`** — main surgery.
  - Delete `sessionise`, `merge_similar_adjacent`, `_group_event_types`, `_jaccard`, `_group_gap_minutes`, `GAP_MINUTES`, `MAX_MERGE_GAP_MINUTES`, `JACCARD_THRESHOLD`. They have no other callers.
  - Add `edge_probability(a, b) -> float` — pure, unit-testable.
  - Add `build_edges(alerts, split_marker_ids) -> list[tuple[int, int, float]]` — pure.
  - Add `connected_components(alerts, edges) -> list[list[dict]]` — pure.
  - Add `incident_confidence(edges_in_component) -> float` — pure.
  - Rewrite `regroup_all` to: load alerts + splits → build edges → connected components → upsert incidents.
- **`mlss_monitor/routes/api_incidents.py`** — augment detail response with `edges`; add `split` and `unsplit` endpoints; register new blueprint route `require_role("controller")`.
- **`database/init_db.py`** — add the `incident_splits` table.
- **`templates/incidents.html`** — slider + confidence bar + split button markup.
- **`static/js/incident_graph.js`** — edge styling by P, hover tooltip, slider wiring, confidence bar rendering, hull dash-by-confidence, split/unsplit fetch calls, client-side subdivision preview (`previewSubdivisions(incidentEdges, threshold)` pure helper reused from a shared `connectedComponents` routine — aim to share the same algorithm shape as the server for parity).
- **`static/css/incident_graph.css`** — slider styles, confidence bar styles, hull dash variants, advisory banner.
- **`readme.md`** — rewrite the Incident correlation graph section to describe the new grouping algorithm.
- **Tests:**
  - `tests/test_incident_grouper.py` — delete sessionise/merge tests; add edge_probability / build_edges / connected_components / incident_confidence / regroup_all tests.
  - `tests/test_api_incidents.py` — add tests for split / unsplit endpoints.

## Testing plan

Pure-function tests (no DB, no Flask):

- **`edge_probability`** — full P at zero gap, full P at 30 min, decay at 90/120/180 min, zero at 240 min, zero when no shared sensor, zero when sensors share but signs disagree, zero when |r| below threshold.
- **`build_edges`** — round-trips signal_deps correctly; respects split markers; drops sub-floor edges.
- **`connected_components`** — singletons handled; transitive closure (A-B, B-C without A-C → one component); two disconnected subgraphs → two components; respects split markers (same alerts without a split = one component, with a split = two).
- **`incident_confidence`** — min over component edges; singletons return 1.0; deterministic on equal-P ties.

Integration tests (`regroup_all` against a seeded DB):
- Two events with shared sensors 45 min apart → one incident.
- Two events with disjoint sensors 10 min apart → two incidents.
- Three events where A-B and B-C share sensors but A-C doesn't → one incident.
- A split marker between two otherwise-connected alerts → two incidents.
- Unsplitting → back to one incident.
- 5h-apart events with shared sensors → two incidents (zero P).

API tests:
- `POST /api/incidents/<id>/split` with valid alert → 200 and new incident count.
- `POST /api/incidents/<id>/split` without controller role → 403.
- `POST /api/incidents/<id>/unsplit` → reverses.
- Split endpoint creates an `incident_splits` row; unsplit removes it.

Frontend behaviour to verify manually after deploy:
- Slider at 0.0 shows all edges; at 1.0 shows only P=1 edges.
- Confidence bar updates live with slider.
- `[Split at weakest link]` button appears and works.
- Undo action appears on operator-split incidents.
- Raising the slider above a cluster's internal edge probabilities draws dashed sub-outlines and shows "Would split into N" badge.
- `[Commit these splits]` button converts the preview into real splits via the existing `/split` endpoint and the graph refreshes to reflect them.

Client-side unit tests (small JSDOM or plain-node harness for the pure graph helper):
- `connectedComponents(alerts, edges, threshold)` produces identical output to the server-side Python implementation on the same input — property-based if practical, otherwise a small fixture suite covering singletons, chains, disconnected subgraphs, and threshold-induced splits.

## Migration

On first deploy:
1. `create_db()` adds the `incident_splits` table (idempotent `CREATE TABLE IF NOT EXISTS`).
2. The background grouper runs on startup, reads all existing inferences, builds the causal graph, and rewrites the `incidents` / `incident_alerts` / `alert_signal_deps` tables via the existing `INSERT OR REPLACE` path. The old sessionise-based incidents are wiped and replaced.

No data loss: every `inference` row is preserved; only the grouping changes.

Rollback: the pre-change grouper code is available via git revert. If we revert, the incidents table gets re-populated on next restart using the old algorithm — again, no data loss.

## Default configuration

| Constant                               | Value | Rationale |
|----------------------------------------|-------|-----------|
| `EDGE_FULL_P_WINDOW_MINUTES`           | 30    | Matches the existing operator intuition for "a burst of activity". Below this, we treat the link as certain. |
| `EDGE_ZERO_P_WINDOW_MINUTES`           | 240   | Four hours. Longer than any plausible single cause; shorter than "different day". |
| `EDGE_STRONG_R_THRESHOLD`              | 0.5   | The threshold we already use for "this sensor is involved in this alert". Keeps the sensor-sharing rule consistent with the existing `_build_correlation` in `incidents_narrative.py`. |
| `MIN_EDGE_P_SERVER`                    | 0.05  | Prevents P≈0 chains from being persisted. |
| Client slider default (localStorage)   | 0.20  | Good initial "only show me meaningful links" value. Operator can drag freely. |
| Client slider range                    | 0 – 1 | Full exploration. |
| Confidence thresholds (hull dash)      | 0.5, 0.3 | Three tiers: solid / long-dashed / short-dashed+warning. |

## Why not attribution-first

We considered attribution-first grouping (every incident has one `attribution_source`, multiple sources → multiple incidents) and rejected it because:

1. **Cascades are real events.** "Human activity → HVAC response" is one event in the operator's head. Forcing a split on attribution divergence would hide the cascade in two separate incidents.
2. **Attribution is probabilistic.** Fingerprint matches are noisy. A grouping algorithm that trusts them as ground truth would inherit their noise.
3. **Attribution is still shown.** Every alert carries its attribution through to the UI. The narrative and node overlay both display it. An operator who disagrees with the causal-grouping's merge can use the "split at weakest link" action. The information is not lost.

## Open items / future work

- **Sensor-weighted confidence.** Currently `incident.confidence = min(edge P)`. Alternative: weight each edge by how many sensors it shares, so edges backed by multiple sensor correlations count for more. Not in scope for this cut — revisit if "min edge" produces unintuitive results.
- **Attribution as a secondary tie-breaker.** If two plausibly-distinct incidents could be merged by a weak edge, and their dominant attributions disagree, we could automatically suppress the merge. Interesting but opinionated — park for real-data observation.
- **Extending the slider to the similar-past match.** Right now similar-past uses the full incident signature. A future refinement could re-compute similar-past against the sub-incident an operator has previewed (but not yet committed) to answer "if I split this incident, what does each half match?". Potentially useful for operators debating a split — defer for now.

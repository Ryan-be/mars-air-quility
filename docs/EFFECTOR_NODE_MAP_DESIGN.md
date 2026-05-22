# Effector node-map design

[Back to main README](../readme.md) ·
[Backlog entry](Bugs_Improvements_and_Roadmap.md#-feature-configurable-smart-plug-effectors-hub-scoped-or-grow-unit-scoped)

This is the visual + interaction design for the **Configurable smart-plug
effectors** feature tracked in the roadmap. It supersedes any earlier
sketch — when the feature is implemented, the look + interactions must
match this design.

---

## Quick view

A pan/zoom-able dark-themed canvas showing the live topology of the
whole environmental-control system:

- **MLSS Hub** at the centre with live whole-room readings
  (temp / RH / CO₂ sparklines) inside the card.
- **N grow units** as their own nodes with per-plant readings (soil
  moisture / soil temp / air-T+RH sparklines) + phase tag (seedling,
  vegetative, flowering, fruiting).
- **N effectors** as smaller cards attached to either the hub or a
  specific grow unit. Each effector card carries its own
  `Auto / ON / OFF` segmented control directly on the card — no
  need to open a modal for the most common operation.
- **Connecting edges** colour-coded by parent type (blue from hub,
  green from grow units) and weighted by live state (solid when
  effector is on, dashed when off).
- **Sticky telemetry topbar** with mission-time clock, hub status,
  totals (grow units count, effectors count, active count, auto vs
  forced split).
- **Slide-out side panel** opens on node click for full configuration
  — type, scope (re-parent to a different hub or grow unit), per-type
  rules, manual overrides.

## How to view the prototype

The hi-fi reference implementation lives at
[`docs/assets/effector-map-handoff/`](assets/effector-map-handoff/) —
React-on-Babel via CDN, no build needed:

```bash
# from the repo root, open in your default browser:
start docs/assets/effector-map-handoff/index.html       # Windows
open  docs/assets/effector-map-handoff/index.html       # macOS
xdg-open docs/assets/effector-map-handoff/index.html    # Linux
```

The prototype includes a "Tweaks" floating panel (visible in the
bottom-right) for design exploration — edge style, line weight, card
density, colour-coding mode, etc. **Omit the tweaks panel from
production**; it exists only so the designer could explore variants
without re-editing CSS.

## What's in the bundle

| File | What it does |
| --- | --- |
| [`README.md`](assets/effector-map-handoff/README.md) | Full design spec — colour tokens, typography, all interaction states, edge cases. **The canonical spec — implementation must match this.** |
| [`index.html`](assets/effector-map-handoff/index.html) | Browser entry point. React + Babel CDN; no `npm install`. |
| `app.jsx` | Top-level component, telemetry topbar, layout orchestration. |
| `graph.jsx` | Pan / zoom / SVG edge renderer. |
| `nodes.jsx` | Hub / Grow / Effector card components. |
| `panel.jsx` | Slide-out config side panel. |
| `tweaks-panel.jsx` | Design-time variant controls (**not for production**). |
| `data.js` | Sample fleet (4 grow units, 14 effectors) for the prototype. |
| `layout.js` | Initial graph layout heuristic + `localStorage` persistence. |
| `icons.jsx` | Icon set. |
| `styles.css` | Design tokens + hand-rolled approximation of AstroUX styling. **Reference only — ship with the real AstroUX library.** |

## Implementation notes (when this feature is built)

The handoff bundle's README lists the spec in full detail. The most
load-bearing translation points for our codebase:

1. **Use the real AstroUX library** (`@astrouxds/astro-web-components`)
   for buttons, segmented controls, status indicators, side panels.
   The bundle's CSS is a hand-rolled approximation — use the AstroUX
   tokens directly instead of duplicating them.
2. **Wire live state to the existing event bus.** The prototype runs a
   mock SSE tick loop; the real implementation should subscribe to
   the existing `/api/stream` SSE endpoint and react to a new
   `effector_state_changed` event class (and the existing
   `sensor_update`, `health_update`, `fan_status` events).
3. **Persist node positions** in the new `smart_plugs.layout_json`
   column (or a sibling `node_layout` table) so re-arrangements
   survive across sessions and across operators. The prototype's
   `localStorage` is per-browser; production needs a server-side
   store.
4. **Route node clicks** to existing routes where possible:
   - Click MLSS Hub node → `/controls`.
   - Click Grow Unit node → `/grow/<id>`.
   - Click Effector node → opens the side panel from this design.
5. **The "Re-arrange" button** clears persisted positions and re-runs
   the layout heuristic. The "Recenter" button resets pan + zoom
   without touching positions.

## Where this fits in the existing app

This view replaces the current `/controls` page contents (which today
is just the single hardcoded fan). The current `/controls` route stays
the entry point — same URL, much richer experience. The earlier
"Effectors" tab planned under `/admin` is no longer needed; this is
where effector management lives.

## Status

**Backlog item — not yet implemented.** Tracked in
[Bugs_Improvements_and_Roadmap.md](Bugs_Improvements_and_Roadmap.md#-feature-configurable-smart-plug-effectors-hub-scoped-or-grow-unit-scoped).
The design above is the agreed visual target whenever the feature is
picked up.

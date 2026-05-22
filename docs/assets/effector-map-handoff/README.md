# Handoff: MLSS Node Map

## Overview

The **MLSS Node Map** is an interactive topology view for a Modular Life-Support / grow-room control system. It visualises three node types — a central **Hub**, multiple **Grow Units**, and **Effectors** (fans, AC, heat pads, grow lights, pumps, etc.) — and the parent-child associations between them. Every effector exposes an `Auto / ON / OFF` segmented control directly on its card; clicking a node opens a side panel for full configuration including re-parenting an effector to a different hub or grow unit.

The visual language is **AstroUX** — NASA's open-source dark-themed design system for mission-control interfaces. Status colors are the standard Astro classifiers (off / standby / nominal / caution / serious / critical) and typography is **IBM Plex Sans + IBM Plex Mono**.

## About the Design Files

The files in this bundle are **design references created in HTML** — a working React-on-Babel prototype showing the intended look, layout, and interactions. They are **not production code to ship**. Your task is to **recreate these designs in the target codebase's existing environment** (React, Vue, SwiftUI, native, etc.) using its established patterns and libraries. If no environment exists yet, pick the most appropriate framework for the project (a React SPA is a natural fit) and implement the designs there.

Specifically:
- The prototype uses ad-hoc `localStorage` persistence and a mock SSE tick loop — your implementation should plug into the real `/events` SSE endpoint and whatever store the rest of MLSS uses.
- The prototype keeps everything in window globals (`MLSS_NODES`, `MLSS_LAYOUT`) and ESM-less `<script>` tags so it can run unbundled. Use proper modules, types, and your codebase's component library when implementing.
- AstroUX has official component implementations (`@astrouxds/astro-web-components`, `@astrouxds/react`). **Prefer the official AstroUX library** for buttons, status indicators, segmented controls, panels, etc. The CSS in this handoff is a hand-rolled approximation — you can use it as a token reference but should not ship it.

## Fidelity

**High-fidelity (hi-fi)** prototype. Colors, typography, spacing, line weights, status semantics, and interactions are final. Recreate pixel-perfectly using the AstroUX library + the design tokens listed below.

## Screens / Views

There is one primary screen — a full-viewport node map — plus a slide-out side panel and a floating tweaks panel (the tweaks panel is a design-time tool, **omit from production**).

### 1. Top Bar (height 44px)

Sticky telemetry-style header.

- **Layout**: flex row, full-width, height 44px, background `--bg-100` (`#1b2226`), bottom border 1px `--border-deep` (`#1f262b`).
- **Brand cell** (left): 18px horizontal padding, vertical right border 1px `--border-subtle`. Contains a 14×14 px gradient mark (`linear-gradient(135deg, #4dacff 0%, #56f000 100%)`, 2px radius) + label `"MLSS · NODE MAP"` in IBM Plex Sans 11px 600 weight, uppercase, letter-spacing 0.08em.
- **Telemetry cells** (flex-1): Each cell has two stacked lines.
  - Key: IBM Plex Mono 9px, uppercase, letter-spacing 0.12em, color `--text-tertiary` (`#8a96a3`).
  - Value: IBM Plex Mono 12px, color `--text-primary` (`#ffffff`), tabular-nums.
  - Cells used (in order): `Mission Time` (T+ ddd hh:mm:ss), `Hub` (● NOMINAL — green), `Grow Units` (count), `Effectors` (count), `Active` (count of on effectors, green), `Auto / Forced` (split).
  - Padding 4px 16px, right border 1px `--border-subtle`.
- **Action buttons** (right, push to end with margin-left:auto):
  - "↻ Re-arrange" — restores auto-layout, clears persisted positions.
  - "⊕ Recenter" — resets pan/zoom to center.
  - Each: transparent bg, 11px uppercase 0.08em letter-spacing, padding 0 14px, hover bg `--bg-200`. Left border 1px on each.

### 2. Graph Viewport (fills remainder)

Full-bleed pan/zoom-able canvas containing nodes (HTML cards) and edges (SVG lines), with patterned background.

- **Pan**: left-click-drag empty canvas. Cursor: grab → grabbing.
- **Zoom**: wheel anywhere over the canvas, zooms around mouse position. Clamp `0.3×` to `2.5×`.
- **Pinned overlays**:
  - **Legend** (bottom-left) — collapsible per Tweaks toggle.
  - **Zoom widget** (top-right, below topbar) — `+` / `−` and current zoom %.
  - **Minimap** (bottom-right, optional via tweaks).

#### Background patterns

Five options, controlled by the design's `background` token:

| Token | Pattern |
|---|---|
| `dots` (default) | 1px dots at 32px spacing, color `rgba(169,179,186,0.18)` |
| `grid` | 48px grid lines, `rgba(169,179,186,0.07)` |
| `grid-fine` | Compound 12px fine + 96px coarse grid |
| `stars` | Random radial-gradient star scatter |
| `base` | Solid `--bg-base` |

### 3. Node Cards

Three variants. All cards share a base shell:

- `min-width: 168px`, padding `10px 12px`, background `--bg-100`, 1px border `--border-grey` (`#4d5860`), 3px border-radius.
- **Left-edge stripe** (3px wide, full height) in the node's type color — `--node-color`.
- Hover: border color → `--status-standby` (`#4dacff`).
- Selected: border + 1px outer ring `--status-standby`, plus a `0 0 24px rgba(77,172,255,0.25)` glow and corner "reticle" brackets (4 right-angle tick marks at the card corners, 8px each).
- Cards are positioned via absolute `left/top` in world coords, then a parent wrapper applies `transform: translate(viewport.x, viewport.y) scale(viewport.k)`.

#### 3a. Hub Card

- **Type color**: `--node-hub` = `#4dacff` (Astro Standby blue). Adds a 2px top edge stripe in addition to the left ledge.
- **Min-width**: 220px.
- **Header row**: hub-icon (16px target/satellite glyph), title `MLSS Hub` (13px 600), subtitle `central coordinator · grow tent A` (11px `--text-tertiary`), node id "hub" in mono 9px uppercase at right.
- **Telemetry grid** (3 columns, separated above by 1px top border `--border-subtle`, 8px padding-top):
  - Temp (°C, 1 decimal), RH (%, 0 decimals), CO₂ (ppm, 0 decimals).
  - Key: 9px uppercase 0.1em, `--text-tertiary`.
  - Value: IBM Plex Mono 14px white, with unit suffix at 9px `--text-tertiary`.
- **Sparkline**: 24px tall area chart (last 30 readings of temp), stroke `--node-hub`, fill `--node-hub` @ 16% alpha.

#### 3b. Grow Unit Card

- **Type color**: `--node-grow` = `#56f000` (Astro Nominal green).
- **Header**: leaf glyph, title `Grow #N` (13/600), subtitle `<plant species>` (11px tertiary), `stage` chip at right in mono 9px (`seedling | vegetative | flowering | fruiting`).
- **Telemetry grid**: Temp / RH / Soil — same styling as hub but soil is %.
- **Sparkline**: 24px, green.

#### 3c. Effector Card

- **Type color**: `--node-eff` = `#ffb302` (Astro Serious amber — matches the mockup).
- **Header**: role-specific glyph (fan, ac, heat, humidifier, light, pump, co2), title (e.g. `Heat Pad`), subtitle = model name (e.g. `VIVOSUN 10×20"`), id at right.
- **Status row** (margin-top 6px, flex space-between):
  - **Status pill** on the left:
    - ON: solid fill in `--status-nominal`, black text, `● ON · 95%`.
    - OFF: outlined in `--status-off` (`#9ea7ad`), text `○ OFF`.
    - Pill is mono 9.5px 600, uppercase, 0.12em letter-spacing, padding `1px 6px`, 2px radius. Leading `ball` dot is 6px circle with `0 0 6px currentColor` glow.
  - **Role label** on right: mono 10px uppercase, `--text-tertiary`.
- **Mode bar** (border-top 1px `--border-subtle`, margin-top 10px, padding-top 8px):
  - Three-segment control: `AUTO · ON · OFF`.
  - Inactive segment: bg `--bg-300`, border `--border-grey`, color `--text-secondary`, mono 10px 600 uppercase 0.12em, 2px radius, 5px vertical padding.
  - Active states (the segment matching the current mode lights up in that mode's color):
    - `auto` active → bg `--status-standby`, text `#001528`.
    - `on` active → bg `--status-nominal`, text `#001a00`.
    - `off` active → bg `--status-off`, text `#1a1a1a`.
  - Buttons must `stopPropagation` so clicking AUTO/ON/OFF doesn't bubble to the card drag handler.

### 4. Edges

Drawn in SVG, parent-to-child for every node that has a parent.

- **Path style** — three options (Tweaks):
  - `straight`: `M ax ay L bx by`.
  - `ortho`: L-shaped, two right angles, midpoint pivot.
  - `bezier` (default): cubic Bézier with horizontal handles, handle length `clamp(40, len * 0.4, 160)`.
- **Anchor points**: lines should meet the card's bounding box edge (not the card center). Compute the ray intersection of `(parent.center → child.center)` with the parent's box, and vice versa, before drawing.
- **Stroke colors**:
  - Hub → grow edge: `--status-standby` (`#4dacff`).
  - Effector edge: color tracks the effector's state — `--status-nominal` if `state === 'on'`, `--status-standby` if `mode === 'auto' && state === 'off'`, otherwise `--status-off`.
- **Base layer**: 1.5px wide, opacity 0.55.
- **Flow overlay** (only when effector is `on` AND tweaks `liveFeel !== 'subtle'`):
  - Same path, stroke-width 1.6, `stroke-dasharray: 2 8`, animated `stroke-dashoffset: 0 → -40` over 1.4s linear infinite. Opacity 0.95.

### 5. Side Panel

Right-side slide-out, width 360px, full viewport height.

- Background `--bg-100`, left border 1px `--border-subtle`, shadow `-8px 0 32px rgba(0,0,0,0.4)`.
- Transform `translateX(100%) → 0` with 200ms `cubic-bezier(.3,.7,.4,1)`.

#### Header (44px-ish)

- Background `--bg-200`, padding `14px 16px`, bottom border 1px `--border-subtle`.
- Icon (18px) → title (14px 600) + id (mono 10px uppercase tertiary) stacked → close button (24×24 square, 1px border `--border-grey`, text "×").

#### Body content (varies by node type)

All sections share a heading style: IBM Plex Mono 10px 600 uppercase 0.14em `--text-tertiary`, followed by a flex-1 horizontal rule. Section gap is 18px.

##### Effector panel

1. **Mode** — big segmented control, 3 columns, ~38px tall. Mono 11px 600 uppercase 0.14em. Active segment colors as above. Below the seg, a 11px tertiary explanation line ("Hub controls this effector based on Grow #1's setpoints for temperature." / "Forced ON — overrides automation." / "Forced OFF — overrides automation.").
2. **Power output** — labeled slider 0–100%, disabled when mode is `off` or state is `off`. Thumb is 14px round, `--status-standby` with white border.
3. **Belongs to** — a vertical list of candidate parents (every hub + grow). Each row: 10px swatch in the parent's type color · label (12px 600) · kind tag (mono 10px uppercase). Selected row: bg `--bg-300`, border 1px `--status-standby`. Clicking a row re-parents the effector.
4. **Schedule** — 24-column grid of hour cells (22px tall, 1px gaps). Toggleable. ON cells filled `--status-nominal`. Axis labels 00 · 06 · 12 · 18 · 24 below.
5. **Hardware** — key/value grid (110px label column, mono value column): Model · Role · State (colored) · Power draw (W) · Last switch (time ago).

##### Grow panel

1. **Plant** — Species · Stage · Soil pH.
2. **Live sensors** — Temperature · Humidity · Soil moisture.
3. **Linked effectors** — list of every effector parented to this grow, with current state inline. Empty state: centered tertiary 11px "No effectors assigned".

##### Hub panel

1. **Room sensors** — Temperature · Humidity · CO₂ · Ambient light (lux).
2. **Coordination** — paragraph from `node.notes`, 12px secondary, line-height 1.5.
3. **Subsystems** — counts of Grow units · Effectors · Active now (green).

### 6. Bottom Status Bar (24px)

- Mono 10px, color `--text-tertiary`, bg `--bg-100`, top border 1px `--border-deep`.
- Cells (left, gap 18px): `● SSE · /events` (green dot, 2.4s blink animation), `14 sub · 0 backlog`, `Δt <seconds>s`. Right-aligned: ISO timestamp `YYYY-MM-DD HH:MM:SSZ`.

## Interactions & Behavior

### Pan & Zoom
- Pan: mousedown on empty canvas → drag updates `viewport.x/y`. Ignore mousedowns on `.node`, `.side`, `.topbar`, etc.
- Zoom: wheel anywhere → scale around mouse position, clamp `[0.3, 2.5]`. Use `e.preventDefault()` and a `{ passive: false }` listener.
- Recenter button resets to `{k: 0.9, x: viewportWidth/2, y: viewportHeight/2 - 40}`.

### Node drag
- mousedown on a node card (anywhere except a `.modebtn`) → start drag.
- Track delta in **world** coordinates (`dx / viewport.k`).
- If total movement < 2px on mouseup, treat as a click → open side panel for that node.
- On mouseup, persist all positions to storage.

### Effector mode change
- Clicking AUTO/ON/OFF (card or panel) calls `setMode(id, mode)`.
- Side-effect: derive `state` from `mode`:
  - `on` → state = on, power = current value or 50.
  - `off` → state = off, power = 0.
  - `auto` → state computed by automation rules (in the prototype these are mock rules per role; in production the hub decides).
- Edge color recomputes from new state.

### Effector re-parent
- Clicking a row in "Belongs to" calls `setParent(id, newParentId)`.
- Edge automatically redraws from the new parent.
- Position is unchanged (user can drag to a sensible new spot, or call Re-arrange).

### Live updates (SSE)
- Sensors drift every 1.5s in the mock. In production, subscribe to `/events` SSE, parse each event into a partial node update, and call your store's update method.
- Sparklines keep a rolling window of last 30 readings.

### Animation timings
- Side panel slide: 200ms `cubic-bezier(.3, .7, .4, 1)`.
- Mode bar / hover / focus transitions: 80–120ms.
- Edge flow: 1.4s linear infinite.
- Live blink dot in status bar: 2.4s ease-in-out infinite, alpha 1 → 0.35 → 1.

## State Management

```ts
type NodeId = string;
type Mode = 'auto' | 'on' | 'off';
type State = 'on' | 'off' | 'fault';
type Stage = 'seedling' | 'vegetative' | 'flowering' | 'fruiting';
type Role = 'circulation' | 'cooling' | 'heating' | 'humidity' | 'lighting' | 'irrigation' | 'co2';

interface BaseNode { id: NodeId; label: string; parent?: NodeId; }

interface HubNode  extends BaseNode { kind: 'hub'; sub: string; sensors: { temp: number; rh: number; co2: number; lux: number }; notes: string; }
interface GrowNode extends BaseNode { kind: 'grow'; plant: string; stage: Stage; sensors: { temp: number; rh: number; soil: number; ph: number }; }
interface EffectorNode extends BaseNode {
  kind: 'effector';
  model: string;
  parent: NodeId;
  role: Role;
  mode: Mode;
  state: State;
  power: number;          // 0–100
  setpoint?: number;
}

interface Position { x: number; y: number; }
interface Viewport { x: number; y: number; k: number; }
```

Store needs:
- `nodes: Record<NodeId, Node>` — keyed for easy partial updates from SSE.
- `positions: Record<NodeId, Position>` — persisted to whatever local store you use (in the prototype: `localStorage` key `mlss.node-positions.v2`).
- `viewport: Viewport` — transient, not persisted.
- `selectedId: NodeId | null`.
- `history: Record<NodeId, number[]>` — rolling sparkline windows (only for sensored nodes).

Actions:
- `setMode(id, mode)` · `setPower(id, %)` · `setParent(id, parentId)` · `setPosition(id, {x,y})` · `selectNode(id | null)` · `resetLayout()`.

## Design Tokens

Copy-paste-ready CSS variables (already in `styles.css`):

```css
:root {
  /* Backgrounds */
  --bg-base:        #10161a;
  --bg-100:         #1b2226;
  --bg-200:         #252e33;
  --bg-300:         #303a40;
  --bg-400:         #3d464d;

  /* Borders */
  --border-deep:    #1f262b;
  --border-subtle:  #2f363b;
  --border-grey:    #4d5860;
  --border-strong:  #6c757d;

  /* Text */
  --text-primary:   #ffffff;
  --text-secondary: #a9b3ba;
  --text-tertiary:  #8a96a3;
  --text-disabled:  #6c757d;

  /* Astro status classifiers — DO NOT CHANGE */
  --status-off:      #9ea7ad;
  --status-standby:  #4dacff;
  --status-nominal:  #56f000;
  --status-caution:  #fce83a;
  --status-serious:  #ffb302;
  --status-critical: #ff3838;

  /* Node-type semantic aliases */
  --node-hub:  var(--status-standby);   /* #4dacff */
  --node-grow: var(--status-nominal);   /* #56f000 */
  --node-eff:  var(--status-serious);   /* #ffb302 */
}
```

**Typography**:
- UI: `"IBM Plex Sans", system-ui, -apple-system, sans-serif` (weights 400/500/600/700).
- Mono / numerics / labels: `"IBM Plex Mono", ui-monospace, "SF Mono", monospace` (weights 400/500/600).
- Base size 13px / line-height 1.35. UI is `-webkit-font-smoothing: antialiased`.

**Type scale**:
| Use | Size | Weight | Notes |
|---|---|---|---|
| Section header (mono) | 10px | 600 | uppercase, letter-spacing 0.14em |
| Telemetry key | 9px | 400 | uppercase, 0.10–0.12em |
| Telemetry value | 14px (cards) / 12px (kv-grid) | 400 mono | tabular-nums |
| Card title | 13px | 600 |  |
| Card sub | 11px | 400 | `--text-tertiary` |
| Status pill | 9.5px | 600 mono | uppercase, 0.12em |
| Mode segment | 10–11px | 600 mono | uppercase, 0.12–0.14em |
| Topbar value | 12px | 400 mono | tabular |

**Spacing**: 4 / 6 / 8 / 10 / 12 / 14 / 16 / 18 / 24px.

**Radii**: `--rad-sm` 2px (chips, segments) · `--rad-md` 3px (cards, panel surfaces) · `--rad-lg` 4px (large).

**Shadows**:
- Selected card glow: `0 0 0 1px var(--status-standby), 0 0 24px rgba(77,172,255,0.25)`.
- Side panel: `-8px 0 32px rgba(0,0,0,0.4)`.
- Status pill ball: `0 0 6px currentColor`.

## Layout Algorithm

Auto-arrange runs once when no persisted positions exist. Pure deterministic radial layout:

1. Hub at world origin `(0, 0)`.
2. **Room effectors** (parent = hub, kind = effector) → arc above hub on a ring of `r = 320`, angles spread evenly from `-160°` to `-20°`.
3. **Grow units** (parent = hub, kind = grow) → arc below hub on a ring of `r = 360`, angles spread evenly from `+20°` to `+160°`. Cache each grow's angle.
4. **Per-grow effectors** → cluster around their grow, ring of `r = 175`, fanned across `±50°` of the grow's outward-facing direction.

`resetLayout()` re-runs this and clears the persisted positions. Once a user has dragged a node, that position wins until reset.

## Assets

- **Icons**: Hand-rolled inline SVG, 24×24 viewbox, 1.6px strokes, `currentColor`. See `icons.jsx` in the bundle. **Replace with your icon library** — AstroUX ships SVG icons via `@astrouxds/icons`, or use any consistent stroke-based set (Tabler, Phosphor light).
- **No bitmap assets** are used.
- **No brand assets** beyond the AstroUX color/type system.

## Files

- `index.html` — entry point + script tags
- `styles.css` — all design tokens + component CSS (your reference for exact values)
- `data.js` — mock topology (nodes + initial state) → replace with your real store / SSE feed
- `layout.js` — pure-function radial auto-layout + localStorage helpers
- `icons.jsx` — inline SVG icon set + `effectorIcon(role)` resolver
- `nodes.jsx` — `HubCard`, `GrowCard`, `EffectorCard`, `Sparkline`, `StatusPill`, `ModeBar`
- `graph.jsx` — `Edges` component + `edgePath` / `anchorOn` / `edgeColorFor` helpers
- `panel.jsx` — `SidePanel` (effector / grow / hub variants) + `Schedule`
- `app.jsx` — top-level: viewport state, pan/zoom, drag, live tick, top/status bars, tweaks, minimap
- `tweaks-panel.jsx` — design-time tweaks tool (**do not port**)

## Notes for Implementation

- **Use the official AstroUX library** wherever possible. Status pills, segmented controls, side panels, and many other elements have first-class AstroUX components — don't hand-roll them.
- **Edge anchoring** is the one piece worth porting carefully: lines that touch card centers look messy when cards overlap; the `anchorOn` ray-rectangle intersection in `graph.jsx` gives clean meets.
- **Don't drag from inside a button**. The card-drag handler must early-return if the mousedown target is inside the mode-bar segments, or the user can't change modes.
- **Production should not have the Tweaks panel.** It's a design-time tool for picking between visual variations during review.
- **SSE wiring**: replace the `setInterval` block in `app.jsx`'s tick effect with an `EventSource('/events')` listener that calls a granular `applyNodeUpdate(id, partial)` on your store.
- **Accessibility gap to close**: the current prototype isn't keyboard-navigable through the node graph. For production, add `Tab` to cycle nodes (sorted by Y then X), Enter to open the panel, arrow keys to move the selected node by 8px.

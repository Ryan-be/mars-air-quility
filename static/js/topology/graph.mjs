/**
 * Topology graph renderer (Phase 5).
 *
 * Vanilla-JS port of `docs/assets/effector-map-handoff/graph.jsx`. The
 * graph is split across two stacked layers inside a transformed wrapper:
 *
 *   <div class="tp-graph-inner" style="transform: translate(x, y) scale(k)">
 *     <svg class="tp-graph-svg">
 *       <g class="tp-edges">
 *         <path class="tp-edge" />          ← Edges layer (SVG)
 *         ...
 *       </g>
 *     </svg>
 *     <div class="tp-nodes-layer">
 *       <div class="tp-node" data-node-id="..." style="left/top">
 *         ...                               ← Card content (filled by Phase 6)
 *       </div>
 *     </div>
 *   </div>
 *
 * Splitting edges (SVG) from nodes (HTML) gives us crisp form controls
 * inside the cards (buttons, focus rings) while still letting us draw
 * smooth vector lines between them.
 *
 * Pure helpers (`edgePath`, `anchorOn`, `edgeColorFor`) are exported
 * directly so they can be unit-tested without a DOM. The renderer
 * (`renderGraph`) takes `ownerDocument` so tests can pass a JSDOM
 * window without leaking globals.
 *
 * Pan / zoom / node-drag handlers (`setupPan`, `setupZoom`,
 * `setupNodeDrag`) are wired by the page boot after the initial render.
 * They use a `getViewport()` + `onChange(newViewport)` callback pair
 * (rather than a stateful object) so the page boot stays the single
 * source of truth for the current viewport.
 */

// Card sizes used for edge-anchor maths. Approximate — the real cards
// resize a bit based on content, but the edge anchor only needs to
// land near the card edge.
const NODE_CARD_W = 200;
const NODE_CARD_H = 100;


// ─── Pure edge maths ──────────────────────────────────────────────────


/**
 * Compute the SVG path `d` string between two points for a given edge
 * style. `straight` and `bezier` are the two production styles; the
 * prototype also has `ortho` (kept for completeness, not exposed in UI).
 *
 * @param {{x: number, y: number}} a Start point.
 * @param {{x: number, y: number}} b End point.
 * @param {"straight"|"bezier"|"ortho"} style Path style.
 * @returns {string} SVG path data.
 */
export function edgePath(a, b, style) {
  if (style === "ortho") {
    const midX = (a.x + b.x) / 2;
    return `M${a.x} ${a.y} L${midX} ${a.y} L${midX} ${b.y} L${b.x} ${b.y}`;
  }
  if (style === "bezier") {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy);
    const handle = Math.min(160, Math.max(40, len * 0.4));
    return `M${a.x} ${a.y} C${a.x + handle} ${a.y} ${b.x - handle} ${b.y} ${b.x} ${b.y}`;
  }
  return `M${a.x} ${a.y} L${b.x} ${b.y}`;
}


/**
 * Project the line from `node` toward `towards` onto the node's
 * bounding box and return the intersection point. Used so edges meet
 * the card edge rather than the (hidden) card centre.
 *
 * @param {{x: number, y: number}} node    Centre of the source node.
 * @param {{x: number, y: number}} towards Centre of the target node.
 * @param {number} [halfW] Half-width of the source card.
 * @param {number} [halfH] Half-height of the source card.
 * @returns {{x: number, y: number}}
 */
export function anchorOn(node, towards, halfW = NODE_CARD_W / 2, halfH = NODE_CARD_H / 2) {
  const dx = towards.x - node.x;
  const dy = towards.y - node.y;
  if (dx === 0 && dy === 0) return { x: node.x, y: node.y };
  const absX = Math.abs(dx);
  const absY = Math.abs(dy);
  const slope = absX === 0 ? Infinity : absY / absX;
  if (slope < halfH / halfW) {
    // Exits the left/right edge.
    const sx = dx > 0 ? 1 : -1;
    return { x: node.x + sx * halfW, y: node.y + sx * halfW * (dy / dx) };
  }
  // Exits the top/bottom edge.
  const sy = dy > 0 ? 1 : -1;
  return { x: node.x + sy * halfH * (dx / dy), y: node.y + sy * halfH };
}


/**
 * Pick the colour token for an edge based on association semantics.
 *
 * - Hub → grow: standby blue (the standard linkage cue).
 * - Hub → effector OR grow → effector: tracks the effector's state.
 *   - on  → status-normal (green).
 *   - auto + off → status-standby (blue, "armed but quiet").
 *   - manually-off → status-off (grey).
 *
 * Returns the CSS `var(--…)` reference so the consumer can drop it
 * straight into a `stroke` attribute or `color` style.
 *
 * @param {{kind: string}} parent
 * @param {{kind: string, state?: string, mode?: string}} child
 * @returns {string}
 */
export function edgeColorFor(parent, child) {
  if (parent.kind === "hub" && child.kind === "grow") {
    return "var(--color-status-standby, #2dccff)";
  }
  if (child.kind === "effector") {
    if (child.state === "on") return "var(--color-status-normal, #56f000)";
    if (child.mode === "auto") return "var(--color-status-standby, #2dccff)";
    return "var(--color-status-off, #a4abb6)";
  }
  return "var(--color-text-secondary, #b0bec5)";
}


// ─── Renderer ─────────────────────────────────────────────────────────


/**
 * Estimate card half-size for edge anchoring. Hub gets a bigger box
 * (more telemetry tiles + sparkline), effectors are smallest.
 */
function _cardHalfSize(kind) {
  if (kind === "hub") return { w: 240 / 2, h: 130 / 2 };
  if (kind === "effector") return { w: 200 / 2, h: 110 / 2 };
  return { w: 220 / 2, h: 130 / 2 };
}


/**
 * Render the full graph (edges + node placeholders) into a fresh
 * detached wrapper element. The caller mounts the returned element
 * into `#tp-graph-host` (or replaces the existing inner content).
 *
 * @param {object} args
 * @param {Array} args.nodes        Full topology node list.
 * @param {Object} args.positions   `{id: {x, y}}` world positions.
 * @param {{x: number, y: number, k: number}} args.viewport
 * @param {Document} args.ownerDocument
 * @param {object} [args.handlers]  Optional callbacks for node cards.
 * @returns {HTMLElement}
 */
export function renderGraph({ nodes, positions, viewport, ownerDocument, handlers = {} }) {
  const doc = ownerDocument || document;

  const inner = doc.createElement("div");
  inner.className = "tp-graph-inner";
  applyViewport(inner, viewport);

  // ── SVG layer: edges only ────────────────────────────────────────
  const SVG_NS = "http://www.w3.org/2000/svg";
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "tp-graph-svg");
  // viewBox is centred on the origin so world coords map directly to
  // pixels. The wrapper transform handles pan/zoom.
  svg.setAttribute("xmlns", SVG_NS);
  svg.setAttribute("overflow", "visible");

  const edgesG = doc.createElementNS(SVG_NS, "g");
  edgesG.setAttribute("class", "tp-edges");

  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  for (const child of nodes) {
    if (!child.parent) continue;
    const parent = byId[child.parent];
    if (!parent) continue;
    const aPos = positions[parent.id];
    const bPos = positions[child.id];
    if (!aPos || !bPos) continue;
    const sizeP = _cardHalfSize(parent.kind);
    const sizeC = _cardHalfSize(child.kind);
    const aAnc = anchorOn(aPos, bPos, sizeP.w, sizeP.h);
    const bAnc = anchorOn(bPos, aPos, sizeC.w, sizeC.h);
    const colour = edgeColorFor(parent, child);
    const d = edgePath(aAnc, bAnc, "bezier");
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "tp-edge");
    path.setAttribute("d", d);
    path.setAttribute("stroke", colour);
    path.setAttribute("stroke-width", "1.5");
    path.setAttribute("fill", "none");
    edgesG.appendChild(path);
  }
  svg.appendChild(edgesG);
  inner.appendChild(svg);

  // ── HTML layer: node placeholder divs ───────────────────────────
  // Phase 5 ships empty divs (or the card content if components are
  // wired via Task 6.7). They sit in absolute world coords; the
  // wrapper transform places them on screen.
  const nodesLayer = doc.createElement("div");
  nodesLayer.className = "tp-nodes-layer";

  // The renderXCard imports are top-level so the renderer can populate
  // each node's content based on its kind. If a card module isn't
  // available yet (Phase 5 ships before 6) the helper returns an
  // empty placeholder and the test still finds .tp-node divs.
  for (const node of nodes) {
    const pos = positions[node.id];
    if (!pos) continue;
    const div = doc.createElement("div");
    div.className = `tp-node tp-node-${node.kind}`;
    div.dataset.nodeId = node.id;
    div.style.left = `${pos.x}px`;
    div.style.top = `${pos.y}px`;
    const cardContent = _renderCardForNode(node, doc, handlers);
    if (cardContent) div.appendChild(cardContent);
    nodesLayer.appendChild(div);
  }
  inner.appendChild(nodesLayer);

  return inner;
}


/**
 * Switch on `node.kind` and delegate to the appropriate card renderer.
 * Returns `null` for unknown kinds (Phase 5 ships before the card
 * components land — empty placeholders are fine for the layout tests).
 *
 * Card renderers are passed in via `handlers.cardRenderers`:
 *   { hub, grow, effector }
 * — see page.mjs::boot. Keeping the imports out of this module means
 * the pure-renderer test in test_topology_graph.mjs doesn't have to
 * stub the card modules.
 */
function _renderCardForNode(node, doc, handlers) {
  const cardRenderers = handlers.cardRenderers || {};
  const history = (handlers && handlers.history && handlers.history[node.id]) || {};
  if (node.kind === "hub" && cardRenderers.hub) {
    return cardRenderers.hub(node, history, doc);
  }
  if (node.kind === "grow" && cardRenderers.grow) {
    return cardRenderers.grow(node, history, doc);
  }
  if (node.kind === "effector" && cardRenderers.effector) {
    const isAdmin = (doc.body && doc.body.dataset && doc.body.dataset.role === "admin");
    return cardRenderers.effector(node, doc, {
      onMode: handlers.onMode || (() => {}),
      isAdmin,
    });
  }
  return null;
}


/**
 * Apply a viewport to the inner wrapper transform. Public helper so
 * the page boot can update pan/zoom without re-rendering every node.
 */
export function applyViewport(wrapInner, viewport) {
  if (!wrapInner) return;
  const vp = viewport || { x: 0, y: 0, k: 1 };
  wrapInner.style.transformOrigin = "0 0";
  wrapInner.style.transform =
    `translate(${vp.x}px, ${vp.y}px) scale(${vp.k})`;
}


// ─── Pan / zoom / drag interaction ───────────────────────────────────


/**
 * Wire pan-on-canvas-drag onto the wrapper element. Mousedown must
 * land on the SVG itself (not a node card) — node-drag has its own
 * handler and we don't want both to fire simultaneously.
 *
 * Less than 2px of total movement is treated as a click and does NOT
 * fire onChange — matches the "drag-vs-click" convention used on the
 * node cards so the operator's empty-canvas click can still deselect
 * later (Phase 8 wiring).
 *
 * @param {object} args
 * @param {HTMLElement} args.wrapEl     Hosts the SVG + node layer.
 * @param {() => {x,y,k}} args.getViewport
 * @param {(vp: {x,y,k}) => void} args.onChange
 */
export function setupPan({ wrapEl, getViewport, onChange }) {
  let dragState = null;
  wrapEl.addEventListener("mousedown", (ev) => {
    // Only the SVG itself initiates a pan — node clicks and panel
    // controls handle their own events.
    const onSvg = ev.target.closest("svg.tp-graph-svg") === ev.target ||
      (ev.target.tagName === "svg" && ev.target.classList.contains("tp-graph-svg"));
    if (!onSvg) return;
    if (ev.button !== 0) return;
    const vp = getViewport();
    dragState = {
      startX: ev.clientX,
      startY: ev.clientY,
      origX: vp.x,
      origY: vp.y,
      origK: vp.k,
      moved: 0,
    };
  });
  const win = wrapEl.ownerDocument.defaultView || globalThis;
  win.addEventListener("mousemove", (ev) => {
    if (!dragState) return;
    const dx = ev.clientX - dragState.startX;
    const dy = ev.clientY - dragState.startY;
    dragState.moved = Math.max(dragState.moved, Math.hypot(dx, dy));
    if (dragState.moved < 2) return;
    onChange({
      x: dragState.origX + dx,
      y: dragState.origY + dy,
      k: dragState.origK,
    });
  });
  win.addEventListener("mouseup", () => {
    dragState = null;
  });
}


/**
 * Wire wheel-zoom onto the wrapper. Zoom is centred on the cursor's
 * position so the point under the mouse stays put (the "natural" map
 * zoom UX). Clamped to [0.3, 2.5] per the design spec.
 */
export function setupZoom({ wrapEl, getViewport, onChange }) {
  wrapEl.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const vp = getViewport();
    const rect = wrapEl.getBoundingClientRect();
    const cx = ev.clientX - rect.left;
    const cy = ev.clientY - rect.top;
    // World coords under cursor before zoom.
    const worldX = (cx - vp.x) / vp.k;
    const worldY = (cy - vp.y) / vp.k;
    // Scale factor — wheel-up (negative deltaY) zooms IN.
    const factor = Math.exp(-ev.deltaY * 0.001);
    let newK = vp.k * factor;
    newK = Math.max(0.3, Math.min(2.5, newK));
    // New viewport keeps the point under the cursor stationary.
    onChange({
      x: cx - worldX * newK,
      y: cy - worldY * newK,
      k: newK,
    });
  }, { passive: false });
}


/**
 * Wire mousedown-drag onto a single node element. World-space delta is
 * `screen-delta / viewport.k` so the node stays under the cursor while
 * being dragged at any zoom level.
 *
 * Less than 2px movement triggers `onClick(nodeId)` instead of
 * `onChange`. `stopPropagation` on the node's mousedown prevents the
 * wrapping pan handler from firing.
 *
 * After a real drag (≥2px) the mouseup additionally fires
 * `onDragEnd(nodeId, finalPos)` — used by the page boot's debounced
 * bulk-save queue (Phase 11 Task 11.1) so the wire-flushing logic
 * doesn't have to track every onChange tick. Clicks never fire
 * onDragEnd, only onClick.
 *
 * @param {object} args
 * @param {HTMLElement} args.nodeEl
 * @param {string} args.nodeId
 * @param {() => {x, y}} args.getPos
 * @param {() => {x, y, k}} args.getViewport
 * @param {(id: string, pos: {x, y}) => void} args.onChange
 * @param {(id: string) => void} args.onClick
 * @param {(id: string, pos: {x, y}) => void} [args.onDragEnd]
 */
export function setupNodeDrag({
  nodeEl, nodeId, getPos, getViewport,
  onChange, onClick, onDragEnd,
}) {
  let dragState = null;
  nodeEl.addEventListener("mousedown", (ev) => {
    // Don't drag when the mousedown landed on a button inside the
    // card (AUTO/ON/OFF segments, admin cog). Those handlers
    // stopPropagation themselves but the closest-check is a safety
    // net.
    if (ev.target.closest("button")) return;
    if (ev.button !== 0) return;
    // Block the wrapping pan handler — we own this drag.
    ev.stopPropagation();
    const pos = getPos();
    dragState = {
      startX: ev.clientX,
      startY: ev.clientY,
      origX: pos.x,
      origY: pos.y,
      moved: 0,
      lastPos: { x: pos.x, y: pos.y },
    };
  });
  const doc = nodeEl.ownerDocument;
  const win = doc.defaultView || globalThis;
  win.addEventListener("mousemove", (ev) => {
    if (!dragState) return;
    const dx = ev.clientX - dragState.startX;
    const dy = ev.clientY - dragState.startY;
    dragState.moved = Math.max(dragState.moved, Math.hypot(dx, dy));
    if (dragState.moved < 2) return;
    const vp = getViewport();
    const next = {
      x: dragState.origX + dx / vp.k,
      y: dragState.origY + dy / vp.k,
    };
    dragState.lastPos = next;
    onChange(nodeId, next);
  });
  win.addEventListener("mouseup", () => {
    if (!dragState) return;
    if (dragState.moved < 2) {
      onClick(nodeId);
    } else if (typeof onDragEnd === "function") {
      onDragEnd(nodeId, dragState.lastPos);
    }
    dragState = null;
  });
}

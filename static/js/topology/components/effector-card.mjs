/**
 * Effector card — pluggable equipment on the topology graph (Phase 6
 * Tasks 6.5 + 6.6).
 *
 * Port of `docs/assets/effector-map-handoff/nodes.jsx::EffectorCard`
 * with the on-card AUTO / ON / OFF segmented control wired up to fire
 * the page-level `onMode(id, mode)` callback (which posts to
 * /api/effectors/<id>/state).
 *
 * Structure:
 *
 *   <div class="tp-card tp-card-effector">
 *     <span class="tp-card-stripe"></span>
 *     <div class="tp-card-head">
 *       <div class="tp-card-title">Room fan</div>
 *       <div class="tp-card-sub">fan</div>
 *       <button data-action="open-config">⚙</button>   ← admin only
 *     </div>
 *     <div class="tp-status-row">
 *       <span class="tp-pill tp-pill-…">…</span>
 *       <span class="tp-effector-role">FAN</span>
 *     </div>
 *     <div class="tp-modebar">
 *       <button data-mode="auto" aria-pressed="true|false" class="active?">AUTO</button>
 *       <button data-mode="on"   aria-pressed=…>ON</button>
 *       <button data-mode="off"  aria-pressed=…>OFF</button>
 *     </div>
 *   </div>
 *
 * Mode-bar buttons stop propagation on BOTH mousedown and click so
 * the wrapping `.tp-node` drag handler doesn't fire when the operator
 * clicks ON/OFF. Without this, every mode-change would also count as
 * a node-drag and the page would re-render mid-click.
 *
 * Admin cog button only renders when `isAdmin === true`; it surfaces
 * an explicit data-action="open-config" target so the Phase 8 side
 * panel can hook in.
 */

import { renderEffectorStatusPill } from "./effector-status-pill.mjs";
import { renderModeBar } from "./mode-bar.mjs";


const EFFECTOR_COLOUR = "var(--color-status-serious, #ffb302)";


function _statusForCard(node) {
  // The status-pill state maps from the topology node fields:
  //   * current_state="on"          → "on" (solid pill)
  //   * mode="auto" + state="off"   → "auto" (armed but quiet)
  //   * otherwise                   → "off"
  if (node.current_state === "on") return { state: "on", solid: true };
  if (node.mode === "auto") return { state: "auto", solid: false };
  return { state: "off", solid: false };
}


function _statusLabel(node) {
  if (node.current_state === "on") return "ON";
  if (node.mode === "auto") return "AUTO";
  return "OFF";
}


/**
 * Render the effector card.
 *
 * @param {object} node      One topology node (kind=effector).
 * @param {Document} doc
 * @param {object} options
 * @param {(id: string, mode: string) => void} [options.onMode]
 *        Click handler for AUTO/ON/OFF. Wired to the page-level
 *        setEffectorState API call by page.mjs.
 * @param {boolean} [options.isAdmin]  Show the admin cog when true.
 * @returns {HTMLDivElement}
 */
export function renderEffectorCard(node, doc = document, options = {}) {
  const onMode = options.onMode || (() => {});
  const isAdmin = !!options.isAdmin;

  const card = doc.createElement("div");
  card.className = "tp-card tp-card-effector";
  card.style.setProperty("--node-color", EFFECTOR_COLOUR);

  // Left-edge type stripe.
  const stripe = doc.createElement("span");
  stripe.className = "tp-card-stripe";
  card.appendChild(stripe);

  // Header: title + effector_type sub-label + (admin only) cog.
  const head = doc.createElement("div");
  head.className = "tp-card-head";

  const title = doc.createElement("div");
  title.className = "tp-card-title";
  title.textContent = node.label || node.id;
  head.appendChild(title);

  if (node.effector_type) {
    const sub = doc.createElement("div");
    sub.className = "tp-card-sub";
    sub.textContent = node.effector_type;
    head.appendChild(sub);
  }

  if (isAdmin) {
    const cog = doc.createElement("button");
    cog.type = "button";
    cog.className = "tp-card-cog";
    cog.dataset.action = "open-config";
    cog.dataset.nodeId = node.id;
    cog.setAttribute("aria-label", "Configure effector");
    cog.textContent = "⚙";
    // Same propagation guards as the mode-bar buttons — the cog
    // shouldn't trigger the node drag.
    cog.addEventListener("mousedown", (ev) => { ev.stopPropagation(); });
    cog.addEventListener("click", (ev) => { ev.stopPropagation(); });
    head.appendChild(cog);
  }

  card.appendChild(head);

  // Status row: pill on the left, effector type label on the right.
  const statusRow = doc.createElement("div");
  statusRow.className = "tp-status-row";
  const pillCfg = _statusForCard(node);
  const pill = renderEffectorStatusPill({
    state: pillCfg.state,
    label: _statusLabel(node),
    solid: pillCfg.solid,
    ownerDocument: doc,
  });
  statusRow.appendChild(pill);

  const role = doc.createElement("span");
  role.className = "tp-effector-role";
  role.textContent = (node.effector_type || "").toUpperCase();
  statusRow.appendChild(role);

  card.appendChild(statusRow);

  // AUTO / ON / OFF segmented control — shared with the side panel
  // (Phase 8 Task 8.2) so the on-card and in-panel mode toggles stay
  // pixel-and-behaviour identical.
  const bar = renderModeBar({
    nodeId: node.id,
    mode: node.mode,
    onMode,
    doc,
  });
  card.appendChild(bar);

  return card;
}

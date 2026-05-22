/**
 * AUTO / ON / OFF segmented mode-bar (Phase 8 Task 8.2 extraction).
 *
 * Shared between the on-card mode-bar (`effector-card.mjs`) and the
 * side-panel Mode section (`side-panel.mjs`). Both surfaces want the
 * exact same control: three buttons that send `(id, mode)` to the
 * page-level callback (which posts to `/api/effectors/<id>/state`),
 * and which guard mousedown + click against the surrounding card-drag
 * handler.
 *
 * Structure:
 *
 *   <div class="tp-modebar">
 *     <button data-mode="auto" aria-pressed="…">AUTO</button>
 *     <button data-mode="on"   aria-pressed="…">ON</button>
 *     <button data-mode="off"  aria-pressed="…">OFF</button>
 *   </div>
 *
 * The propagation guards are essential when the mode-bar lives inside
 * the draggable .tp-node card on the graph. They're harmless when the
 * bar lives inside the side panel (which doesn't have a drag handler),
 * so we keep them unconditionally — single behaviour, single test surface.
 */


function _modeButton(doc, label, value, isCurrent, onMode, nodeId) {
  const btn = doc.createElement("button");
  btn.type = "button";
  btn.className = isCurrent ? "tp-modebtn active" : "tp-modebtn";
  btn.dataset.mode = value;
  btn.setAttribute("aria-pressed", isCurrent ? "true" : "false");
  btn.textContent = label;
  // stopPropagation on BOTH mousedown and click — the wrapping .tp-node
  // drag handler listens on mousedown, and the page-level click handler
  // (side panel open-on-card-click) listens on click. Without both
  // guards either one would fire when the operator changes mode.
  btn.addEventListener("mousedown", (ev) => { ev.stopPropagation(); });
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    onMode(nodeId, value);
  });
  return btn;
}


/**
 * Render the segmented mode-bar.
 *
 * @param {object} opts
 * @param {string} opts.nodeId       The effector id (e.g. "effector:7").
 *   Surfaces as the first arg to the onMode callback.
 * @param {"auto"|"on"|"off"} opts.mode  The currently-active mode.
 *   Used to set aria-pressed + the .active class on the matching button.
 * @param {Function} opts.onMode    `(nodeId, mode) => void`. Wired to the
 *   page-level setEffectorState API call by page.mjs.
 * @param {Document} [opts.doc=document]
 * @returns {HTMLDivElement} A `<div class="tp-modebar">`.
 */
export function renderModeBar({ nodeId, mode, onMode, doc = document }) {
  const bar = doc.createElement("div");
  bar.className = "tp-modebar";
  const handler = typeof onMode === "function" ? onMode : (() => {});
  bar.appendChild(_modeButton(doc, "AUTO", "auto", mode === "auto", handler, nodeId));
  bar.appendChild(_modeButton(doc, "ON",   "on",   mode === "on",   handler, nodeId));
  bar.appendChild(_modeButton(doc, "OFF",  "off",  mode === "off",  handler, nodeId));
  return bar;
}

/**
 * Topology side panel (Phase 8).
 *
 * The slide-out configuration surface anchored to the right edge of the
 * /controls viewport. Renders a per-node-kind configuration block when
 * a node is selected; collapses to an empty `.hidden` shell when none is.
 *
 * Structure:
 *
 *   <aside class="tp-sidepanel [hidden]" data-node-id="…">
 *     <header class="tp-sidepanel-head">
 *       <div class="tp-sidepanel-title">Room fan</div>
 *       <div class="tp-sidepanel-sub">fan · effector:7</div>
 *       <button data-testid="tp-sidepanel-close">×</button>
 *     </header>
 *     <div class="tp-sidepanel-body">
 *       …kind-specific sections (Mode / Power / Hardware /
 *         Belongs-to / Schedule for effectors;
 *         Plant / Live sensors / Linked effectors for grows;
 *         Room sensors / Coordination / Subsystems for hub)…
 *     </div>
 *   </aside>
 *
 * Per the plan the panel is server-mounted into `#tp-sidepanel-host` —
 * the page boot replaces the host's children on every selection change.
 * Keeping the panel as a pure renderer (no DOM mutation against the
 * host) means the same component can be unit-tested against a detached
 * DOM in JSDOM, and the boot stays the single source of truth for
 * mounting.
 *
 * Task 8.1 owns: open/close mechanics + the close × button + the
 * header chrome. Tasks 8.2-8.5 add the per-kind sections below (Mode
 * bar, Power slider, Hardware grid, Belongs-to picker, schedule grid,
 * Plant/Live sensors blocks, Hub sub-systems).
 */

import { renderModeBar } from "./mode-bar.mjs";


/** Build a labelled section wrapper: <section class="tp-sect">
 * <div class="tp-sect-h">…</div> …content… </section>.
 *
 * Sections are the primary visual unit of the panel — every block of
 * controls (Mode, Power, Hardware, Belongs to, Schedule on the effector
 * variant; Plant, Live sensors, Linked effectors on the grow variant;
 * Room sensors, Coordination, Subsystems on the hub variant) lives in
 * its own section with a consistent heading style. */
function _section(doc, heading) {
  const sect = doc.createElement("section");
  sect.className = "tp-sect";
  const h = doc.createElement("div");
  h.className = "tp-sect-h";
  h.textContent = heading;
  sect.appendChild(h);
  return sect;
}


/** Build a key-value grid row inside an existing .tp-kv-grid wrapper. */
function _kv(doc, gridEl, key, value) {
  const k = doc.createElement("span");
  k.className = "tp-kv-k";
  k.textContent = key;
  gridEl.appendChild(k);
  const v = doc.createElement("span");
  v.className = "tp-kv-v";
  v.textContent = value == null || value === "" ? "—" : String(value);
  gridEl.appendChild(v);
}


/** Build a key-value grid wrapper. */
function _kvGrid(doc) {
  const g = doc.createElement("div");
  g.className = "tp-kv-grid";
  return g;
}


/**
 * Build the side-panel chrome (header with title/sub/close button).
 * Returns the header element + the body container so the caller can
 * append kind-specific sections.
 *
 * @returns {{header: HTMLElement, body: HTMLElement}}
 */
function _renderHeader(doc, node, onClose) {
  const header = doc.createElement("header");
  header.className = "tp-sidepanel-head";

  const titleBox = doc.createElement("div");
  titleBox.className = "tp-sidepanel-titlebox";

  const title = doc.createElement("div");
  title.className = "tp-sidepanel-title";
  title.textContent = node.label || node.id;
  titleBox.appendChild(title);

  const sub = doc.createElement("div");
  sub.className = "tp-sidepanel-sub";
  // Surface the kind + canonical id so admins can correlate the panel
  // back to the topology snapshot easily. Effectors get the type ("fan",
  // "humidifier") inlined so the operator doesn't have to scroll to
  // Hardware to remember what they're tweaking.
  if (node.kind === "effector" && node.effector_type) {
    sub.textContent = `${node.effector_type} · ${node.id}`;
  } else {
    sub.textContent = `${node.kind} · ${node.id}`;
  }
  titleBox.appendChild(sub);
  header.appendChild(titleBox);

  const closeBtn = doc.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "tp-sidepanel-close";
  closeBtn.dataset.testid = "tp-sidepanel-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";  // U+00D7 multiplication sign
  closeBtn.addEventListener("click", () => {
    if (typeof onClose === "function") onClose();
  });
  header.appendChild(closeBtn);

  return header;
}


/**
 * Render the side panel.
 *
 * @param {object} opts
 * @param {object|null} opts.node           Selected topology node (any
 *   kind), or `null` for the hidden state.
 * @param {Array<object>} [opts.allNodes]   Full node list (used by the
 *   "Belongs to" picker for effectors + "Linked effectors" for grows).
 * @param {Document} [opts.doc=document]    JSDOM in tests.
 * @param {boolean} [opts.isAdmin=false]    Gates write-affecting controls.
 * @param {object} [opts.callbacks]
 * @param {Function} [opts.callbacks.onClose]
 *   Called when the close × button is clicked. The page boot wipes its
 *   `selectedNodeId` state + re-renders the panel into the hidden state.
 * @param {Function} [opts.callbacks.onReparent]
 *   `(effectorId, newParentId) => void` — Task 8.3.
 * @param {Function} [opts.callbacks.onModeChange]
 *   `(effectorId, mode) => void` — Task 8.2 (delegates to setEffectorState).
 * @param {Function} [opts.callbacks.onRename]
 *   `(effectorId, newLabel) => void` — reserved for a later task.
 * @returns {HTMLElement} An <aside class="tp-sidepanel">.
 */
export function renderSidePanel({
  node = null,
  allNodes = [],
  doc = document,
  isAdmin = false,
  callbacks = {},
} = {}) {
  const aside = doc.createElement("aside");
  aside.className = "tp-sidepanel";

  // Hidden when no node is selected — `.hidden` class flips the
  // transform off-screen via topology.css.
  if (!node) {
    aside.classList.add("hidden");
    return aside;
  }

  aside.dataset.nodeId = node.id;
  // Mark the kind on the root so the topology.css can theme each
  // variant (e.g. left stripe colour) without per-section duplication.
  aside.dataset.kind = node.kind;

  const header = _renderHeader(doc, node, callbacks.onClose);
  aside.appendChild(header);

  const body = doc.createElement("div");
  body.className = "tp-sidepanel-body";
  aside.appendChild(body);

  if (node.kind === "effector") {
    _renderEffectorBody(body, node, allNodes, doc, isAdmin, callbacks);
  } else if (node.kind === "grow") {
    _renderGrowBody(body, node, allNodes, doc, callbacks);
  } else if (node.kind === "hub") {
    _renderHubBody(body, node, allNodes, doc);
  }

  return aside;
}


// ─── Effector variant (Tasks 8.2 + 8.3 + 8.4) ───────────────────────────


function _renderEffectorBody(body, node, allNodes, doc, isAdmin, callbacks) {
  // Mode section — segmented AUTO/ON/OFF mirroring the on-card bar.
  const modeSect = _section(doc, "Mode");
  const bar = renderModeBar({
    nodeId: node.id,
    mode: node.mode,
    onMode: (id, mode) => {
      if (typeof callbacks.onModeChange === "function") {
        callbacks.onModeChange(id, mode);
      }
    },
    doc,
  });
  modeSect.appendChild(bar);
  body.appendChild(modeSect);

  // Power section — slider input for the desired output level (0-100%).
  // Per the spec the slider is visual-only in v1: the smart_plugs
  // backend is on/off-only today; the slider sets a "target_power"
  // value that gets stored but doesn't yet drive hardware. Disabled
  // when the effector is forced OFF or currently OFF.
  const powerSect = _section(doc, "Power");
  const row = doc.createElement("div");
  row.className = "tp-slider-row";
  const slider = doc.createElement("input");
  slider.type = "range";
  slider.min = "0";
  slider.max = "100";
  slider.step = "1";
  // Default power: 100% when on / auto, last-known target otherwise.
  const initialPower = (node.target_power != null)
    ? Number(node.target_power)
    : (node.current_state === "on" ? 100 : 0);
  slider.value = String(initialPower);
  slider.className = "tp-power-slider";
  if (!isAdmin || node.mode === "off" || node.current_state === "off") {
    // Viewers can see the position but not move it. The CSS dims the
    // thumb to communicate disabled state.
    slider.disabled = true;
  }
  row.appendChild(slider);
  const readout = doc.createElement("span");
  readout.className = "tp-slider-value";
  readout.textContent = `${slider.value}%`;
  slider.addEventListener("input", () => {
    readout.textContent = `${slider.value}%`;
  });
  row.appendChild(readout);
  powerSect.appendChild(row);
  body.appendChild(powerSect);

  // Belongs-to picker — Task 8.3 (re-parent control). Stays after
  // Power so the most-common interactions (Mode, Power) live at the
  // top of the panel.
  body.appendChild(_renderBelongsToPicker(node, allNodes, doc, callbacks));

  // Schedule grid — Task 8.4 (render-only with v2 marker).
  body.appendChild(_renderScheduleGrid(node, doc));

  // Hardware section — read-only kv grid of the wire-level details.
  const hwSect = _section(doc, "Hardware");
  const hwGrid = _kvGrid(doc);
  _kv(doc, hwGrid, "Type", node.effector_type);
  _kv(doc, hwGrid, "Kasa host", node.kasa_host);
  _kv(doc, hwGrid, "Protocol", node.protocol || "kasa");
  hwSect.appendChild(hwGrid);
  body.appendChild(hwSect);
}


// Task 8.3 + 8.4 stubs — replaced by full implementations in those tasks.


function _renderBelongsToPicker(node, allNodes, doc, callbacks) {
  // Placeholder — Task 8.3 fills this in. Returning an empty section
  // keeps the body layout consistent during the TDD progression.
  void node; void allNodes; void callbacks;
  return _section(doc, "Belongs to");
}


function _renderScheduleGrid(node, doc) {
  // Placeholder — Task 8.4 fills this in.
  void node;
  return _section(doc, "Schedule");
}


// ─── Grow variant (Task 8.5) ────────────────────────────────────────────


function _renderGrowBody(body, node, allNodes, doc, callbacks) {
  // Placeholder — Task 8.5 fills this in.
  void body; void node; void allNodes; void doc; void callbacks;
}


// ─── Hub variant (Task 8.5) ─────────────────────────────────────────────


function _renderHubBody(body, node, allNodes, doc) {
  // Placeholder — Task 8.5 fills this in.
  void body; void node; void allNodes; void doc;
}

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

  // Per-node-kind sections land in Tasks 8.2-8.5. Task 8.1 ships the
  // open/close shell.
  // Suppress unused parameter linting — these will be wired in
  // subsequent tasks.
  void allNodes; void isAdmin;

  return aside;
}

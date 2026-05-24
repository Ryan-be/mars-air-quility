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
import { renderSparkline } from "./sparkline.mjs";


/** Humanise an ISO timestamp into "Xs ago" / "Xm ago" / "Xh ago" copy.
 *
 * The Why? section + Recent activity block both need a compact "how
 * long ago did the evaluator last fire" string. Falls back to the raw
 * ISO when parsing fails so the operator still sees *something*
 * instead of "Invalid date".
 */
function _timeAgo(iso) {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 5)   return "just now";
  if (seconds < 60)  return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}


/**
 * Per-effector-type scope whitelist. MIRRORS
 * mlss_monitor.effectors.base.COMPATIBLE_SCOPES (and the parallel copy
 * in add-effector-modal.mjs). Keeping a third copy here vs reaching
 * across the boundary into add-effector-modal feels duplicative, but
 * importing it would couple the panel to the modal — and the matrix
 * is a 12-line constant that very rarely changes. The server-side v2
 * API still validates on PATCH, so a drift fails loudly via a 400. */
const COMPATIBLE_SCOPES = {
  fan:                 ["hub"],
  fan_carbon_filter:   ["hub"],
  circulation_fan:     ["hub"],
  ac:                  ["hub"],
  whole_room_heater:   ["hub"],
  dehumidifier:        ["hub"],
  humidifier:          ["hub", "grow_unit"],
  light_supplementary: ["hub", "grow_unit"],
  heat_pad:            ["grow_unit"],
  generic:             ["hub", "grow_unit"],
  co2_injector:        ["hub"],
};


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
 * @param {Function} [opts.callbacks.onDelete]
 *   `(effectorId) => void` — admin-only. Gated by ``window.confirm()``
 *   inside the panel so the page boot just has to issue the DELETE.
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
  // Mode section — segmented AUTO/ON/OFF mirroring the on-card bar, but
  // sized larger per the prototype's "bigseg" (38px tall in the design).
  // The `.tp-sect-mode` class on the section + the bar's existing
  // `.tp-modebar` class let topology.css apply the panel-only sizing
  // without touching the on-card variant.
  const modeSect = _section(doc, "Mode");
  modeSect.classList.add("tp-sect-mode");
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
  bar.classList.add("tp-panel-modebar");
  modeSect.appendChild(bar);
  body.appendChild(modeSect);

  // Why? section — under the Mode bar so the operator sees the
  // current decision + reasoning right next to the override controls.
  body.appendChild(_renderWhySection(node, doc));

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

  // Last-known reading — shows what the plug is actually doing today
  // (vs the slider's staged change). For binary on/off plugs that's
  // 100/0 derived from current_state; once we track real per-plug
  // power readings the field will move to node.power_w.
  const lastKnown = doc.createElement("div");
  lastKnown.className = "tp-power-last-known";
  const reading = node.current_state === "on" ? "100%" : "0%";
  lastKnown.textContent = `Last reading: ${reading}`;
  powerSect.appendChild(lastKnown);
  body.appendChild(powerSect);

  // Hardware section — read-only kv grid of the wire-level details.
  // Kept above Belongs-to/Schedule so operators looking at the panel
  // to identify which plug they've selected don't have to scroll.
  const hwSect = _section(doc, "Hardware");
  const hwGrid = _kvGrid(doc);
  _kv(doc, hwGrid, "Type", node.effector_type);
  _kv(doc, hwGrid, "Kasa host", node.kasa_host);
  _kv(doc, hwGrid, "Protocol", node.protocol || "kasa");
  hwSect.appendChild(hwGrid);
  body.appendChild(hwSect);

  // Belongs-to picker — Task 8.3 (re-parent control). Stays after
  // the wire-level Hardware block so the picker doesn't push every
  // other section off-screen on small viewports.
  body.appendChild(_renderBelongsToPicker(node, allNodes, doc, callbacks));

  // Schedule grid — Task 8.4 (render-only with v2 marker).
  body.appendChild(_renderScheduleGrid(node, doc));

  // Danger zone — admin-only Delete effector button at the bottom.
  // Confirmation dialog gates the destructive call so a stray click
  // doesn't drop a configured plug.
  if (isAdmin) {
    body.appendChild(_renderDeleteAdmin(node, doc, callbacks));
  }
}


/**
 * Render the "Why?" reasoning section under the Mode bar. Three cases:
 *
 * 1. Manual override (``mode === 'on'`` / ``mode === 'off'``) →
 *    "Forced ON|OFF by operator".
 * 2. Manual-only types (``generic`` / ``co2_injector``) → "Manual
 *    control — no auto rules" regardless of last_evaluation contents.
 * 3. Auto with a last_evaluation blob → decision pill ("ON because…")
 *    + one row per reason + an "evaluated Xs ago" timestamp.
 *
 * Backwards-compatible: a node without ``last_evaluation`` shows a
 * "Not yet evaluated" placeholder so operators see *something* on a
 * fresh-from-migration row.
 */
function _renderWhySection(node, doc) {
  const sect = _section(doc, "Why?");
  sect.classList.add("tp-why-section");

  const decision = doc.createElement("div");
  decision.className = "tp-why-decision";
  sect.appendChild(decision);

  // Case 1: operator override.
  if (node.mode === "on") {
    decision.textContent = "Forced ON by operator";
    decision.dataset.state = "on";
    return sect;
  }
  if (node.mode === "off") {
    decision.textContent = "Forced OFF by operator";
    decision.dataset.state = "off";
    return sect;
  }

  // Case 2: manual-only effector type.
  const manualTypes = new Set(["generic", "co2_injector"]);
  if (manualTypes.has(node.effector_type)) {
    decision.textContent = "Manual control — no auto rules";
    decision.dataset.state = "manual";
    return sect;
  }

  // Case 3: auto + last_evaluation. Render decision pill + reason rows.
  const evaluation = node.last_evaluation;
  if (!evaluation) {
    decision.textContent = "Not yet evaluated — waiting for the next tick";
    decision.dataset.state = "pending";
    return sect;
  }

  const verdict = evaluation.decision === "on" ? "ON" : "OFF";
  decision.dataset.state = evaluation.decision;
  decision.textContent = `${verdict} because`;

  const reasonsList = doc.createElement("ul");
  reasonsList.className = "tp-why-reasons";
  for (const r of (evaluation.reasons || [])) {
    const li = doc.createElement("li");
    li.className = "tp-why-reason";
    if (r.fired) li.classList.add("tp-why-reason-fired");
    const mark = doc.createElement("span");
    mark.className = "tp-why-mark";
    // U+2713 check mark (fired) / U+2715 multiplication (no vote) — the
    // CSS colours them green / dim grey respectively.
    mark.textContent = r.fired ? "✓" : "✕";
    li.appendChild(mark);
    const rule = doc.createElement("span");
    rule.className = "tp-why-rule";
    rule.textContent = r.rule;
    li.appendChild(rule);
    const detail = doc.createElement("span");
    detail.className = "tp-why-detail";
    detail.textContent = r.detail || "";
    li.appendChild(detail);
    reasonsList.appendChild(li);
  }
  if (!reasonsList.childNodes.length) {
    // Empty reasons list (e.g. a typed controller with no rules
    // configured yet) — fall back to the manual-only placeholder so the
    // operator gets useful copy instead of a blank section.
    decision.textContent = "Manual control — no auto rules";
    decision.dataset.state = "manual";
  } else {
    sect.appendChild(reasonsList);
  }

  const ago = _timeAgo(evaluation.evaluated_at);
  if (ago) {
    const ts = doc.createElement("div");
    ts.className = "tp-why-evaluated-at";
    ts.textContent = `Evaluated ${ago}`;
    sect.appendChild(ts);
  }
  return sect;
}


/**
 * Render the admin-only Delete-effector button block.
 *
 * The destructive call is gated by ``window.confirm()`` so a stray
 * click on the bottom-of-panel button doesn't drop the plug. The
 * page-level ``onDelete`` callback (passed in via callbacks) handles
 * the network call + the post-delete store mutation.
 */
function _renderDeleteAdmin(node, doc, callbacks) {
  const sect = _section(doc, "Danger zone");
  sect.classList.add("tp-danger-zone");
  const btn = doc.createElement("button");
  btn.type = "button";
  btn.className = "tp-delete-effector-btn";
  btn.dataset.testid = "tp-delete-effector";
  btn.textContent = "Delete effector";
  btn.addEventListener("click", () => {
    const win = doc.defaultView || globalThis;
    const confirmFn = (win && typeof win.confirm === "function")
      ? win.confirm
      : globalThis.confirm;
    const msg = `Delete "${node.label || node.id}"?\n\n` +
      "This removes the smart_plugs row + clears its persisted layout. " +
      "The physical plug isn't touched.";
    const ok = typeof confirmFn === "function" ? confirmFn(msg) : false;
    if (!ok) return;
    if (typeof callbacks.onDelete === "function") {
      callbacks.onDelete(node.id);
    }
  });
  sect.appendChild(btn);
  return sect;
}


// Task 8.3 + 8.4 stubs — replaced by full implementations in those tasks.


function _renderBelongsToPicker(node, allNodes, doc, callbacks) {
  const sect = _section(doc, "Belongs to");

  const pick = doc.createElement("div");
  pick.className = "tp-target-pick";
  sect.appendChild(pick);

  // Inline error surface — populated on a server 400 (scope mismatch).
  // Starts hidden; the picker exposes show/hide via the .hidden class
  // so the page-level onReparent handler can flip it after the PATCH
  // response lands.
  const err = doc.createElement("div");
  err.className = "tp-reparent-error hidden";
  err.dataset.testid = "tp-reparent-error";
  err.style.display = "none";
  pick.appendChild(err);

  function showError(msg) {
    err.textContent = msg;
    err.classList.remove("hidden");
    err.style.display = "";
  }
  function clearError() {
    err.textContent = "";
    err.classList.add("hidden");
    err.style.display = "none";
  }

  // Per-type scope compatibility: a heat_pad effector can't move to
  // the Hub, a fan can't move to a grow unit. Greyed-out candidates
  // surface visibly (disabled) rather than being hidden — admins
  // expect to see why a target isn't available.
  const compat = COMPATIBLE_SCOPES[node.effector_type] || ["hub", "grow_unit"];
  const hubAllowed = compat.includes("hub");
  const growAllowed = compat.includes("grow_unit");

  // Candidate list: hub plus every grow node. Each renders as a button
  // exposing `data-parent-id` ("hub" or "grow:<n>").
  const candidates = [];
  candidates.push({
    id: "hub",
    label: "Hub",
    kind: "hub",
    allowed: hubAllowed,
  });
  for (const n of allNodes) {
    if (n.kind === "grow") {
      candidates.push({
        id: n.id,
        label: n.label || n.id,
        kind: "grow",
        allowed: growAllowed,
      });
    }
  }

  for (const cand of candidates) {
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "tp-target-pick-btn";
    btn.dataset.parentId = cand.id;
    btn.dataset.kind = cand.kind;
    if (cand.id === node.parent) {
      btn.classList.add("selected");
      btn.setAttribute("aria-pressed", "true");
    } else {
      btn.setAttribute("aria-pressed", "false");
    }
    if (!cand.allowed) {
      btn.disabled = true;
      btn.title = `${node.effector_type} is not compatible with this scope`;
    }

    const swatch = doc.createElement("span");
    swatch.className = `tp-target-swatch tp-target-swatch-${cand.kind}`;
    btn.appendChild(swatch);

    const lbl = doc.createElement("span");
    lbl.className = "tp-target-lbl";
    lbl.textContent = cand.label;
    btn.appendChild(lbl);

    const sub = doc.createElement("span");
    sub.className = "tp-target-sub";
    sub.textContent = cand.kind;
    btn.appendChild(sub);

    btn.addEventListener("click", async () => {
      if (cand.id === node.parent) {
        // No-op — already the selected parent. Don't fire the network
        // call, don't bother the user with a spurious server round trip.
        return;
      }
      clearError();
      if (typeof callbacks.onReparent === "function") {
        try {
          const result = await callbacks.onReparent(node.id, cand.id);
          if (result && result.error) {
            showError(result.error);
          }
        } catch (exc) {
          // The callback may throw on a 4xx; surface the message
          // inline so the operator can correct + retry without
          // closing the panel.
          showError(exc.message || String(exc));
        }
      }
    });
    pick.appendChild(btn);
  }

  return sect;
}


function _renderScheduleGrid(node, doc) {
  // Render-only 24-cell grid + a "coming in v2" marker. Per-effector
  // schedules are out of scope for v1: the visual shape is here so
  // the panel doesn't visually regress from the prototype, but the
  // cells themselves carry no state and aren't interactive. Phase 12+
  // (or a future v2 feature) will wire schedule storage onto the
  // smart_plugs.rules_json blob.
  const sect = _section(doc, "Schedule");

  const grid = doc.createElement("div");
  grid.className = "tp-sched-grid";
  for (let h = 0; h < 24; h += 1) {
    const cell = doc.createElement("div");
    cell.className = "tp-sched-cell";
    cell.title = `${String(h).padStart(2, "0")}:00`;
    cell.dataset.hour = String(h);
    grid.appendChild(cell);
  }
  sect.appendChild(grid);

  // Axis labels (00 / 06 / 12 / 18 / 24) so the grid reads as a
  // 24-hour cycle even without per-cell interaction.
  const axis = doc.createElement("div");
  axis.className = "tp-sched-axis";
  for (const lbl of ["00", "06", "12", "18", "24"]) {
    const sp = doc.createElement("span");
    sp.textContent = lbl;
    axis.appendChild(sp);
  }
  sect.appendChild(axis);

  const marker = doc.createElement("small");
  marker.className = "tp-coming-v2";
  marker.textContent = "Per-effector schedules — coming in v2";
  sect.appendChild(marker);

  // Reference node to silence the unused-parameter lint — the cells
  // themselves don't yet take any per-node state.
  void node;

  return sect;
}


// ─── Grow variant (Task 8.5) ────────────────────────────────────────────


function _renderGrowBody(body, node, allNodes, doc, callbacks) {
  // Plant section — plant_type chip + medium/phase kv-grid. The chip
  // matches the on-card chip styling so the panel and the graph card
  // visually agree on which species this is.
  const plantSect = _section(doc, "Plant");
  if (node.plant_type) {
    const chip = doc.createElement("span");
    chip.className = "tp-plant-chip";
    chip.textContent = node.plant_type;
    plantSect.appendChild(chip);
  }
  const plantGrid = _kvGrid(doc);
  _kv(doc, plantGrid, "Phase", node.phase);
  _kv(doc, plantGrid, "Medium", node.medium);
  plantSect.appendChild(plantGrid);
  body.appendChild(plantSect);

  // Live sensors section — 4 telemetry values from the grow_telemetry
  // payload (soil_moisture, soil_temp_c, air_temp_c, air_humidity_pct).
  const sensorsSect = _section(doc, "Live sensors");
  const sensorsGrid = _kvGrid(doc);
  const sensors = node.sensors || {};
  // Surface raw values rather than formatted ones — the topology
  // endpoint already round-trips through Python's json.dumps which
  // emits whole-number floats as integers, so we don't need a separate
  // toFixed pass to keep the test assertions tight.
  _kv(doc, sensorsGrid, "Soil moisture",
    sensors.soil_moisture == null ? "—" : `${sensors.soil_moisture} %`);
  _kv(doc, sensorsGrid, "Soil temp",
    sensors.soil_temp_c == null ? "—" : `${sensors.soil_temp_c} °C`);
  _kv(doc, sensorsGrid, "Air temp",
    sensors.air_temp_c == null ? "—" : `${sensors.air_temp_c} °C`);
  _kv(doc, sensorsGrid, "Air humidity",
    sensors.air_humidity_pct == null ? "—" : `${sensors.air_humidity_pct} %`);
  sensorsSect.appendChild(sensorsGrid);

  // Soil moisture sparkline — the prototype renders a trend chart in
  // the grow panel so operators can see whether the plant is drying
  // out. The history buffer lives on the page boot's per-node history
  // dict; until SSE wires it through, the sparkline is an empty SVG
  // shell so the layout doesn't shift when the first values land.
  const sparkWrap = doc.createElement("div");
  sparkWrap.className = "tp-grow-soil-spark";
  const history = (node.history && node.history.soil_moisture) || [];
  sparkWrap.appendChild(renderSparkline({
    values: history,
    color: "var(--color-status-normal, #56f000)",
    height: 28,
    ownerDocument: doc,
  }));
  sensorsSect.appendChild(sparkWrap);
  body.appendChild(sensorsSect);

  // Linked effectors section — list every effector parented to this
  // grow unit. Each row is a button so a future iteration can wire
  // click → re-select that effector in the panel without the operator
  // having to find it in the graph.
  const linkedSect = _section(doc, "Linked effectors");
  const linkedList = doc.createElement("div");
  linkedList.className = "tp-linked-effectors";
  const linked = allNodes.filter(
    (n) => n.kind === "effector" && n.parent === node.id,
  );
  if (linked.length === 0) {
    const empty = doc.createElement("div");
    empty.className = "tp-linked-empty";
    empty.textContent = "No effectors assigned";
    linkedList.appendChild(empty);
  } else {
    for (const eff of linked) {
      const row = doc.createElement("div");
      row.className = "tp-linked-row";
      const swatch = doc.createElement("span");
      swatch.className = "tp-target-swatch tp-target-swatch-effector";
      row.appendChild(swatch);
      const lbl = doc.createElement("span");
      lbl.className = "tp-target-lbl";
      lbl.textContent = eff.label || eff.id;
      row.appendChild(lbl);
      const sub = doc.createElement("span");
      sub.className = "tp-target-sub";
      sub.textContent = eff.current_state === "on"
        ? "● on"
        : "○ " + (eff.mode || "off");
      row.appendChild(sub);
      linkedList.appendChild(row);
    }
  }
  linkedSect.appendChild(linkedList);
  body.appendChild(linkedSect);

  // "View full grow page" link — anchor to the per-unit detail route.
  // The grow id has the form "grow:<n>"; the URL is /grow/<n>.
  const link = doc.createElement("a");
  link.className = "tp-view-grow-link";
  const numeric = (node.id || "").split(":")[1] || "";
  link.href = `/grow/${numeric}`;
  link.textContent = "View full grow page";
  body.appendChild(link);

  void callbacks;
}


// ─── Hub variant (Task 8.5) ─────────────────────────────────────────────


function _renderHubBody(body, node, allNodes, doc) {
  // Room sensors section — temp / RH / CO2 from the hot-tier snapshot.
  const sensSect = _section(doc, "Room sensors");
  const sensGrid = _kvGrid(doc);
  const sensors = node.sensors || {};
  _kv(doc, sensGrid, "Temperature",
    sensors.temp == null ? "—" : `${sensors.temp} °C`);
  _kv(doc, sensGrid, "Humidity",
    sensors.rh == null ? "—" : `${sensors.rh} %`);
  _kv(doc, sensGrid, "CO₂",
    sensors.co2 == null ? "—" : `${sensors.co2} ppm`);
  sensSect.appendChild(sensGrid);
  body.appendChild(sensSect);

  // Coordination section — static narrative blurb from node.notes.
  // The topology endpoint sets a default string; admin-edited notes
  // would land here in a future iteration.
  const coordSect = _section(doc, "Coordination notes");
  if (node.notes) {
    const p = doc.createElement("p");
    p.className = "tp-coord-notes";
    p.textContent = node.notes;
    coordSect.appendChild(p);
  }
  body.appendChild(coordSect);

  // Subsystems section — count rollups so the operator can scan the
  // hub's role at a glance. Rendered as pills (vs the plain kv-grid
  // used elsewhere) so the numeric values jump out — the operator's
  // first scan of the panel is "how many things am I managing right
  // now?".
  const subSect = _section(doc, "Subsystems");
  const subGrid = doc.createElement("div");
  subGrid.className = "tp-kv-grid tp-subsystems-grid";
  const grows = allNodes.filter((n) => n.kind === "grow").length;
  const effectors = allNodes.filter((n) => n.kind === "effector").length;
  const active = allNodes.filter(
    (n) => n.kind === "effector" && n.current_state === "on",
  ).length;
  for (const [label, value, kind] of [
    ["Grows", grows, "grow"],
    ["Effectors", effectors, "effector"],
    ["Active", active, "active"],
  ]) {
    const k = doc.createElement("span");
    k.className = "tp-kv-k";
    k.textContent = label;
    subGrid.appendChild(k);
    const v = doc.createElement("span");
    v.className = "tp-kv-v tp-subsystems-pill";
    v.dataset.pill = kind;
    v.textContent = String(value);
    subGrid.appendChild(v);
  }
  subSect.appendChild(subGrid);
  body.appendChild(subSect);

  // Recent activity section — surfaces the freshest evaluator pass
  // across all effectors as a single "evaluator last ran Xs ago"
  // breadcrumb, plus how many effectors are currently in auto-mode.
  // Helps the operator notice if the evaluator has stalled.
  const recentSect = _section(doc, "Recent activity");
  const evaluations = allNodes
    .filter((n) => n.kind === "effector" && n.last_evaluation
      && n.last_evaluation.evaluated_at)
    .map((n) => n.last_evaluation.evaluated_at)
    .sort()
    .reverse();
  const recent = doc.createElement("div");
  recent.className = "tp-recent-activity";
  if (evaluations.length === 0) {
    recent.textContent = "No auto-mode evaluations recorded yet";
  } else {
    const latest = _timeAgo(evaluations[0]);
    const autoCount = allNodes.filter(
      (n) => n.kind === "effector" && n.mode === "auto",
    ).length;
    recent.textContent =
      `Evaluator last ran ${latest} · ${autoCount} effector` +
      `${autoCount === 1 ? "" : "s"} in auto-mode`;
  }
  recentSect.appendChild(recent);
  body.appendChild(recentSect);
}

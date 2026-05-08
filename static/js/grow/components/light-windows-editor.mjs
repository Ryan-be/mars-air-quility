/**
 * Light-windows editor — third panel of the Configure tab.
 *
 * Per-phase grouping (5 phases). Each phase has its own ordered list of
 * HH:MM windows, an Add-window button, and its own Save button. The
 * server's PUT replaces all windows for one (unit, phase) pair, so the
 * UI follows the same contract: editing vegetative + clicking
 * "Save vegetative" only PUTs the vegetative payload — it never touches
 * other phases.
 *
 * Why per-phase save (rather than one big "Save all" button): a partial
 * save halfway through (network blip, server 503, browser refresh)
 * shouldn't blow away the unrelated phases the user spent the previous
 * five minutes editing. Per-phase saves are atomic units of work the
 * user can mentally model.
 *
 * Validation mirrors the server's LightWindowsUpdate schema:
 *   - HH:MM 24h regex
 *   - start ≠ end (zero-length rejected)
 *   - max 8 windows per phase (Add disables at the cap)
 *
 * Doing client-side validation spares users a round-trip 400 and gives
 * them an inline error next to the field rather than a generic banner.
 */

const PHASES = ["seedling", "vegetative", "flowering", "fruiting", "dormant"];
const HHMM_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const MAX_WINDOWS_PER_PHASE = 8;


function _validateWindow(start, end) {
  if (!HHMM_RE.test(start)) return `start "${start}" is not HH:MM 24h`;
  if (!HHMM_RE.test(end)) return `end "${end}" is not HH:MM 24h`;
  if (start === end) return "start and end must differ (zero-length window)";
  return null;
}


/**
 * Build the light-windows editor panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `light_windows`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderLightWindowsEditor(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const lwIn = unit.light_windows || {};
  const currentPhase = unit.current_phase || "vegetative";

  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-light-windows";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🕐 Light schedule</span>";
  wrap.appendChild(head);

  const form = doc.createElement("div");
  form.className = "cfg-form";
  wrap.appendChild(form);

  // Per-phase mutable state. We keep arrays of {start, end} mirrors of the
  // DOM rows; the rows are the source of truth on user input (we read them
  // back at save time), but state[].length drives Add-button disable logic.
  const state = {};
  for (const phase of PHASES) {
    state[phase] = [...(lwIn[phase] || [])];
    form.appendChild(_buildPhaseGroup(doc, unit, phase, state, currentPhase));
  }

  return wrap;
}


function _buildPhaseGroup(doc, unit, phase, state, currentPhase) {
  // Design-critique #12: each phase becomes a <details> accordion. The
  // current phase opens by default; the four others stay collapsed so
  // operators don't see ten Add/Save buttons (5 phases × 2 each) when
  // only one phase is active. Native <details> means keyboard + screen-
  // reader support are free, and the summary is clickable as a whole.
  const group = doc.createElement("details");
  group.className = "cfg-lw-phase";
  group.dataset.testid = `lw-phase-${phase}`;
  if (phase === currentPhase) {
    group.open = true;
  }

  const head = doc.createElement("summary");
  head.className = "cfg-lw-phase-head";
  const title = doc.createElement("strong");
  title.textContent = phase.charAt(0).toUpperCase() + phase.slice(1);
  head.appendChild(title);
  if (phase === currentPhase) {
    const tag = doc.createElement("span");
    tag.className = "cfg-lw-current-tag";
    tag.textContent = "current";
    head.appendChild(tag);
  }
  // Status hint inside the summary so collapsed sections still tell you
  // whether they have a custom schedule or are inheriting the default.
  const summaryHint = doc.createElement("span");
  summaryHint.className = "cfg-lw-summary-hint";
  summaryHint.dataset.testid = `lw-summary-hint-${phase}`;
  const haveCustom = (state[phase] && state[phase].length > 0);
  summaryHint.textContent = haveCustom
    ? `${state[phase].length} window${state[phase].length === 1 ? "" : "s"}`
    : "using profile default";
  head.appendChild(summaryHint);
  group.appendChild(head);

  const rowsHost = doc.createElement("div");
  rowsHost.className = "cfg-lw-rows";
  rowsHost.dataset.testid = `lw-rows-${phase}`;
  group.appendChild(rowsHost);

  const empty = doc.createElement("div");
  empty.className = "cfg-lw-empty";
  empty.dataset.testid = `lw-empty-${phase}`;
  empty.textContent = "(no windows — using profile default)";
  group.appendChild(empty);

  // Action row: Add + Save + status
  const actions = doc.createElement("div");
  actions.className = "cfg-row cfg-lw-actions";

  const addBtn = doc.createElement("button");
  addBtn.type = "button";
  addBtn.className = "cfg-lw-add";
  addBtn.textContent = "+ Add window";
  addBtn.dataset.testid = `lw-add-${phase}`;
  actions.appendChild(addBtn);

  const saveBtn = doc.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "cfg-save px-btn";
  saveBtn.textContent = `Save ${phase}`;
  saveBtn.dataset.testid = `lw-save-${phase}`;
  actions.appendChild(saveBtn);

  const status = doc.createElement("span");
  status.className = "cfg-status";
  status.dataset.testid = `lw-status-${phase}`;
  actions.appendChild(status);

  group.appendChild(actions);

  // Renders each window row from `state[phase]`. Re-renders on add/remove
  // rather than mutating individual rows, because a fresh redraw makes
  // the testid index alignment easier to reason about.
  function renderRows() {
    rowsHost.innerHTML = "";
    state[phase].forEach((w, i) => {
      rowsHost.appendChild(_buildRow(doc, phase, i, w, state, renderRows));
    });
    // Toggle empty-state placeholder
    empty.style.display = state[phase].length === 0 ? "" : "none";
    // Cap Add at MAX_WINDOWS_PER_PHASE
    addBtn.disabled = state[phase].length >= MAX_WINDOWS_PER_PHASE;
    // Keep the accordion summary hint in sync with row count so collapsed
    // sections always tell you whether they're "using profile default"
    // vs "N windows" without having to expand them.
    summaryHint.textContent = state[phase].length > 0
      ? `${state[phase].length} window${state[phase].length === 1 ? "" : "s"}`
      : "using profile default";
  }

  addBtn.addEventListener("click", () => {
    if (state[phase].length >= MAX_WINDOWS_PER_PHASE) return;
    state[phase].push({ start: "", end: "" });
    renderRows();
  });

  saveBtn.addEventListener("click", async () => {
    // Read row values back from the DOM so trailing typed-but-not-blurred
    // edits aren't dropped.
    const rows = rowsHost.querySelectorAll("[data-testid^='lw-row-']");
    const windows = [];
    for (const r of rows) {
      const s = r.querySelector("[data-testid^='lw-start-']").value.trim();
      const e = r.querySelector("[data-testid^='lw-end-']").value.trim();
      windows.push({ start: s, end: e });
    }
    // Validate every window before fetch — match server constraints.
    for (let i = 0; i < windows.length; i += 1) {
      const err = _validateWindow(windows[i].start, windows[i].end);
      if (err) {
        status.textContent = `✗ Window ${i + 1}: ${err}`;
        status.className = "cfg-status err";
        return;
      }
    }
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    status.textContent = "";
    status.className = "cfg-status";
    try {
      const r = await fetch(`/api/grow/units/${unit.id}/light_windows`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phase, windows }),
      });
      if (r.ok) {
        status.textContent = "✓ Saved";
        status.className = "cfg-status ok";
        // Sync local state so subsequent Add/Remove operations stay aligned
        state[phase] = windows.map((w) => ({ ...w }));
      } else {
        const err = await r.json().catch(() => ({}));
        const msg = err.error || err.detail || r.statusText || "Save failed";
        status.textContent = `✗ ${msg}`;
        status.className = "cfg-status err";
      }
    } catch (exc) {
      status.textContent = `✗ ${exc.message || "Network error"}`;
      status.className = "cfg-status err";
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = `Save ${phase}`;
    }
  });

  renderRows();
  return group;
}


function _buildRow(doc, phase, idx, win, state, rerender) {
  const row = doc.createElement("div");
  row.className = "cfg-lw-row";
  row.dataset.testid = `lw-row-${phase}-${idx}`;

  const start = doc.createElement("input");
  start.type = "text";
  start.className = "cfg-lw-time";
  start.placeholder = "06:00";
  start.value = win.start || "";
  start.maxLength = 5;
  start.dataset.testid = `lw-start-${phase}-${idx}`;
  row.appendChild(start);

  const dash = doc.createElement("span");
  dash.className = "cfg-lw-dash";
  dash.textContent = "–";
  row.appendChild(dash);

  const end = doc.createElement("input");
  end.type = "text";
  end.className = "cfg-lw-time";
  end.placeholder = "22:00";
  end.value = win.end || "";
  end.maxLength = 5;
  end.dataset.testid = `lw-end-${phase}-${idx}`;
  row.appendChild(end);

  // Mirror DOM edits into state so Add doesn't lose unsaved typing.
  start.addEventListener("input", () => {
    state[phase][idx].start = start.value;
  });
  end.addEventListener("input", () => {
    state[phase][idx].end = end.value;
  });

  const remove = doc.createElement("button");
  remove.type = "button";
  remove.className = "cfg-lw-remove";
  remove.textContent = "×";
  remove.title = "Remove window";
  remove.dataset.testid = `lw-remove-${phase}-${idx}`;
  remove.addEventListener("click", () => {
    state[phase].splice(idx, 1);
    rerender();
  });
  row.appendChild(remove);

  return row;
}

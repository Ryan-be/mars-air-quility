/**
 * Danger Zone — fourth section of the Diagnostics tab panel.
 *
 * Four actions with progressively-stronger confirmation friction:
 *
 *   1. Rotate bearer token  — relocated from the Configure tab. The
 *                             token-rotator component owns its own
 *                             confirm + reveal flow; we just mount it
 *                             inside the danger-styled wrapper.
 *
 *   2. Decommission unit    — DELETE /api/grow/units/<id>. Friction:
 *                             operator must TYPE the unit's label (e.g.
 *                             "Tom 1") into a text input before the
 *                             Confirm button enables. Soft-delete
 *                             preserves telemetry but the unit cannot be
 *                             revived without a manual DB UPDATE.
 *
 *   3. Clear remote buffer  — POST /api/grow/units/<id>/clear-buffer.
 *                             Friction: single OK/Cancel modal (less
 *                             than safety-override's 3-click FSM because
 *                             the worst-case outcome is "lose un-replayed
 *                             telemetry", which is recoverable in the
 *                             abstract; the unit keeps running normally).
 *                             Returns 202 on confirmed delivery, 503 if
 *                             the unit is disconnected.
 *
 *   4. Clear all photos     — DELETE /api/grow/units/<id>/photos. Wipes
 *                             every photo (DB rows + JPEG files on disk)
 *                             for this unit. Use case: clearing test-
 *                             data slate before the unit goes live with
 *                             a real plant. Friction: single OK/Cancel
 *                             (same level as clear-buffer) — the worst
 *                             case is "lose your test photos", which is
 *                             cheap to re-take.
 *
 * Styling: lives inside a single .diag-danger-zone container with a red
 * border + warning header, so the whole block reads visually as "danger
 * area" before the operator clicks anything. Per-action sub-panels use
 * the .diag-danger-action selector for layout.
 */
import { renderTokenRotator } from "./token-rotator.mjs";


/**
 * Build the danger-zone panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response — needs `id`
 *                       + `label` (the label drives the type-to-confirm
 *                       guard on Decommission).
 * @param {object} opts  { ownerDocument?, fetchFn? }
 * @returns {HTMLElement}
 */
export function renderDangerZone(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "du-panel diag-danger-zone";
  wrap.dataset.testid = "diag-danger-zone";

  const head = doc.createElement("div");
  head.className = "du-panel-head diag-danger-head";
  head.innerHTML = "<span>⚠ Danger zone</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "diag-danger-body";
  wrap.appendChild(body);

  const blurb = doc.createElement("p");
  blurb.className = "diag-danger-blurb";
  blurb.textContent =
    "The actions below are administrative, irreversible (or expensive " +
    "to undo), and require admin privileges. Read each warning carefully " +
    "before clicking.";
  body.appendChild(blurb);

  // 1) Token rotator — relocated from Configure tab. The component
  //    handles its own confirm/reveal flow + 403/404 inline error
  //    surfacing, so we just mount it.
  body.appendChild(renderTokenRotator(unit, opts));

  // 2) Decommission unit
  body.appendChild(_renderDecommission(unit, doc, fetchFn));

  // 3) Clear remote buffer
  body.appendChild(_renderClearBuffer(unit, doc, fetchFn));

  // 4) Clear all photos
  body.appendChild(_renderClearPhotos(unit, doc, fetchFn));

  return wrap;
}


/** Decommission action — DELETE with type-the-label-to-confirm friction. */
function _renderDecommission(unit, doc, fetchFn) {
  const panel = doc.createElement("div");
  panel.className = "diag-danger-action";
  panel.dataset.testid = "decommission-action";

  const title = doc.createElement("h4");
  title.className = "diag-danger-title";
  // Danger-ramp icon: ⚠ for the most destructive action — decommission
  // hides the unit from the fleet view + breaks its WS auth and can't
  // be undone from the UI (only via manual DB UPDATE). See danger-zone
  // module docstring + design-critique #18 for the full ramp spec.
  title.textContent = "⚠ Decommission unit";
  panel.appendChild(title);

  const desc = doc.createElement("p");
  desc.className = "diag-danger-desc";
  desc.textContent =
    "Hide this unit from the fleet view. Telemetry history + photos are " +
    "preserved, but the unit cannot be revived without a manual DB edit. " +
    "The unit will lose its WS connection immediately.";
  panel.appendChild(desc);

  // Idle button — clicking arms the confirm pane.
  const armRow = doc.createElement("div");
  armRow.className = "diag-danger-row";
  panel.appendChild(armRow);

  const armBtn = doc.createElement("button");
  armBtn.type = "button";
  armBtn.className = "px-btn danger diag-decom-arm";
  armBtn.textContent = "⚠ Decommission unit";
  armBtn.dataset.testid = "decom-arm-btn";
  armRow.appendChild(armBtn);

  // Confirm pane — initially hidden; appears after armBtn click.
  const confirmPane = doc.createElement("div");
  confirmPane.className = "diag-danger-confirm";
  confirmPane.style.display = "none";
  confirmPane.dataset.testid = "decom-confirm";

  const warn = doc.createElement("p");
  warn.className = "diag-danger-warn";
  warn.textContent =
    `Type the unit's label (${unit.label}) below to confirm. ` +
    `This action cannot be undone from the UI.`;
  confirmPane.appendChild(warn);

  const labelInput = doc.createElement("input");
  labelInput.type = "text";
  labelInput.className = "diag-decom-label-input";
  labelInput.dataset.testid = "decom-label-input";
  labelInput.placeholder = `Type "${unit.label}" exactly`;
  confirmPane.appendChild(labelInput);

  const confirmRow = doc.createElement("div");
  confirmRow.className = "diag-danger-confirm-row";

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn danger diag-decom-confirm";
  confirmBtn.textContent = "Confirm decommission";
  confirmBtn.dataset.testid = "decom-confirm-btn";
  confirmBtn.disabled = true;  // enabled only when label matches
  confirmRow.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn diag-decom-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "decom-cancel-btn";
  confirmRow.appendChild(cancelBtn);

  confirmPane.appendChild(confirmRow);
  panel.appendChild(confirmPane);

  // Status / error surface
  const statusEl = doc.createElement("div");
  statusEl.className = "diag-danger-status";
  statusEl.dataset.testid = "decom-status";
  panel.appendChild(statusEl);

  // ── State transitions ──
  function _arm() {
    armBtn.style.display = "none";
    confirmPane.style.display = "";
    statusEl.textContent = "";
    statusEl.className = "diag-danger-status";
    labelInput.value = "";
    confirmBtn.disabled = true;
  }

  function _disarm() {
    armBtn.style.display = "";
    confirmPane.style.display = "none";
    labelInput.value = "";
    confirmBtn.disabled = true;
  }

  // Type-to-confirm gate: button enables only when the trimmed input
  // exactly matches the unit's label.
  labelInput.addEventListener("input", () => {
    confirmBtn.disabled = labelInput.value !== unit.label;
  });

  async function _fire() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Decommissioning…";
    try {
      const r = await fetchFn(`/api/grow/units/${unit.id}`, {
        method: "DELETE",
      });
      if (r.ok) {
        statusEl.textContent =
          `✓ ${unit.label} decommissioned. Refresh the fleet view.`;
        statusEl.className = "diag-danger-status ok";
        confirmPane.style.display = "none";
        // Don't show the arm button again — the unit is gone, no
        // second decommission makes sense from this page.
        armBtn.style.display = "none";
        return;
      }
      // Error path
      let msg;
      if (r.status === 403) {
        msg = "Forbidden — admin role required to decommission units.";
      } else if (r.status === 404) {
        msg = "Unit not found — it may have been deleted already.";
      } else {
        const err = await r.json().catch(() => ({}));
        msg = err.error || r.statusText || "Decommission failed";
      }
      statusEl.textContent = `✗ ${msg}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm decommission";
    } catch (exc) {
      statusEl.textContent = `✗ ${exc.message || "Network error"}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm decommission";
    }
  }

  armBtn.addEventListener("click", _arm);
  cancelBtn.addEventListener("click", _disarm);
  confirmBtn.addEventListener("click", _fire);

  return panel;
}


/** Clear remote buffer action — POST with single OK/Cancel modal. */
function _renderClearBuffer(unit, doc, fetchFn) {
  const panel = doc.createElement("div");
  panel.className = "diag-danger-action";
  panel.dataset.testid = "clear-buffer-action";

  const title = doc.createElement("h4");
  title.className = "diag-danger-title";
  // Danger-ramp icon: 🧹 for "sweep / clear" — the buffer is firmware-
  // side state (offline outbox); clearing loses unreplayed telemetry
  // but the unit keeps running normally. Less destructive than
  // clear-photos (🗑) or decommission (⚠).
  title.textContent = "🧹 Clear remote buffer";
  panel.appendChild(title);

  const desc = doc.createElement("p");
  desc.className = "diag-danger-desc";
  desc.textContent =
    "Empty the unit's local SQLite buffer. Any un-replayed telemetry " +
    "will be permanently lost. The unit keeps running normally — only " +
    "the offline-replay queue is affected.";
  panel.appendChild(desc);

  const armRow = doc.createElement("div");
  armRow.className = "diag-danger-row";
  panel.appendChild(armRow);

  const armBtn = doc.createElement("button");
  armBtn.type = "button";
  armBtn.className = "px-btn danger diag-cb-arm";
  armBtn.textContent = "🧹 Clear remote buffer";
  armBtn.dataset.testid = "clear-buffer-arm-btn";
  armRow.appendChild(armBtn);

  // Confirm pane
  const confirmPane = doc.createElement("div");
  confirmPane.className = "diag-danger-confirm";
  confirmPane.style.display = "none";
  confirmPane.dataset.testid = "clear-buffer-confirm";

  const warn = doc.createElement("p");
  warn.className = "diag-danger-warn";
  warn.textContent =
    "This will empty the unit's local SQLite buffer. Any un-replayed " +
    "telemetry will be permanently lost. Continue?";
  confirmPane.appendChild(warn);

  const confirmRow = doc.createElement("div");
  confirmRow.className = "diag-danger-confirm-row";

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn danger diag-cb-confirm";
  confirmBtn.textContent = "Yes, clear it";
  confirmBtn.dataset.testid = "clear-buffer-confirm-btn";
  confirmRow.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn diag-cb-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "clear-buffer-cancel-btn";
  confirmRow.appendChild(cancelBtn);

  confirmPane.appendChild(confirmRow);
  panel.appendChild(confirmPane);

  const statusEl = doc.createElement("div");
  statusEl.className = "diag-danger-status";
  statusEl.dataset.testid = "clear-buffer-status";
  panel.appendChild(statusEl);

  function _arm() {
    armBtn.style.display = "none";
    confirmPane.style.display = "";
    statusEl.textContent = "";
    statusEl.className = "diag-danger-status";
  }

  function _disarm() {
    armBtn.style.display = "";
    confirmPane.style.display = "none";
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
    confirmBtn.textContent = "Yes, clear it";
  }

  async function _fire() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Clearing…";
    try {
      const r = await fetchFn(`/api/grow/units/${unit.id}/clear-buffer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (r.status === 202 || r.ok) {
        statusEl.textContent = "✓ Buffer cleared on the unit.";
        statusEl.className = "diag-danger-status ok";
        _disarm();
        return;
      }
      let msg;
      if (r.status === 503) {
        msg = "Unit offline — try again when reconnected.";
      } else if (r.status === 403) {
        msg = "Forbidden — admin role required.";
      } else {
        const err = await r.json().catch(() => ({}));
        msg = err.error || r.statusText || "Clear buffer failed";
      }
      statusEl.textContent = `✗ ${msg}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Yes, clear it";
    } catch (exc) {
      statusEl.textContent = `✗ ${exc.message || "Network error"}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Yes, clear it";
    }
  }

  armBtn.addEventListener("click", _arm);
  cancelBtn.addEventListener("click", _disarm);
  confirmBtn.addEventListener("click", _fire);

  return panel;
}


/** Clear all photos action — DELETE with single OK/Cancel modal.
 *
 * Uses the same friction level as clear-buffer (single OK/Cancel, no
 * type-the-label) because the worst-case outcome is "lose your test
 * photos" — easily re-taken with a Snap-photo click. The decommission
 * action's type-to-confirm gate is reserved for things that can't be
 * undone from the UI at all.
 *
 * Status surface mirrors clear-buffer: 200 + {deleted_count: N} →
 * "✓ Deleted N photos."; 403 → admin-only; network/other errors →
 * inline message with .err styling. The arm button does NOT auto-hide
 * after success because the operator may want to wipe again later
 * (e.g. after re-testing) without a page refresh.
 */
function _renderClearPhotos(unit, doc, fetchFn) {
  const panel = doc.createElement("div");
  panel.className = "diag-danger-action";
  panel.dataset.testid = "clear-photos-action";

  const title = doc.createElement("h4");
  title.className = "diag-danger-title";
  title.textContent = "🗑 Clear all photos";
  panel.appendChild(title);

  const desc = doc.createElement("p");
  desc.className = "diag-danger-desc";
  desc.textContent =
    "Delete every photo for this unit. Both the DB rows and the JPEG " +
    "files on disk are removed. Useful for wiping the test-data slate " +
    "before the unit goes live with a real plant. Telemetry, watering " +
    "history, and unit configuration are NOT affected — only photos.";
  panel.appendChild(desc);

  const armRow = doc.createElement("div");
  armRow.className = "diag-danger-row";
  panel.appendChild(armRow);

  const armBtn = doc.createElement("button");
  armBtn.type = "button";
  armBtn.className = "px-btn danger diag-cp-arm";
  armBtn.textContent = "🗑 Clear all photos";
  armBtn.dataset.testid = "clear-photos-arm-btn";
  armRow.appendChild(armBtn);

  // Confirm pane
  const confirmPane = doc.createElement("div");
  confirmPane.className = "diag-danger-confirm";
  confirmPane.style.display = "none";
  confirmPane.dataset.testid = "clear-photos-confirm";

  const warn = doc.createElement("p");
  warn.className = "diag-danger-warn";
  warn.textContent =
    `This will permanently delete every photo for ${unit.label}. ` +
    `JPEG files on disk and DB rows will both be removed. Continue?`;
  confirmPane.appendChild(warn);

  const confirmRow = doc.createElement("div");
  confirmRow.className = "diag-danger-confirm-row";

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn danger diag-cp-confirm";
  confirmBtn.textContent = "Yes, delete all photos";
  confirmBtn.dataset.testid = "clear-photos-confirm-btn";
  confirmRow.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn diag-cp-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "clear-photos-cancel-btn";
  confirmRow.appendChild(cancelBtn);

  confirmPane.appendChild(confirmRow);
  panel.appendChild(confirmPane);

  const statusEl = doc.createElement("div");
  statusEl.className = "diag-danger-status";
  statusEl.dataset.testid = "clear-photos-status";
  panel.appendChild(statusEl);

  function _arm() {
    armBtn.style.display = "none";
    confirmPane.style.display = "";
    statusEl.textContent = "";
    statusEl.className = "diag-danger-status";
  }

  function _disarm() {
    armBtn.style.display = "";
    confirmPane.style.display = "none";
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
    confirmBtn.textContent = "Yes, delete all photos";
  }

  async function _fire() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Deleting…";
    try {
      const r = await fetchFn(`/api/grow/units/${unit.id}/photos`, {
        method: "DELETE",
      });
      if (r.ok) {
        const body = await r.json().catch(() => ({}));
        const n = body.deleted_count ?? 0;
        statusEl.textContent = n === 0
          ? "✓ No photos to delete."
          : `✓ Deleted ${n} photo${n === 1 ? "" : "s"}.`;
        statusEl.className = "diag-danger-status ok";
        _disarm();
        return;
      }
      let msg;
      if (r.status === 403) {
        msg = "Forbidden — admin role required.";
      } else if (r.status === 404) {
        msg = "Unit not found — it may have been decommissioned.";
      } else {
        const err = await r.json().catch(() => ({}));
        msg = err.error || r.statusText || "Clear photos failed";
      }
      statusEl.textContent = `✗ ${msg}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Yes, delete all photos";
    } catch (exc) {
      statusEl.textContent = `✗ ${exc.message || "Network error"}`;
      statusEl.className = "diag-danger-status err";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Yes, delete all photos";
    }
  }

  armBtn.addEventListener("click", _arm);
  cancelBtn.addEventListener("click", _disarm);
  confirmBtn.addEventListener("click", _fire);

  return panel;
}

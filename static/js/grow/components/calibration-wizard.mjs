/**
 * Calibration wizard — fourth panel of the Configure tab.
 *
 * Two-step capture flow for soil-moisture sensor calibration:
 *   step 1 (dry):  user puts the sensor in known-dry soil → click "I'm
 *                  dry now" → wizard reads `unit.last_known_state.
 *                  soil_moisture_raw` and stores it as dry_raw
 *   step 2 (wet):  user moves the sensor to known-wet (waterlogged)
 *                  soil → click "I'm wet now" → wizard reads the live
 *                  raw and stores it as wet_raw
 *   save:          PUT /api/grow/units/<id>/calibration with
 *                  {dry_raw, wet_raw}
 *
 * If the unit is already calibrated, render a summary line + a
 * Recalibrate button so the user has to deliberately enter the wizard.
 *
 * Live raw source: `unit.last_known_state.soil_moisture_raw`. Confirmed
 * present via mlss_monitor/grow/handlers.py LastKnownState TypedDict —
 * every telemetry write updates this cached blob, and the GET unit
 * endpoint includes `last_known_state` verbatim. Reading from the cached
 * state means the wizard doesn't need a new live-poll endpoint.
 *
 * Edge case: if the unit has never reported telemetry,
 * `unit.last_known_state` may be null. We render an inline error in
 * that case rather than capturing a phantom 0 reading.
 */


function _readLiveRaw(unit) {
  return unit.last_known_state?.soil_moisture_raw ?? null;
}


/**
 * Build the calibration wizard panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include
 *                       `calibration` and `last_known_state`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderCalibrationWizard(unit, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-calibration";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🧪 Soil calibration</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "cfg-form";
  wrap.appendChild(body);

  // Wizard state. dry_raw / wet_raw are number|null; mode is "summary"
  // (already calibrated) | "step1" (capture dry) | "step2" (capture wet)
  // | "review" (both captured, awaiting Save). Mutate via _setMode.
  const state = {
    mode: null,
    dry_raw: null,
    wet_raw: null,
  };

  function _renderForMode() {
    body.innerHTML = "";
    if (state.mode === "summary") {
      body.appendChild(_renderSummary(doc, unit, () => _setMode("step1")));
    } else {
      body.appendChild(_renderWizard(doc, unit, state, _setMode));
    }
  }

  function _setMode(next) {
    state.mode = next;
    if (next === "step1") {
      // Reset captures when entering a fresh wizard run
      state.dry_raw = null;
      state.wet_raw = null;
    }
    _renderForMode();
  }

  // Initial mode: summary if unit already calibrated, else step1
  const cal = unit.calibration || {};
  if (cal.dry_raw != null && cal.wet_raw != null) {
    state.mode = "summary";
  } else {
    state.mode = "step1";
  }
  _renderForMode();

  return wrap;
}


function _renderSummary(doc, unit, onRecalibrate) {
  const block = doc.createElement("div");
  block.className = "cfg-cal-summary";

  const line = doc.createElement("div");
  line.className = "cfg-cal-current";
  line.dataset.testid = "cal-existing";
  const cal = unit.calibration || {};
  line.textContent =
    `Currently calibrated: dry=${cal.dry_raw} · wet=${cal.wet_raw}`;
  block.appendChild(line);

  const recal = doc.createElement("button");
  recal.type = "button";
  recal.className = "cfg-save px-btn";
  recal.textContent = "Recalibrate";
  recal.dataset.testid = "cal-recalibrate";
  recal.addEventListener("click", onRecalibrate);
  block.appendChild(recal);

  return block;
}


function _renderWizard(doc, unit, state, setMode) {
  const block = doc.createElement("div");
  block.className = "cfg-cal-wizard";

  // Step indicator
  const indicator = doc.createElement("div");
  indicator.className = "cfg-cal-step";
  indicator.dataset.testid = "cal-step-indicator";
  if (state.mode === "step1") {
    indicator.textContent = "Step 1: place sensor in dry soil, then capture";
  } else if (state.mode === "step2") {
    indicator.textContent = "Step 2: place sensor in wet soil, then capture";
  } else {
    indicator.textContent = "Review captured values, then save";
  }
  block.appendChild(indicator);

  // Captured-values panel — visible once dry_raw is set
  const captured = doc.createElement("div");
  captured.className = "cfg-cal-captured";
  if (state.dry_raw != null) {
    const dry = doc.createElement("div");
    dry.dataset.testid = "cal-dry-value";
    dry.textContent = `Dry raw: ${state.dry_raw}`;
    captured.appendChild(dry);
  }
  if (state.wet_raw != null) {
    const wet = doc.createElement("div");
    wet.dataset.testid = "cal-wet-value";
    wet.textContent = `Wet raw: ${state.wet_raw}`;
    captured.appendChild(wet);
  }
  block.appendChild(captured);

  const status = doc.createElement("div");
  status.className = "cfg-status";
  status.dataset.testid = "cal-status";
  block.appendChild(status);

  // Capture buttons depending on step
  const actions = doc.createElement("div");
  actions.className = "cfg-row cfg-cal-actions";

  if (state.mode === "step1") {
    const dryBtn = doc.createElement("button");
    dryBtn.type = "button";
    dryBtn.className = "cfg-save px-btn";
    dryBtn.textContent = "I'm dry now";
    dryBtn.dataset.testid = "cal-capture-dry";
    dryBtn.addEventListener("click", () => {
      const raw = _readLiveRaw(unit);
      if (raw == null) {
        status.textContent = "✗ No live reading yet — wait for first telemetry";
        status.className = "cfg-status err";
        return;
      }
      state.dry_raw = raw;
      state.mode = "step2";
      // Re-render the parent block. Simplest: rebuild via setMode. We use
      // the explicit "step2" string so dry capture state is preserved.
      setMode("step2");
    });
    actions.appendChild(dryBtn);
  } else if (state.mode === "step2") {
    const wetBtn = doc.createElement("button");
    wetBtn.type = "button";
    wetBtn.className = "cfg-save px-btn";
    wetBtn.textContent = "I'm wet now";
    wetBtn.dataset.testid = "cal-capture-wet";
    wetBtn.addEventListener("click", () => {
      const raw = _readLiveRaw(unit);
      if (raw == null) {
        status.textContent = "✗ No live reading yet — wait for first telemetry";
        status.className = "cfg-status err";
        return;
      }
      state.wet_raw = raw;
      state.mode = "review";
      setMode("review");
    });
    actions.appendChild(wetBtn);
  }

  // Save button — only enabled in review mode (after both captures).
  // Design-critique #14: previously the button looked like a normal
  // primary action even before prerequisites were met, making
  // operators wonder if they could click it. Now we add an explicit
  // tooltip + cursor:not-allowed (via CSS) and keep the button visibly
  // separate from the capture buttons. Only when both raw values are
  // present + state.mode is "review" does it become clickable.
  const saveBtn = doc.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "cfg-save px-btn";
  saveBtn.textContent = "Save";
  saveBtn.dataset.testid = "cal-save";
  const ready = (state.dry_raw != null && state.wet_raw != null);
  saveBtn.disabled = !ready;
  if (!ready) {
    saveBtn.title = state.dry_raw == null
      ? "Capture dry-soil reading first (Step 1)"
      : "Capture wet-soil reading first (Step 2)";
  } else {
    saveBtn.title = "Save calibration to the unit";
  }
  saveBtn.addEventListener("click", async () => {
    if (state.dry_raw == null || state.wet_raw == null) return;
    if (state.dry_raw >= state.wet_raw) {
      status.textContent =
        "✗ dry_raw must be < wet_raw (sensor inverted? recapture wet step)";
      status.className = "cfg-status err";
      return;
    }
    saveBtn.disabled = true;
    saveBtn.classList.add("is-saving");
    saveBtn.textContent = "Saving…";
    status.textContent = "";
    status.className = "cfg-status";
    try {
      const r = await fetch(`/api/grow/units/${unit.id}/calibration`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dry_raw: state.dry_raw,
          wet_raw: state.wet_raw,
        }),
      });
      if (r.ok) {
        status.textContent = "✓ Saved";
        status.className = "cfg-status ok";
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
      saveBtn.classList.remove("is-saving");
      saveBtn.textContent = "Save";
    }
  });
  actions.appendChild(saveBtn);

  block.appendChild(actions);
  return block;
}

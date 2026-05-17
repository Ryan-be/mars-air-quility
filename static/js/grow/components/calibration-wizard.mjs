/**
 * Calibration wizard — fourth panel of the Configure tab.
 *
 * Two-step capture flow for soil-moisture sensor calibration:
 *   step 1 (dry):  user puts the sensor in known-dry soil → click "I'm
 *                  dry now" → wizard captures the live raw value and
 *                  stores it as dry_raw
 *   step 2 (wet):  user moves the sensor to known-wet (waterlogged)
 *                  soil → click "I'm wet now" → wizard captures the
 *                  live raw and stores it as wet_raw
 *   save:          PUT /api/grow/units/<id>/calibration with
 *                  {dry_raw, wet_raw}
 *
 * If the unit is already calibrated, render a summary line + a
 * Recalibrate button so the user has to deliberately enter the wizard.
 *
 * ── LIVE POLLING (added after the closure-stale bug) ──
 *
 * The original implementation captured the `unit` object passed in at
 * page load and read `unit.last_known_state?.soil_moisture_raw` on
 * every capture click. Because the closure never re-fetched, both
 * "I'm dry now" and "I'm wet now" captured the SAME stale snapshot
 * value taken at page-load time.
 *
 * Smoking-gun reproduction: user opened the page when sensor was dry
 * (raw=321), placed sensor in dry soil and clicked "I'm dry now"
 * (captured 321 — correct), watered the soil (real raw rose to ~800),
 * then clicked "I'm wet now" and the wizard captured 321 AGAIN.
 * Result: dry_raw=321, wet_raw=321 — useless calibration.
 *
 * Fix: in step1 / step2 modes, poll GET /api/grow/units/<id> every 5s
 * and write `last_known_state.soil_moisture_raw` into a mutable
 * `livestate` object. Capture clicks read from `livestate`, NOT from
 * the closure-captured `unit`. A live display above the capture
 * button shows the current raw + age so the user sees what's about
 * to be captured. Polling stops on transition to review/summary
 * modes and on detach (document.contains check before each poll).
 *
 * ── MANUAL INPUT ESCAPE HATCH ──
 *
 * Beneath each capture button is a small "Set manually" form so the
 * user can type a value directly. Useful when (a) the sensor is
 * offline so polling can't fetch a value, (b) the user already
 * knows the correct raw value from a prior calibration, or (c) the
 * polling delay (up to 5s) is too long to wait. Range-validated to
 * 0..2000 (Seesaw capacitive moisture raw range — see RAW_MAX below).
 *
 * Edge case: if no telemetry exists yet when the user clicks capture,
 * we render an inline "wait for telemetry" error rather than
 * capturing a phantom 0.
 */


// Seesaw capacitive moisture sensor raw range — used to validate
// manual-input values. Out-of-range values are almost certainly
// finger errors (typo "5000" instead of "500", etc.).
//
// Hardware truth: grow_unit/src/mlss_grow/sensors/seesaw.py defines
// SANE_RAW_MIN=200, SANE_RAW_MAX=2000 — that's the working range
// the driver itself enforces. We accept 0..2000 here (slightly
// broader at the low end) so that a sensor reporting 0 (cable
// unplugged?) doesn't get blocked from being recorded as a
// deliberate "dry" calibration anchor.
const RAW_MIN = 0;
const RAW_MAX = 2000;

// Live-polling cadence. 5s is the same heartbeat the dashboard uses
// for its own fleet refresh, so this won't add meaningful new load.
const POLL_INTERVAL_MS = 5000;


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

  // Live-polled telemetry. The polling loop writes here; capture
  // click handlers read from here. Initialized from the page-load
  // unit so the very first capture click (before the first poll
  // completes) still has SOMETHING — though the first poll fires
  // immediately on mount, so this is belt-and-braces.
  const livestate = {
    soil_moisture_raw: unit.last_known_state?.soil_moisture_raw ?? null,
    // Wall-clock instant when this reading was last refreshed. Used
    // by the live display to render "Updated 3s ago".
    updated_at: Date.now(),
  };

  // setInterval id for the polling loop. Stored at wizard scope so
  // _setMode and the attach-check can both clear it. We start it
  // lazily inside _renderForMode so it only runs when actually needed
  // (step1 / step2 — summary and review modes don't need live data).
  let pollTimer = null;

  function _stopPolling() {
    if (pollTimer != null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function _pollOnce() {
    // Bail if the component has been removed from the DOM. Cheaper
    // than wiring a MutationObserver; the cost is one document.contains
    // call per poll, which is O(depth).
    if (!doc.contains(wrap)) {
      _stopPolling();
      return;
    }
    // Bail if the mode changed while a previous poll was in flight
    // (race-condition guard). We compare against the mode at the
    // moment we kick off the fetch — if it's different by the time
    // the response arrives, the poll is stale and we shouldn't
    // mutate livestate or the UI based on it.
    const modeAtStart = state.mode;
    if (modeAtStart !== "step1" && modeAtStart !== "step2") {
      _stopPolling();
      return;
    }
    try {
      const r = await fetch(`/api/grow/units/${unit.id}`);
      if (!r.ok) return;
      const fresh = await r.json();
      // Mode-changed-during-fetch guard: ignore the response if the
      // user has already advanced past the capture step.
      if (state.mode !== modeAtStart) return;
      if (!doc.contains(wrap)) {
        _stopPolling();
        return;
      }
      const raw = fresh?.last_known_state?.soil_moisture_raw ?? null;
      if (raw != null) {
        livestate.soil_moisture_raw = raw;
        livestate.updated_at = Date.now();
        _refreshLiveDisplay();
      }
    } catch (_) {
      // Network errors are silent — the live display will simply
      // show a stale age, which is the correct visual cue that
      // something's wrong. The user can still click capture or use
      // the manual-input escape hatch.
    }
  }

  function _startPolling() {
    _stopPolling();
    // Fire one poll immediately so the live display doesn't sit
    // blank for the first 5 seconds after the user opens the wizard.
    // BUT defer it via a microtask: at the moment _startPolling is
    // first called during renderCalibrationWizard(), the wrap hasn't
    // been attached to a parent yet (the caller does that AFTER
    // renderCalibrationWizard returns). _pollOnce's document.contains
    // check would see an unattached wrap and bail. The microtask
    // defer gives the caller time to attach before the first poll.
    Promise.resolve().then(_pollOnce);
    pollTimer = setInterval(_pollOnce, POLL_INTERVAL_MS);
  }

  // The live-display nodes are looked up on every _refreshLiveDisplay
  // call rather than cached, because _renderForMode rebuilds the body
  // (and therefore swaps these nodes) on every mode transition. The
  // cost is a couple of querySelector calls per 5s tick — negligible.
  function _refreshLiveDisplay() {
    const rawEl = wrap.querySelector("[data-testid='cal-live-raw']");
    const ageEl = wrap.querySelector("[data-testid='cal-live-age']");
    if (rawEl) {
      rawEl.textContent = livestate.soil_moisture_raw == null
        ? "—"
        : String(livestate.soil_moisture_raw);
    }
    if (ageEl) {
      const ageMs = Date.now() - livestate.updated_at;
      ageEl.textContent = _formatAge(ageMs);
    }
  }

  function _renderForMode() {
    body.innerHTML = "";
    if (state.mode === "summary") {
      _stopPolling();
      body.appendChild(_renderSummary(doc, unit, () => _setMode("step1")));
    } else if (state.mode === "review") {
      // Review mode: both captures done, just show the captured
      // values + Save button. No more live polling needed.
      _stopPolling();
      body.appendChild(_renderWizard(doc, unit, state, _setMode, livestate));
    } else {
      // step1 / step2 — poll for live values
      body.appendChild(_renderWizard(doc, unit, state, _setMode, livestate));
      _startPolling();
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


/** Format the age of the live reading. "just now" for very-recent
 *  readings, "Ns ago" / "Nm ago" otherwise. Used in cal-live-age. */
function _formatAge(ms) {
  if (ms == null || ms < 0) return "just now";
  if (ms < 1500) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  return `${m}m ago`;
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


function _renderWizard(doc, unit, state, setMode, livestate) {
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

  // Live-reading display — visible in step1 / step2 only. Sits ABOVE
  // the capture button so the user sees what's about to be captured
  // (the fix for the closure-stale bug). In review mode we skip it
  // because both values are already captured and the live reading
  // would only be confusing.
  if (state.mode === "step1" || state.mode === "step2") {
    const liveWrap = doc.createElement("div");
    liveWrap.className = "cfg-cal-live";

    const liveLabel = doc.createElement("span");
    liveLabel.className = "cfg-cal-live-label";
    liveLabel.textContent = "Live raw: ";
    liveWrap.appendChild(liveLabel);

    const liveRaw = doc.createElement("span");
    liveRaw.className = "cfg-cal-live-raw";
    liveRaw.dataset.testid = "cal-live-raw";
    liveRaw.textContent = livestate.soil_moisture_raw == null
      ? "—"
      : String(livestate.soil_moisture_raw);
    liveWrap.appendChild(liveRaw);

    const liveAge = doc.createElement("span");
    liveAge.className = "cfg-cal-live-age";
    liveAge.dataset.testid = "cal-live-age";
    liveAge.textContent = _formatAge(Date.now() - livestate.updated_at);
    liveWrap.appendChild(liveAge);

    block.appendChild(liveWrap);
  }

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

  /** Validate a typed manual-input string. Returns {ok, value, msg}.
   *  Integer only (Seesaw raw is an integer), in [RAW_MIN, RAW_MAX]. */
  function _parseManualRaw(text) {
    const trimmed = String(text ?? "").trim();
    if (trimmed === "") {
      return { ok: false, msg: `✗ Enter an integer between ${RAW_MIN} and ${RAW_MAX}` };
    }
    // Reject decimals, scientific notation, NaN, etc. We accept only
    // an optional minus + digits — and then range-check the result.
    if (!/^-?\d+$/.test(trimmed)) {
      return { ok: false, msg: "✗ Manual value must be a whole integer (no decimals)" };
    }
    const n = Number(trimmed);
    if (!Number.isInteger(n)) {
      return { ok: false, msg: "✗ Manual value must be a whole integer" };
    }
    if (n < RAW_MIN || n > RAW_MAX) {
      return { ok: false, msg: `✗ Out of range — must be between ${RAW_MIN} and ${RAW_MAX}` };
    }
    return { ok: true, value: n };
  }

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
      // Read from livestate — the polling loop writes the latest
      // value here. THIS is the fix for the closure-stale bug; the
      // previous version read unit.last_known_state, which was the
      // page-load snapshot and never refreshed.
      const raw = livestate.soil_moisture_raw;
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
      const raw = livestate.soil_moisture_raw;
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

  // Manual-input escape hatch — beneath the capture button. Placed
  // BELOW (not beside) so on narrow screens the controls don't squash
  // together, and so the visual hierarchy is "primary action → fallback
  // option" top-to-bottom. Step 1 gets the dry inputs; step 2 gets the
  // wet inputs; review mode skips them entirely (both values captured).
  if (state.mode === "step1" || state.mode === "step2") {
    const isDry = state.mode === "step1";
    const manualWrap = doc.createElement("div");
    manualWrap.className = "cfg-cal-manual";

    const manualLabel = doc.createElement("label");
    manualLabel.className = "cfg-cal-manual-label";
    manualLabel.textContent = isDry
      ? "Or set dry value manually: "
      : "Or set wet value manually: ";
    manualWrap.appendChild(manualLabel);

    const input = doc.createElement("input");
    input.type = "number";
    input.min = String(RAW_MIN);
    input.max = String(RAW_MAX);
    input.step = "1";
    input.placeholder = isDry ? "e.g. 320" : "e.g. 1450";
    input.className = "cfg-cal-manual-input";
    input.dataset.testid = isDry ? "cal-manual-dry-input" : "cal-manual-wet-input";
    manualWrap.appendChild(input);

    const setBtn = doc.createElement("button");
    setBtn.type = "button";
    setBtn.className = "cfg-cal-manual-set px-btn";
    setBtn.textContent = "Set manually";
    setBtn.dataset.testid = isDry ? "cal-manual-dry-set" : "cal-manual-wet-set";
    setBtn.addEventListener("click", () => {
      const parsed = _parseManualRaw(input.value);
      if (!parsed.ok) {
        status.textContent = parsed.msg;
        status.className = "cfg-status err";
        return;
      }
      // Mirror the capture-button flow: write to state, advance mode.
      if (isDry) {
        state.dry_raw = parsed.value;
        state.mode = "step2";
        setMode("step2");
      } else {
        state.wet_raw = parsed.value;
        state.mode = "review";
        setMode("review");
      }
    });
    manualWrap.appendChild(setBtn);

    block.appendChild(manualWrap);
  }

  return block;
}

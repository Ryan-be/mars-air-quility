/**
 * Tests for the Calibration wizard — fourth Configure-tab panel
 * delivered in Task 7.
 *
 * The wizard reads `unit.last_known_state.soil_moisture_raw` to capture
 * dry/wet readings (verified to be present in handlers.py LastKnownState
 * TypedDict) and PUTs to /api/grow/units/<id>/calibration.
 *
 * Two-step flow: dry → wet → save. Already-calibrated units render a
 * "currently dry=X wet=Y · Recalibrate" affordance so users don't
 * accidentally start over.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderCalibrationWizard } from "../../static/js/grow/components/calibration-wizard.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit({ calibration = { dry_raw: null, wet_raw: null }, raw = 320 } = {}) {
  return {
    id: 7,
    label: "Tom 1",
    calibration,
    last_known_state: { soil_moisture_raw: raw },
  };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
function _flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }
/** Microtask flush. Use this in tests that enable t.mock.timers — the
 *  mocked setTimeout queues forever and stalls awaits on real timers. */
async function _flushMicro() {
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
}


test("calibration wizard: renders step 1 with capture-dry button when uncalibrated", () => {
  const el = renderCalibrationWizard(_unit({ raw: 320 }), { ownerDocument: document });
  const dryBtn = el.querySelector("[data-testid='cal-capture-dry']");
  assert.ok(dryBtn, "capture-dry button present");
  assert.match(dryBtn.textContent, /dry/i);
  // Step indicator visible
  const step = el.querySelector("[data-testid='cal-step-indicator']");
  assert.ok(step);
  assert.match(step.textContent, /step 1|dry/i);
});


test("calibration wizard: clicking 'I'm dry now' captures the current raw", () => {
  const el = renderCalibrationWizard(_unit({ raw: 320 }), { ownerDocument: document });
  const dryBtn = el.querySelector("[data-testid='cal-capture-dry']");
  dryBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  // After capture, dry_raw shown; advances to step 2
  const dryDisplay = el.querySelector("[data-testid='cal-dry-value']");
  assert.ok(dryDisplay);
  assert.match(dryDisplay.textContent, /320/);
  // Step 2 button now visible
  const wetBtn = el.querySelector("[data-testid='cal-capture-wet']");
  assert.ok(wetBtn, "capture-wet button now visible");
});


test("calibration wizard: clicking 'I'm wet now' captures wet raw and enables Save", () => {
  // Capture flow now reads from livestate (the polling-loop sink),
  // not from the closure-captured unit. To simulate the sensor
  // physically moving from dry → wet between clicks, we use the
  // manual-input escape hatch which writes the user-supplied value
  // directly into state — same end result, no polling needed.
  const u = _unit({ raw: 320 });
  const el = renderCalibrationWizard(u, { ownerDocument: document });
  el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
    new dom.window.Event("click", { bubbles: true, cancelable: true })
  );
  // Set the wet value manually — equivalent to the user waiting for
  // the live poll to refresh, but deterministic in tests.
  el.querySelector("[data-testid='cal-manual-wet-input']").value = "1450";
  el.querySelector("[data-testid='cal-manual-wet-set']").dispatchEvent(
    new dom.window.Event("click", { bubbles: true, cancelable: true })
  );
  const wetDisplay = el.querySelector("[data-testid='cal-wet-value']");
  assert.ok(wetDisplay);
  assert.match(wetDisplay.textContent, /1450/);
  const saveBtn = el.querySelector("[data-testid='cal-save']");
  assert.ok(saveBtn);
  assert.equal(saveBtn.disabled, false);
});


test("calibration wizard: Save PUTs dry_raw + wet_raw", async () => {
  // Use the manual-input path for the dry → wet transition (same
  // reason as the test above). Save still fires the PUT — the
  // capture path the user took shouldn't matter to the save logic.
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    // Intercept only the /calibration PUT — let other URLs (e.g.
    // the GET /api/grow/units/<id> polling fetch, in case any
    // leaks in before review mode shuts polling down) fall through
    // to a benign empty response.
    if (typeof url === "string" && url.includes("/calibration")) {
      captured = { url, opts };
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const u = _unit({ raw: 320 });
    const el = renderCalibrationWizard(u, { ownerDocument: document });
    el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    el.querySelector("[data-testid='cal-manual-wet-input']").value = "1450";
    el.querySelector("[data-testid='cal-manual-wet-set']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    el.querySelector("[data-testid='cal-save']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    await _flush();
    assert.ok(captured, "PUT to /calibration was captured");
    assert.equal(captured.url, "/api/grow/units/7/calibration");
    assert.equal(captured.opts.method, "PUT");
    const body = JSON.parse(captured.opts.body);
    assert.equal(body.dry_raw, 320);
    assert.equal(body.wet_raw, 1450);
  } finally {
    _setMockFetch(orig);
  }
});


test("calibration wizard: rejects wet < dry client-side (sensor inverted)", async () => {
  // Manual-input path again — guarantees the wet < dry condition
  // without depending on polling timing.
  const orig = _origFetch();
  let calledCalibration = false;
  _setMockFetch(async (url) => {
    if (typeof url === "string" && url.includes("/calibration")) {
      calledCalibration = true;
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const u = _unit({ raw: 1500 });
    const el = renderCalibrationWizard(u, { ownerDocument: document });
    // Capture dry first (1500 — high)
    el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    // Manually set wet to a LOWER raw (sensor inverted scenario)
    el.querySelector("[data-testid='cal-manual-wet-input']").value = "200";
    el.querySelector("[data-testid='cal-manual-wet-set']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    el.querySelector("[data-testid='cal-save']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    assert.equal(calledCalibration, false,
      "PUT to /calibration must not fire when wet < dry");
    const status = el.querySelector("[data-testid='cal-status']");
    assert.match(status.textContent, /dry.*<.*wet|inverted|order/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});


test("calibration wizard: shows existing calibration with Recalibrate", () => {
  const el = renderCalibrationWizard(
    _unit({ calibration: { dry_raw: 200, wet_raw: 1500 }, raw: 800 }),
    { ownerDocument: document }
  );
  const summary = el.querySelector("[data-testid='cal-existing']");
  assert.ok(summary, "existing-calibration summary visible");
  assert.match(summary.textContent, /200/);
  assert.match(summary.textContent, /1500/);
  const recal = el.querySelector("[data-testid='cal-recalibrate']");
  assert.ok(recal, "Recalibrate button visible");
  // Clicking Recalibrate enters step 1
  recal.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const dryBtn = el.querySelector("[data-testid='cal-capture-dry']");
  assert.ok(dryBtn, "capture-dry button visible after recalibrate click");
});


// ───────────────────────────────────────────────────────────────────
//  Stale-fix + live polling + manual-input tests
// ───────────────────────────────────────────────────────────────────
//
// Bug observed in the wild: user opened the page when sensor was dry
// (raw=321), clicked "I'm dry now" (captured 321 — correct), watered
// the soil so the live raw rose to ~800, then clicked "I'm wet now"
// and the wizard captured 321 AGAIN. dry_raw=321, wet_raw=321. The
// closure was holding the page-load snapshot of `unit` and never
// re-fetching. Fix: poll /api/grow/units/<id> every 5s and capture
// from the latest polled value.
//
// The manual-input escape hatch covers cases where polling can't
// catch up (sensor offline, user knows the value already, etc.).


/** Mount a wrap into a document body so document.contains() returns
 *  true — the polling loop uses this attached-check to know when to
 *  stop. Tests that mount without attaching will not poll. */
function _attach(el) {
  document.body.appendChild(el);
  return el;
}

function _detach(el) {
  if (el.parentNode) el.parentNode.removeChild(el);
}


test("calibration wizard: capture uses LATEST polled raw, not stale page-load value", async (t) => {
  // The smoking-gun reproduction. Page loads with raw=321. User
  // physically wets the sensor. The next /api/grow/units/<id> poll
  // returns raw=805. Click "I'm dry now" — wizard MUST capture 805,
  // not the closure-stale 321.
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (typeof url === "string" && url.includes("/api/grow/units/7")
        && !url.includes("/calibration")) {
      return new Response(JSON.stringify({
        id: 7,
        label: "Tom 1",
        calibration: { dry_raw: null, wet_raw: null },
        last_known_state: { soil_moisture_raw: 805 },
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  try {
    const el = _attach(renderCalibrationWizard(
      _unit({ raw: 321 }), { ownerDocument: document }
    ));
    // First poll fires immediately on mount; let it resolve.
    await _flushMicro();
    el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    const dryDisplay = el.querySelector("[data-testid='cal-dry-value']");
    assert.ok(dryDisplay);
    assert.match(dryDisplay.textContent, /805/,
      "captured value must be the freshly-polled 805, not the stale 321");
    assert.doesNotMatch(dryDisplay.textContent, /321/);
    _detach(el);
  } finally {
    _setMockFetch(orig);
  }
});


test("calibration wizard: live display renders with initial unit value", () => {
  const el = _attach(renderCalibrationWizard(
    _unit({ raw: 444 }), { ownerDocument: document }
  ));
  const live = el.querySelector("[data-testid='cal-live-raw']");
  assert.ok(live, "live-raw display present");
  assert.match(live.textContent, /444/, "initial raw rendered from page-load unit");
  const age = el.querySelector("[data-testid='cal-live-age']");
  assert.ok(age, "live-age display present");
  // "just now" or "0s ago" — accept either phrasing
  assert.match(age.textContent, /just now|0\s*s/i,
    "age starts at near-zero on mount");
  _detach(el);
});


test("calibration wizard: manual dry input sets dry_raw and advances to step 2", () => {
  const el = _attach(renderCalibrationWizard(
    _unit({ raw: 320 }), { ownerDocument: document }
  ));
  const input = el.querySelector("[data-testid='cal-manual-dry-input']");
  const setBtn = el.querySelector("[data-testid='cal-manual-dry-set']");
  assert.ok(input, "manual dry input present");
  assert.ok(setBtn, "manual dry Set button present");
  input.value = "500";
  setBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const dryDisplay = el.querySelector("[data-testid='cal-dry-value']");
  assert.ok(dryDisplay, "dry value displayed after manual set");
  assert.match(dryDisplay.textContent, /500/);
  // Advanced to step 2: capture-wet button is now present
  const wetBtn = el.querySelector("[data-testid='cal-capture-wet']");
  assert.ok(wetBtn, "advanced to step 2 (capture-wet button now visible)");
  _detach(el);
});


test("calibration wizard: manual wet input from step 2 sets wet_raw and advances to review", () => {
  const el = _attach(renderCalibrationWizard(
    _unit({ raw: 320 }), { ownerDocument: document }
  ));
  // First get into step 2 by manually setting dry
  el.querySelector("[data-testid='cal-manual-dry-input']").value = "300";
  el.querySelector("[data-testid='cal-manual-dry-set']").dispatchEvent(
    new dom.window.Event("click", { bubbles: true, cancelable: true })
  );
  // Now in step 2 — use manual wet input
  const wetInput = el.querySelector("[data-testid='cal-manual-wet-input']");
  const wetSet = el.querySelector("[data-testid='cal-manual-wet-set']");
  assert.ok(wetInput);
  assert.ok(wetSet);
  wetInput.value = "850";
  wetSet.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const wetDisplay = el.querySelector("[data-testid='cal-wet-value']");
  assert.ok(wetDisplay);
  assert.match(wetDisplay.textContent, /850/);
  // Advanced to review: Save button enabled, capture buttons gone
  const saveBtn = el.querySelector("[data-testid='cal-save']");
  assert.ok(saveBtn);
  assert.equal(saveBtn.disabled, false, "Save enabled in review mode");
  assert.equal(el.querySelector("[data-testid='cal-capture-wet']"), null,
    "no wet-capture button in review mode");
  _detach(el);
});


test("calibration wizard: manual input rejects out-of-range value", () => {
  // Seesaw raw range is 0..2000 (see sensors/seesaw.py SANE_RAW_MAX).
  // Anything above that is almost certainly a typo (extra zero etc.)
  // and should be rejected with a status message rather than silently
  // captured.
  const el = _attach(renderCalibrationWizard(
    _unit({ raw: 320 }), { ownerDocument: document }
  ));
  const input = el.querySelector("[data-testid='cal-manual-dry-input']");
  const setBtn = el.querySelector("[data-testid='cal-manual-dry-set']");
  input.value = "5000";  // above Seesaw max (2000) — clear typo
  setBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const status = el.querySelector("[data-testid='cal-status']");
  assert.match(status.textContent, /range|0.*2000|invalid/i,
    "out-of-range value rejected with a status message");
  assert.match(status.className, /err/);
  // Did NOT advance to step 2 — dry-capture button still present
  assert.ok(el.querySelector("[data-testid='cal-capture-dry']"),
    "still in step 1 after rejection");
  // Did NOT show a dry-value display
  assert.equal(el.querySelector("[data-testid='cal-dry-value']"), null,
    "no dry value captured");
  _detach(el);
});


test("calibration wizard: manual input rejects non-integer", () => {
  const el = _attach(renderCalibrationWizard(
    _unit({ raw: 320 }), { ownerDocument: document }
  ));
  const input = el.querySelector("[data-testid='cal-manual-dry-input']");
  const setBtn = el.querySelector("[data-testid='cal-manual-dry-set']");
  input.value = "12.5";  // not an integer
  setBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const status = el.querySelector("[data-testid='cal-status']");
  assert.match(status.textContent, /integer|invalid|whole/i,
    "non-integer value rejected with a status message");
  assert.match(status.className, /err/);
  assert.equal(el.querySelector("[data-testid='cal-dry-value']"), null);
  _detach(el);
});


test("calibration wizard: polling stops once review mode is reached", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  let pollCount = 0;
  _setMockFetch(async (url) => {
    if (typeof url === "string" && url.includes("/api/grow/units/7")
        && !url.includes("/calibration")) {
      pollCount += 1;
      return new Response(JSON.stringify({
        id: 7,
        label: "Tom 1",
        calibration: { dry_raw: null, wet_raw: null },
        last_known_state: { soil_moisture_raw: 320 },
      }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  });
  try {
    const el = _attach(renderCalibrationWizard(
      _unit({ raw: 320 }), { ownerDocument: document }
    ));
    // First poll fires immediately on mount
    await _flushMicro();
    const initialCount = pollCount;
    assert.ok(initialCount >= 1, "first poll fired immediately on mount");
    // Walk to review via manual-set (avoids needing more polled values)
    el.querySelector("[data-testid='cal-manual-dry-input']").value = "300";
    el.querySelector("[data-testid='cal-manual-dry-set']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flushMicro();
    el.querySelector("[data-testid='cal-manual-wet-input']").value = "800";
    el.querySelector("[data-testid='cal-manual-wet-set']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flushMicro();
    // We're in review mode now — capture the count baseline
    const countAtReview = pollCount;
    // Advance fake timers by 30s (6× the 5s poll interval). If polling
    // didn't stop, we'd see ~6 more fetches.
    t.mock.timers.tick(30000);
    await _flushMicro();
    assert.equal(pollCount, countAtReview,
      "no polls fired after entering review mode");
    _detach(el);
  } finally {
    _setMockFetch(orig);
  }
});

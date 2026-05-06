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
  const u = _unit({ raw: 320 });
  const el = renderCalibrationWizard(u, { ownerDocument: document });
  el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
    new dom.window.Event("click", { bubbles: true, cancelable: true })
  );
  // Now simulate the live raw rising as the sensor moves to wet soil
  u.last_known_state.soil_moisture_raw = 1450;
  el.querySelector("[data-testid='cal-capture-wet']").dispatchEvent(
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
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const u = _unit({ raw: 320 });
    const el = renderCalibrationWizard(u, { ownerDocument: document });
    el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    u.last_known_state.soil_moisture_raw = 1450;
    el.querySelector("[data-testid='cal-capture-wet']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    el.querySelector("[data-testid='cal-save']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    await _flush();
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
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => {
    called = true;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const u = _unit({ raw: 1500 });
    const el = renderCalibrationWizard(u, { ownerDocument: document });
    // Capture dry first (1500)
    el.querySelector("[data-testid='cal-capture-dry']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    // Now capture wet at a LOWER raw value (sensor inverted)
    u.last_known_state.soil_moisture_raw = 200;
    el.querySelector("[data-testid='cal-capture-wet']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    el.querySelector("[data-testid='cal-save']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    assert.equal(called, false, "fetch must not fire when wet < dry");
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

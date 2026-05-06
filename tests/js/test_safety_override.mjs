/**
 * Tests for the Safety override panel — fifth Configure-tab panel
 * delivered in Task 7.
 *
 * The 3-clicks-in-5s confirmation FSM is the high-friction core. State
 * transitions:
 *   idle → click 1 → "Confirm 1/3" + 5s timer
 *                  → click 2 → "Confirm 2/3"
 *                            → click 3 → POST + flash result
 *                  → 5s elapsed without 3 clicks → reset to idle
 *
 * Server side maps to SafetyOverrideRequest (admin-only). Body:
 * {action, duration_s, acknowledged_warnings}.
 *
 * Uses node:test's t.mock.timers for deterministic timer control.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderSafetyOverride } from "../../static/js/grow/components/safety-override.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit() {
  return { id: 7, label: "Tom 1" };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
/** Microtask flush. Avoid setTimeout-based flush in tests that enable
 *  t.mock.timers — the mocked setTimeout queues forever and stalls await. */
async function _flushMicro() {
  // Several await-Promise.resolve cycles to settle: the fetch mock's
  // `async () => Response(...)`, the await chain inside the panel's
  // _fire() handler, and the new `await r.json()` in the 503 branch.
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
}


test("safety override: renders action picker with all five actions", () => {
  const el = renderSafetyOverride(_unit(), { ownerDocument: document });
  const sel = el.querySelector("[data-testid='safety-action']");
  assert.ok(sel);
  const opts = Array.from(sel.querySelectorAll("option")).map((o) => o.value);
  for (const a of [
    "force_pump_on",
    "force_pump_off",
    "force_light_on",
    "force_light_off",
    "skip_next_soak",
  ]) {
    assert.ok(opts.includes(a), `option ${a} present`);
  }
  const dur = el.querySelector("[data-testid='safety-duration']");
  assert.ok(dur);
  assert.equal(dur.type, "number");
});


test("safety override: single click changes button text to 'Confirm 1/3'", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const el = renderSafetyOverride(_unit(), { ownerDocument: document });
  const btn = el.querySelector("[data-testid='safety-button']");
  const initial = btn.textContent;
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  assert.match(btn.textContent, /1\/3/);
  assert.notEqual(btn.textContent, initial);
  // Cleanup: tick past the timer so it doesn't leak
  t.mock.timers.tick(5500);
});


test("safety override: three clicks within 5s POST the override", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 202 });
  });
  try {
    const el = renderSafetyOverride(_unit(), { ownerDocument: document });
    const btn = el.querySelector("[data-testid='safety-button']");
    const sel = el.querySelector("[data-testid='safety-action']");
    const dur = el.querySelector("[data-testid='safety-duration']");
    sel.value = "force_pump_on";
    dur.value = "8";
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    // Allow the fetch promise to resolve
    await _flushMicro();
    assert.ok(captured, "fetch was called");
    assert.equal(captured.url, "/api/grow/units/7/safety_override");
    assert.equal(captured.opts.method, "POST");
    const body = JSON.parse(captured.opts.body);
    assert.equal(body.action, "force_pump_on");
    assert.equal(body.duration_s, 8);
    assert.ok(Array.isArray(body.acknowledged_warnings));
    assert.ok(body.acknowledged_warnings.length > 0,
      "acknowledged_warnings includes at least one warning code");
  } finally {
    _setMockFetch(orig);
  }
});


test("safety override: resets to idle after 5s timeout from first click", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const el = renderSafetyOverride(_unit(), { ownerDocument: document });
  const btn = el.querySelector("[data-testid='safety-button']");
  const initial = btn.textContent;
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  assert.match(btn.textContent, /1\/3/);
  // Advance past 5s
  t.mock.timers.tick(5100);
  assert.equal(btn.textContent, initial,
    "button reverts to its initial label after timeout");
});


test("safety override: timeout-then-fresh-click sequence still works", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 202 });
  });
  try {
    const el = renderSafetyOverride(_unit(), { ownerDocument: document });
    const btn = el.querySelector("[data-testid='safety-button']");
    // First two clicks
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    // Wait > 5s; FSM resets
    t.mock.timers.tick(5500);
    // Three fresh clicks should fire the POST
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    assert.ok(captured, "POST fires on the second cycle's three clicks");
  } finally {
    _setMockFetch(orig);
  }
});


test("safety override: handles 503 unit-offline gracefully", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "unit_not_connected" }),
    { status: 503 },
  ));
  try {
    const el = renderSafetyOverride(_unit(), { ownerDocument: document });
    const btn = el.querySelector("[data-testid='safety-button']");
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    const status = el.querySelector("[data-testid='safety-status']");
    assert.match(status.textContent, /offline|connect/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});


test("test_safety_override_distinguishes_listener_not_running_from_unit_offline", async (t) => {
  // 503 with body {error: "ws_listener_not_running"} indicates the server's
  // WS listener is down, not the unit. The UI must not blame the unit.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "ws_listener_not_running" }),
    { status: 503 },
  ));
  try {
    const el = renderSafetyOverride(_unit(), { ownerDocument: document });
    const btn = el.querySelector("[data-testid='safety-button']");
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    const status = el.querySelector("[data-testid='safety-status']");
    assert.match(status.textContent, /Server WS listener offline/);
    assert.doesNotMatch(status.textContent, /Unit offline/);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});

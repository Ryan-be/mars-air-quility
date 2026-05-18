/**
 * Tests for the Holiday-mode toggle — Settings → Grow.
 *
 * Behaviour:
 *   - Mount fires GET /api/grow/settings/holiday-mode and renders state
 *   - Click toggle → confirm group with warn message + Confirm/Cancel
 *   - Cancel → returns to display state without firing PUT
 *   - Confirm → fires PUT and updates the displayed state from the response
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderHolidayModeToggle } from
  "../../static/js/grow/components/holiday-mode-toggle.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


function _mockFetch(handlers) {
  return async (url, opts) => {
    const method = (opts && opts.method) || "GET";
    const key = `${method} ${url.split("?")[0]}`;
    const handler = handlers[key];
    if (!handler) throw new Error(`No mock handler for ${key}`);
    return handler(url, opts);
  };
}


test("holiday toggle: GET on mount displays current state", async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/settings/holiday-mode": async () =>
      new Response(JSON.stringify({ enabled: true }), { status: 200 }),
  });
  const el = renderHolidayModeToggle({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  const stateLbl = el.querySelector("[data-testid='hm-state']");
  assert.match(stateLbl.textContent, /on/i);
  const btn = el.querySelector("[data-testid='hm-toggle-btn']");
  assert.match(btn.textContent.toLowerCase(), /turn holiday mode off/);
  assert.equal(btn.disabled, false);
});


test("holiday toggle: clicking the toggle reveals confirm group, no PUT yet",
async () => {
  let putCalled = false;
  const fetchFn = _mockFetch({
    "GET /api/grow/settings/holiday-mode": async () =>
      new Response(JSON.stringify({ enabled: false }), { status: 200 }),
    "PUT /api/grow/settings/holiday-mode": async () => {
      putCalled = true;
      return new Response("{}", { status: 200 });
    },
  });
  const el = renderHolidayModeToggle({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  // Click the toggle
  el.querySelector("[data-testid='hm-toggle-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  // Confirm group visible, warn message contains relevant context
  const confirmGroup = el.querySelector("[data-testid='hm-confirm-group']");
  assert.notEqual(confirmGroup.style.display, "none");
  const warn = el.querySelector("[data-testid='hm-warn']");
  assert.match(warn.textContent.toLowerCase(),
    /pause watering|every unit/);
  assert.equal(putCalled, false, "PUT must not fire on toggle click alone");
});


test("holiday toggle: cancel returns to idle without changing state",
async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/settings/holiday-mode": async () =>
      new Response(JSON.stringify({ enabled: false }), { status: 200 }),
    "PUT /api/grow/settings/holiday-mode": async () => {
      throw new Error("PUT must not fire on cancel");
    },
  });
  const el = renderHolidayModeToggle({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  el.querySelector("[data-testid='hm-toggle-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='hm-cancel-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const confirmGroup = el.querySelector("[data-testid='hm-confirm-group']");
  assert.equal(confirmGroup.style.display, "none");
  const stateLbl = el.querySelector("[data-testid='hm-state']");
  // Still OFF
  assert.match(stateLbl.textContent, /off/i);
});


test("holiday toggle: confirm fires PUT and refreshes display from response",
async () => {
  let putCaptured = null;
  const fetchFn = _mockFetch({
    "GET /api/grow/settings/holiday-mode": async () =>
      new Response(JSON.stringify({ enabled: false }), { status: 200 }),
    "PUT /api/grow/settings/holiday-mode": async (url, opts) => {
      putCaptured = { url, opts };
      return new Response(
        JSON.stringify({ ok: true, enabled: true }),
        { status: 200 },
      );
    },
  });
  const el = renderHolidayModeToggle({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  el.querySelector("[data-testid='hm-toggle-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='hm-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.ok(putCaptured, "PUT was called");
  assert.equal(putCaptured.url, "/api/grow/settings/holiday-mode");
  assert.equal(putCaptured.opts.method, "PUT");
  assert.deepEqual(JSON.parse(putCaptured.opts.body), { enabled: true });
  const stateLbl = el.querySelector("[data-testid='hm-state']");
  assert.match(stateLbl.textContent, /on/i);
  // Confirm group hidden
  const confirmGroup = el.querySelector("[data-testid='hm-confirm-group']");
  assert.equal(confirmGroup.style.display, "none");
});

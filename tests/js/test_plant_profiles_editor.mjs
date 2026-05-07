/**
 * Tests for the Plant profiles editor — Settings → Grow.
 *
 * Behaviour covered:
 *   - GET /api/grow/plant-profiles is fetched on mount
 *   - One row rendered per profile, with shipped pill where applicable
 *   - Click row → editor expands; click again → editor collapses
 *   - Edit a numeric field → Save sends a dirty-subset PUT
 *   - PUT validation errors surface inline (don't crash the form)
 *   - Modified-from-default badge shows once a shipped row has been edited
 *     (server response embeds the breadcrumb in `notes`)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderPlantProfilesEditor } from
  "../../static/js/grow/components/plant-profiles-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


function _seed() {
  return [
    {
      id: 1, plant_type: "tomato", phase: "vegetative",
      target_moisture_pct: 55, deadband_pct: 5, kp: 0.4, ki: 0, kd: 0,
      min_pulse_s: 2, max_pulse_s: 8, soak_window_min: 30,
      default_light_hours: 16, is_shipped: 1, notes: null,
    },
    {
      id: 2, plant_type: "basil", phase: "vegetative",
      target_moisture_pct: 60, deadband_pct: 5, kp: 0.4, ki: 0, kd: 0,
      min_pulse_s: 2, max_pulse_s: 6, soak_window_min: 30,
      default_light_hours: 14, is_shipped: 1, notes: "[modified] tweaked",
    },
  ];
}


function _mockFetch(handlers) {
  // handlers: { "GET /api/grow/plant-profiles": async () => Response, "PUT ...": ... }
  return async (url, opts) => {
    const method = (opts && opts.method) || "GET";
    const key = `${method} ${url.split("?")[0]}`;
    const handler = handlers[key];
    if (!handler) {
      // Generic match — try just the method + path prefix
      for (const k in handlers) {
        const [m, p] = k.split(" ");
        if (m === method && url.startsWith(p)) return handlers[k](url, opts);
      }
      throw new Error(`No mock handler for ${key}`);
    }
    return handler(url, opts);
  };
}


test("plant profiles: fetches list on mount and renders rows", async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  const rows = el.querySelector("[data-testid='pp-rows']");
  assert.ok(rows);
  assert.ok(el.querySelector("[data-testid='pp-row-1']"));
  assert.ok(el.querySelector("[data-testid='pp-row-2']"));
});


test("plant profiles: shipped rows show shipped pill", async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  const row = el.querySelector("[data-testid='pp-row-1']");
  const shippedPill = row.querySelector(".pp-pill-shipped");
  assert.ok(shippedPill);
  assert.match(shippedPill.textContent, /shipped/i);
});


test("plant profiles: 'modified from default' pill shows for shipped+edited rows",
async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  // Profile 2's notes contains the [modified] sentinel
  const modifiedPill =
    el.querySelector("[data-testid='pp-pill-modified-2']");
  assert.ok(modifiedPill);
  // Profile 1's notes is null — no modified pill
  const row1 = el.querySelector("[data-testid='pp-row-1']");
  assert.equal(row1.querySelector(".pp-pill-modified"), null);
});


test("plant profiles: clicking a row reveals editable fields", async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  // No editor yet
  assert.equal(el.querySelector("[data-testid='pp-editor-1']"), null);
  // Click the row head
  const head = el.querySelector("[data-testid='pp-row-head-1']");
  head.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  // Editor exists with input fields
  assert.ok(el.querySelector("[data-testid='pp-editor-1']"));
  assert.ok(
    el.querySelector("[data-testid='pp-input-1-target_moisture_pct']")
  );
  // Input pre-populated with current value
  const tgt = el.querySelector("[data-testid='pp-input-1-target_moisture_pct']");
  assert.equal(Number(tgt.value), 55);
  // Click again → editor collapses
  head.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(el.querySelector("[data-testid='pp-editor-1']"), null);
});


test("plant profiles: Save fires PUT with dirty-subset body", async () => {
  let captured = null;
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
    "PUT /api/grow/plant-profiles/1": async (url, opts) => {
      captured = { url, opts };
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  // Open editor for profile 1
  el.querySelector("[data-testid='pp-row-head-1']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  // Edit target_moisture_pct
  const tgt = el.querySelector("[data-testid='pp-input-1-target_moisture_pct']");
  tgt.value = "62";
  tgt.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  // Click Save
  el.querySelector("[data-testid='pp-save-1']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.ok(captured, "PUT was called");
  assert.equal(captured.url, "/api/grow/plant-profiles/1");
  assert.equal(captured.opts.method, "PUT");
  const body = JSON.parse(captured.opts.body);
  // Only the dirty field
  assert.deepEqual(body, { target_moisture_pct: 62 });
});


test("plant profiles: 400 validation error surfaces inline without crashing",
async () => {
  const fetchFn = _mockFetch({
    "GET /api/grow/plant-profiles": async () =>
      new Response(JSON.stringify(_seed()), { status: 200 }),
    "PUT /api/grow/plant-profiles/1": async () => new Response(
      JSON.stringify({
        error: "invalid_payload",
        detail: "min_pulse_s must be <= max_pulse_s",
      }),
      { status: 400 },
    ),
  });
  const el = renderPlantProfilesEditor({
    ownerDocument: document,
    fetchFn,
  });
  await _flushMicro();
  el.querySelector("[data-testid='pp-row-head-1']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const minP = el.querySelector("[data-testid='pp-input-1-min_pulse_s']");
  minP.value = "10";
  minP.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  const maxP = el.querySelector("[data-testid='pp-input-1-max_pulse_s']");
  maxP.value = "5";
  maxP.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  el.querySelector("[data-testid='pp-save-1']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const localErr = el.querySelector("[data-testid='pp-error-1']");
  assert.ok(localErr.textContent);
  assert.match(localErr.textContent.toLowerCase(),
    /min_pulse|<=|invalid|payload/);
});

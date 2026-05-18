/**
 * Tests for the Profile editor panel — first of two Configure-tab panels
 * delivered in Task 6 of the Configure-tab plan.
 *
 * Pattern: JSDOM document for ownerDocument injection, mock globalThis.fetch
 * for PUT round-trips, restore between tests so cross-test bleed is impossible.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderProfileEditor } from "../../static/js/grow/components/profile-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 7,
  label: "Tom 1",
  plant_type: "tomato",
  medium_type: "soil",
  current_phase: "vegetative",
  sown_at: "2026-04-01T00:00:00Z",
  description: "Heirloom Brandywine",
};


function _origFetch() {
  return globalThis.fetch;
}


function _setMockFetch(fn) {
  globalThis.fetch = fn;
}


/** Wait one microtask + one macrotask so awaited fetches can settle in tests. */
function _flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}


test("profile editor: renders current values into the form", () => {
  const el = renderProfileEditor(sampleUnit, { ownerDocument: document });
  const labelInput = el.querySelector("[data-testid='profile-label']");
  const plantInput = el.querySelector("[data-testid='profile-plant-type']");
  const mediumSel = el.querySelector("[data-testid='profile-medium-type']");
  const phaseSel = el.querySelector("[data-testid='profile-current-phase']");
  const sownInput = el.querySelector("[data-testid='profile-sown-at']");
  assert.equal(labelInput.value, "Tom 1");
  assert.equal(plantInput.value, "tomato");
  assert.equal(mediumSel.value, "soil");
  assert.equal(phaseSel.value, "vegetative");
  // sown_at is rendered into a date input as YYYY-MM-DD
  assert.equal(sownInput.value, "2026-04-01");
});


test("profile editor: PUTs to /api/grow/units/<id>/profile on save", async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderProfileEditor(sampleUnit, { ownerDocument: document });
    const form = el.querySelector("[data-testid='profile-form']");
    // Simulate edit + submit
    el.querySelector("[data-testid='profile-label']").value = "Tom 1 (renamed)";
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    // Let the async submit handler complete
    await _flush();
    await _flush();
    assert.equal(captured.url, "/api/grow/units/7/profile");
    assert.equal(captured.opts.method, "PUT");
    assert.equal(captured.opts.headers["Content-Type"], "application/json");
    const body = JSON.parse(captured.opts.body);
    assert.equal(body.label, "Tom 1 (renamed)");
    assert.equal(body.plant_type, "tomato");
    assert.equal(body.medium_type, "soil");
    assert.equal(body.current_phase, "vegetative");
  } finally {
    _setMockFetch(orig);
  }
});


test("profile editor: disables save button while request is in flight", async () => {
  const orig = _origFetch();
  // Hold the fetch pending so we can observe disabled-state.
  let resolveFetch;
  const pending = new Promise((res) => { resolveFetch = res; });
  _setMockFetch(() => pending);
  try {
    const el = renderProfileEditor(sampleUnit, { ownerDocument: document });
    const form = el.querySelector("[data-testid='profile-form']");
    const saveBtn = el.querySelector("[data-testid='profile-save']");
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    assert.equal(saveBtn.disabled, true);
    assert.match(saveBtn.textContent, /saving/i);
    // Resolve so the test doesn't leak a promise
    resolveFetch(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await _flush();
    await _flush();
  } finally {
    _setMockFetch(orig);
  }
});


test("profile editor: shows success indicator after 200", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  try {
    const el = renderProfileEditor(sampleUnit, { ownerDocument: document });
    const form = el.querySelector("[data-testid='profile-form']");
    const status = el.querySelector("[data-testid='profile-status']");
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    assert.match(status.textContent, /saved|✓/i);
    assert.match(status.className, /ok/);
  } finally {
    _setMockFetch(orig);
  }
});


test("profile editor: shows error detail after 400", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "invalid_payload", detail: "label too long" }),
    { status: 400 }
  ));
  try {
    const el = renderProfileEditor(sampleUnit, { ownerDocument: document });
    const form = el.querySelector("[data-testid='profile-form']");
    const status = el.querySelector("[data-testid='profile-status']");
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    assert.match(status.textContent, /invalid_payload|✗/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});

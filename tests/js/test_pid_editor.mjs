/**
 * Tests for the PID editor panel — second of two Configure-tab panels
 * delivered in Task 6 of the Configure-tab plan.
 *
 * Server side maps PIDUpdate.target_pct → grow_units.watering_target_override,
 * but the GET-/api/grow/units/<id> response surfaces it under
 * unit.overrides.watering_target. The form keys back to PIDUpdate field names
 * for the PUT, so we test that mapping carefully.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderPIDEditor } from "../../static/js/grow/components/pid-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


/** Minimal unit fixture; tests override `overrides` per scenario. */
function _unit(overrides) {
  return {
    id: 7,
    label: "Tom 1",
    plant_type: "tomato",
    overrides: {
      watering_target: null,
      kp: null,
      ki: null,
      kd: null,
      soak_window_min: null,
      min_pulse_s: null,
      max_pulse_s: null,
      ...overrides,
    },
  };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
function _flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }


test("pid editor: renders default badge for null overrides", () => {
  const el = renderPIDEditor(_unit({ kp: null }), { ownerDocument: document });
  const kpRow = el.querySelector("[data-testid='pid-row-kp']");
  assert.ok(kpRow, "kp row exists");
  const badge = kpRow.querySelector(".cfg-badge");
  assert.ok(badge, "badge present");
  assert.match(badge.className, /default/);
  assert.match(badge.textContent, /default/i);
  // No reset button should be rendered (or it should be disabled) for a default field
  const reset = kpRow.querySelector("[data-testid='pid-reset-kp']");
  if (reset) assert.equal(reset.disabled, true);
});


test("pid editor: renders custom badge + active reset for non-null override", () => {
  const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
  const kpRow = el.querySelector("[data-testid='pid-row-kp']");
  const badge = kpRow.querySelector(".cfg-badge");
  assert.match(badge.className, /custom/);
  assert.match(badge.textContent, /custom/i);
  const reset = kpRow.querySelector("[data-testid='pid-reset-kp']");
  assert.ok(reset);
  assert.equal(reset.disabled, false);
  // The kp input shows the override value
  const kpInput = el.querySelector("[data-testid='pid-input-kp']");
  assert.equal(kpInput.value, "0.5");
});


test("pid editor: PUTs only changed fields", async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    // user changes kp from 0.5 → 0.7; leaves ki untouched
    const kpInput = el.querySelector("[data-testid='pid-input-kp']");
    kpInput.value = "0.7";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    assert.equal(captured.url, "/api/grow/units/7/pid");
    assert.equal(captured.opts.method, "PUT");
    const body = JSON.parse(captured.opts.body);
    assert.equal(body.kp, 0.7);
    // ki was never touched, so should not be in PUT body
    assert.equal("ki" in body, false, "ki not sent");
    assert.equal("kd" in body, false, "kd not sent");
  } finally {
    _setMockFetch(orig);
  }
});


test("pid editor: reset to default PUTs null", async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const reset = el.querySelector("[data-testid='pid-reset-kp']");
    reset.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    const body = JSON.parse(captured.opts.body);
    // Server treats explicit null as "clear the override"
    assert.equal(body.kp, null);
    assert.ok("kp" in body);
  } finally {
    _setMockFetch(orig);
  }
});


test("pid editor: validates min_pulse_s <= max_pulse_s client-side", async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => {
    called = true;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderPIDEditor(_unit({}), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const minInput = el.querySelector("[data-testid='pid-input-min_pulse_s']");
    const maxInput = el.querySelector("[data-testid='pid-input-max_pulse_s']");
    minInput.value = "10";
    minInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    maxInput.value = "5";
    maxInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    assert.equal(called, false, "fetch should not be called when validation fails");
    const status = el.querySelector("[data-testid='pid-status']");
    assert.match(status.textContent, /min.*max|pulse/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});


test("pid editor: disables save during request", async () => {
  const orig = _origFetch();
  let resolveFetch;
  const pending = new Promise((res) => { resolveFetch = res; });
  _setMockFetch(() => pending);
  try {
    const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const saveBtn = el.querySelector("[data-testid='pid-save']");
    // Trigger an actual change so the form submits non-empty
    const kpInput = el.querySelector("[data-testid='pid-input-kp']");
    kpInput.value = "0.7";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    assert.equal(saveBtn.disabled, true);
    assert.match(saveBtn.textContent, /saving/i);
    resolveFetch(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await _flush();
    await _flush();
  } finally {
    _setMockFetch(orig);
  }
});


test("pid editor: shows success indicator after 200", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
  try {
    const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const status = el.querySelector("[data-testid='pid-status']");
    const kpInput = el.querySelector("[data-testid='pid-input-kp']");
    kpInput.value = "0.7";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    assert.match(status.textContent, /saved|✓/i);
    assert.match(status.className, /ok/);
  } finally {
    _setMockFetch(orig);
  }
});


test("test_pid_editor_blank_input_with_null_original_skips_field_in_put_body", async () => {
  // User types into a (default) field then clears it. Original was null,
  // so the PUT must NOT include this field at all — sending null would be
  // indistinguishable from clicking Reset on a field that was already
  // default. We also ensure another dirty field still gets through.
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    // kp: null (default), ki: null (default). User edits both then clears kp,
    // and gives ki a real value, so the form actually has a non-empty body.
    const el = renderPIDEditor(_unit({ kp: null, ki: null }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const kpInput = el.querySelector("[data-testid='pid-input-kp']");
    const kiInput = el.querySelector("[data-testid='pid-input-ki']");
    // Type into kp then clear it
    kpInput.value = "0.7";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    kpInput.value = "";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    // Type into ki and leave it set
    kiInput.value = "0.3";
    kiInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    const body = JSON.parse(captured.opts.body);
    assert.equal("kp" in body, false,
      "kp must not appear: original was null and field was blanked, no-op");
    assert.equal(body.ki, 0.3, "ki still gets sent");
  } finally {
    _setMockFetch(orig);
  }
});


test("test_pid_editor_blank_input_with_set_original_sends_null_to_clear", async () => {
  // User types into a (custom) field with a real override then clears it.
  // Original was 0.5, so blanking should send null (== clear the override).
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderPIDEditor(_unit({ kp: 0.5 }), { ownerDocument: document });
    const form = el.querySelector("[data-testid='pid-form']");
    const kpInput = el.querySelector("[data-testid='pid-input-kp']");
    // Field starts populated with "0.5". User clears it.
    kpInput.value = "";
    kpInput.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    form.dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    await _flush();
    await _flush();
    const body = JSON.parse(captured.opts.body);
    assert.ok("kp" in body, "kp must be in body to clear the override");
    assert.equal(body.kp, null, "kp must be null to clear the override");
  } finally {
    _setMockFetch(orig);
  }
});

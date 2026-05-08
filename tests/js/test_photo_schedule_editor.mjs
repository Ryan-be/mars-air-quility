/**
 * Photo capture schedule editor — Configure-tab Phase 4 polish panel.
 *
 * Two interaction modes:
 *   - "Capture 24/7" checkbox checked  → hour selectors disabled,
 *                                        save sends both nulls.
 *   - "Capture 24/7" checkbox unchecked → hour selectors enabled,
 *                                         save sends the chosen hours.
 *
 * Tests pin the round-trip behaviour: defaults, enable/disable,
 * PUT body shape, success surface, validation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderPhotoScheduleEditor } from
  "../../static/js/grow/components/photo-schedule-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit(opts = {}) {
  return {
    id: 7,
    label: "Tom 1",
    photo_schedule: { start_hour: null, end_hour: null },
    ...opts,
  };
}


async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


// ─── Default-on-load + visual state ────────────────────────────


test("defaults to 24/7 checkbox checked when both null", () => {
  const el = renderPhotoScheduleEditor(_unit(), { ownerDocument: document });
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  assert.equal(cb.checked, true);
  // Hour selectors disabled
  const start = el.querySelector("[data-testid='ps-start-hour']");
  const end = el.querySelector("[data-testid='ps-end-hour']");
  assert.equal(start.disabled, true);
  assert.equal(end.disabled, true);
});


test("loads existing window when set", () => {
  const el = renderPhotoScheduleEditor(_unit({
    photo_schedule: { start_hour: 6, end_hour: 22 },
  }), { ownerDocument: document });
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  assert.equal(cb.checked, false);
  assert.equal(
    el.querySelector("[data-testid='ps-start-hour']").value, "6");
  assert.equal(
    el.querySelector("[data-testid='ps-end-hour']").value, "22");
});


test("toggling 24/7 off enables hour selectors", () => {
  const el = renderPhotoScheduleEditor(_unit(), { ownerDocument: document });
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  const start = el.querySelector("[data-testid='ps-start-hour']");
  assert.equal(start.disabled, false);
});


// ─── Save round-trip ────────────────────────────


test("save with 24/7 checked sends both nulls", async () => {
  let bodyJson = null;
  const fetchFn = async (_url, opts) => {
    bodyJson = JSON.parse(opts.body);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const el = renderPhotoScheduleEditor(_unit({ id: 42 }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='ps-save-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.deepEqual(bodyJson, { start_hour: null, end_hour: null });
  const status = el.querySelector("[data-testid='ps-status']");
  assert.match(status.textContent, /24\/7/);
  assert.match(status.className, /ok/);
});


test("save with explicit window sends both ints", async () => {
  let bodyJson = null;
  let capturedUrl = null;
  const fetchFn = async (url, opts) => {
    capturedUrl = String(url);
    bodyJson = JSON.parse(opts.body);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const el = renderPhotoScheduleEditor(_unit({ id: 42 }), {
    ownerDocument: document, fetchFn,
  });
  // Uncheck 24/7
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  // Set hours
  el.querySelector("[data-testid='ps-start-hour']").value = "8";
  el.querySelector("[data-testid='ps-end-hour']").value = "20";
  el.querySelector("[data-testid='ps-save-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(capturedUrl, "/api/grow/units/42/photo_schedule");
  assert.deepEqual(bodyJson, { start_hour: 8, end_hour: 20 });
  const status = el.querySelector("[data-testid='ps-status']");
  assert.match(status.textContent, /08:00/);
  assert.match(status.textContent, /20:00/);
});


test("save with equal start/end blocked client-side", async () => {
  let called = false;
  const fetchFn = async () => {
    called = true;
    return new Response("{}", { status: 200 });
  };
  const el = renderPhotoScheduleEditor(_unit(), {
    ownerDocument: document, fetchFn,
  });
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  const start = el.querySelector("[data-testid='ps-start-hour']");
  const end = el.querySelector("[data-testid='ps-end-hour']");
  start.value = "12";
  end.value = "12";
  el.querySelector("[data-testid='ps-save-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false, "PUT must not fire on client-side validation fail");
  const status = el.querySelector("[data-testid='ps-status']");
  assert.match(status.textContent, /must differ/i);
  assert.match(status.className, /err/);
});


test("403 surfaces inline error", async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "forbidden" }), { status: 403 },
  );
  const el = renderPhotoScheduleEditor(_unit(), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='ps-save-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='ps-status']");
  assert.match(status.textContent.toLowerCase(),
    /controller|admin|forbidden/);
  assert.match(status.className, /err/);
});


test("wraps midnight (start > end) is allowed", async () => {
  let bodyJson = null;
  const fetchFn = async (_url, opts) => {
    bodyJson = JSON.parse(opts.body);
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const el = renderPhotoScheduleEditor(_unit({ id: 42 }), {
    ownerDocument: document, fetchFn,
  });
  const cb = el.querySelector("[data-testid='ps-247-checkbox']");
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  el.querySelector("[data-testid='ps-start-hour']").value = "22";
  el.querySelector("[data-testid='ps-end-hour']").value = "6";
  el.querySelector("[data-testid='ps-save-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.deepEqual(bodyJson, { start_hour: 22, end_hour: 6 });
});

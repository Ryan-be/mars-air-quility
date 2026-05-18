/**
 * Tests for the Danger Zone — fourth section of the Diagnostics tab.
 *
 * Three actions, all admin-only:
 *   1. Token rotator (relocated component) — confirm via existing test
 *      coverage; here we just assert the danger-zone embeds it.
 *   2. Decommission — type-the-label-to-confirm + DELETE.
 *   3. Clear remote buffer — single OK/Cancel + POST.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderDangerZone } from
  "../../static/js/grow/components/danger-zone.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit(opts = {}) {
  return { id: 7, label: "Tom 1", ...opts };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


// ---------------------------------------------------------------------
// Token rotator embedding
// ---------------------------------------------------------------------


test("danger zone: includes token rotator", () => {
  const el = renderDangerZone(_unit(), { ownerDocument: document });
  // The token-rotator component owns a top-level wrapper with this testid.
  const rotator = el.querySelector("[data-testid='token-rotator']");
  assert.ok(rotator, "token rotator embedded");
  const rotateBtn = el.querySelector("[data-testid='tr-rotate-btn']");
  assert.ok(rotateBtn, "token rotator's rotate button accessible");
});


// ---------------------------------------------------------------------
// Decommission — type-the-label-to-confirm
// ---------------------------------------------------------------------


test("decommission: confirm button starts disabled until label typed",
() => {
  const el = renderDangerZone(_unit({ label: "Tom 1" }), {
    ownerDocument: document,
  });
  const armBtn = el.querySelector("[data-testid='decom-arm-btn']");
  armBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const confirmBtn = el.querySelector("[data-testid='decom-confirm-btn']");
  assert.equal(confirmBtn.disabled, true, "confirm starts disabled");

  const input = el.querySelector("[data-testid='decom-label-input']");
  input.value = "wrong label";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(confirmBtn.disabled, true, "still disabled with wrong label");

  input.value = "Tom 1";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  assert.equal(confirmBtn.disabled, false, "enables when label matches exactly");
});


test("decommission: fires DELETE on confirm", async () => {
  let capturedUrl = null;
  let capturedMethod = null;
  const fetchFn = async (url, opts) => {
    capturedUrl = String(url);
    capturedMethod = opts && opts.method;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const el = renderDangerZone(_unit({ id: 42, label: "Tom 1" }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='decom-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const input = el.querySelector("[data-testid='decom-label-input']");
  input.value = "Tom 1";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  el.querySelector("[data-testid='decom-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(capturedUrl, "/api/grow/units/42");
  assert.equal(capturedMethod, "DELETE");
  // Success surface
  const status = el.querySelector("[data-testid='decom-status']");
  assert.match(status.textContent, /decommissioned/i);
});


test("decommission: 403 surfaces inline error", async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "forbidden" }), { status: 403 },
  );
  const el = renderDangerZone(_unit({ label: "Tom 1" }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='decom-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const input = el.querySelector("[data-testid='decom-label-input']");
  input.value = "Tom 1";
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  el.querySelector("[data-testid='decom-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='decom-status']");
  assert.match(status.textContent.toLowerCase(), /admin|forbidden/);
  assert.match(status.className, /err/);
});


test("decommission: cancel returns to idle without firing DELETE", async () => {
  let called = false;
  const fetchFn = async () => { called = true; return new Response("{}"); };
  const el = renderDangerZone(_unit({ label: "Tom 1" }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='decom-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='decom-cancel-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false, "DELETE must not fire on cancel");
  // Arm button visible again, confirm pane hidden
  const armBtn = el.querySelector("[data-testid='decom-arm-btn']");
  assert.notEqual(armBtn.style.display, "none");
  const confirmPane = el.querySelector("[data-testid='decom-confirm']");
  assert.equal(confirmPane.style.display, "none");
});


// ---------------------------------------------------------------------
// Clear remote buffer
// ---------------------------------------------------------------------


test("clear buffer: clicking arm shows confirm pane (no POST yet)",
async () => {
  let called = false;
  const fetchFn = async () => { called = true; return new Response("{}"); };
  const el = renderDangerZone(_unit(), { ownerDocument: document, fetchFn });
  el.querySelector("[data-testid='clear-buffer-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false, "POST must not fire on arm click alone");
  const confirmPane = el.querySelector("[data-testid='clear-buffer-confirm']");
  assert.notEqual(confirmPane.style.display, "none");
  // Warning copy mentions the consequence
  const warn = confirmPane.querySelector(".diag-danger-warn");
  assert.match(warn.textContent.toLowerCase(),
    /permanently lost|un-replayed|telemetry/);
});


test("clear buffer: confirm fires POST to /clear-buffer", async () => {
  let capturedUrl = null;
  let capturedMethod = null;
  const fetchFn = async (url, opts) => {
    capturedUrl = String(url);
    capturedMethod = opts && opts.method;
    return new Response(JSON.stringify({ queued: true }), { status: 202 });
  };
  const el = renderDangerZone(_unit({ id: 42 }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-buffer-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-buffer-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(capturedUrl, "/api/grow/units/42/clear-buffer");
  assert.equal(capturedMethod, "POST");
  const status = el.querySelector("[data-testid='clear-buffer-status']");
  assert.match(status.textContent.toLowerCase(), /cleared/);
  assert.match(status.className, /ok/);
});


test("clear buffer: 503 response shows offline-friendly message",
async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "unit_not_connected" }),
    { status: 503 },
  );
  const el = renderDangerZone(_unit(), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-buffer-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-buffer-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='clear-buffer-status']");
  assert.match(status.textContent.toLowerCase(),
    /offline|reconnected|try again/);
  assert.match(status.className, /err/);
});


test("clear buffer: cancel returns to idle without firing POST",
async () => {
  let called = false;
  const fetchFn = async () => { called = true; return new Response("{}"); };
  const el = renderDangerZone(_unit(), { ownerDocument: document, fetchFn });
  el.querySelector("[data-testid='clear-buffer-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-buffer-cancel-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false);
  const armBtn = el.querySelector("[data-testid='clear-buffer-arm-btn']");
  assert.notEqual(armBtn.style.display, "none");
  const confirmPane = el.querySelector("[data-testid='clear-buffer-confirm']");
  assert.equal(confirmPane.style.display, "none");
});


// ---------------------------------------------------------------------
// Clear all photos
// ---------------------------------------------------------------------


test("clear photos: section is mounted in danger zone", () => {
  const el = renderDangerZone(_unit(), { ownerDocument: document });
  const action = el.querySelector("[data-testid='clear-photos-action']");
  assert.ok(action, "clear-photos action mounted");
  const armBtn = el.querySelector("[data-testid='clear-photos-arm-btn']");
  assert.ok(armBtn, "clear-photos arm button rendered");
  // Description mentions photos + going-live use case so the operator
  // knows what they're about to delete.
  assert.match(action.textContent.toLowerCase(),
    /photo|test.data|live|jpeg|disk/);
});


test("clear photos: clicking arm shows confirm pane (no DELETE yet)",
async () => {
  let called = false;
  const fetchFn = async () => { called = true; return new Response("{}"); };
  const el = renderDangerZone(_unit({ label: "Tom 1" }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false, "DELETE must not fire on arm click alone");
  const confirmPane = el.querySelector("[data-testid='clear-photos-confirm']");
  assert.notEqual(confirmPane.style.display, "none");
  // Warning copy mentions the unit by label (so the operator can't
  // confuse it with a different one).
  const warn = confirmPane.querySelector(".diag-danger-warn");
  assert.match(warn.textContent, /Tom 1/);
});


test("clear photos: confirm fires DELETE to /photos and shows count",
async () => {
  let capturedUrl = null;
  let capturedMethod = null;
  const fetchFn = async (url, opts) => {
    capturedUrl = String(url);
    capturedMethod = opts && opts.method;
    return new Response(JSON.stringify({ deleted_count: 7 }), { status: 200 });
  };
  const el = renderDangerZone(_unit({ id: 42 }), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-photos-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(capturedUrl, "/api/grow/units/42/photos");
  assert.equal(capturedMethod, "DELETE");
  const status = el.querySelector("[data-testid='clear-photos-status']");
  assert.match(status.textContent, /Deleted 7 photos/);
  assert.match(status.className, /ok/);
});


test("clear photos: zero count shows specialised message", async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ deleted_count: 0 }), { status: 200 },
  );
  const el = renderDangerZone(_unit(), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-photos-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='clear-photos-status']");
  assert.match(status.textContent.toLowerCase(),
    /no photos|nothing to delete/);
  assert.match(status.className, /ok/);
});


test("clear photos: 1 deleted uses singular form", async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ deleted_count: 1 }), { status: 200 },
  );
  const el = renderDangerZone(_unit(), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-photos-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='clear-photos-status']");
  assert.match(status.textContent, /Deleted 1 photo\./);
  // No trailing 's' — singular form.
  assert.doesNotMatch(status.textContent, /Deleted 1 photos/);
});


test("clear photos: 403 surfaces admin-only message", async () => {
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "forbidden" }), { status: 403 },
  );
  const el = renderDangerZone(_unit(), {
    ownerDocument: document, fetchFn,
  });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-photos-confirm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const status = el.querySelector("[data-testid='clear-photos-status']");
  assert.match(status.textContent.toLowerCase(), /admin|forbidden/);
  assert.match(status.className, /err/);
});


test("clear photos: cancel returns to idle without firing DELETE",
async () => {
  let called = false;
  const fetchFn = async () => { called = true; return new Response("{}"); };
  const el = renderDangerZone(_unit(), { ownerDocument: document, fetchFn });
  el.querySelector("[data-testid='clear-photos-arm-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("[data-testid='clear-photos-cancel-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(called, false, "DELETE must not fire on cancel");
  const armBtn = el.querySelector("[data-testid='clear-photos-arm-btn']");
  assert.notEqual(armBtn.style.display, "none");
  const confirmPane = el.querySelector("[data-testid='clear-photos-confirm']");
  assert.equal(confirmPane.style.display, "none");
});

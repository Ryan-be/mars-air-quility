/**
 * Tests for the Enrollment-key rotator panel — Settings → Grow.
 *
 * State machine:
 *   idle → click rotate → armed (Confirm/Cancel)
 *                       → cancel → idle
 *                       → confirm → fetch POST → reveal pane
 *                                                → done → idle (key cleared)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderEnrollmentKeyRotator } from
  "../../static/js/grow/components/enrollment-key-rotator.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
async function _flushMicro() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}


test("ek rotator: renders rotate button + explanation, no reveal yet", () => {
  const el = renderEnrollmentKeyRotator({ ownerDocument: document });
  const btn = el.querySelector("[data-testid='ek-rotate-btn']");
  assert.ok(btn, "rotate button present");
  assert.match(btn.textContent, /rotate/i);
  // Explanation paragraph mentions the household-key-vs-bearer-token nuance
  const blurb = el.querySelector(".ek-blurb");
  assert.ok(blurb);
  assert.match(blurb.textContent.toLowerCase(),
    /enrolled units|bearer|future enroll/);
  // Reveal pane is hidden
  const reveal = el.querySelector("[data-testid='ek-reveal']");
  assert.ok(reveal);
  assert.equal(reveal.style.display, "none");
});


test("ek rotator: click 'Rotate' shows Confirm/Cancel without firing fetch", async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => { called = true; return new Response("{}"); });
  try {
    const el = renderEnrollmentKeyRotator({ ownerDocument: document });
    const rotateBtn = el.querySelector("[data-testid='ek-rotate-btn']");
    rotateBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    assert.equal(called, false,
      "fetch must not fire on the first click");
    const confirmGroup =
      el.querySelector("[data-testid='ek-confirm-group']");
    assert.notEqual(confirmGroup.style.display, "none");
    assert.equal(rotateBtn.style.display, "none");
    assert.ok(el.querySelector("[data-testid='ek-confirm-btn']"));
    assert.ok(el.querySelector("[data-testid='ek-cancel-btn']"));
  } finally {
    _setMockFetch(orig);
  }
});


test("ek rotator: cancel returns to idle", () => {
  const el = renderEnrollmentKeyRotator({ ownerDocument: document });
  const rotateBtn = el.querySelector("[data-testid='ek-rotate-btn']");
  const cancelBtn = el.querySelector("[data-testid='ek-cancel-btn']");
  rotateBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  cancelBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  // Idle state: rotate visible, confirm group hidden
  assert.notEqual(rotateBtn.style.display, "none");
  const confirmGroup = el.querySelector("[data-testid='ek-confirm-group']");
  assert.equal(confirmGroup.style.display, "none");
});


test("ek rotator: confirm fires POST and displays the new key in a copy field",
async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(
      JSON.stringify({ key: "fresh-key-12345-abcdefg" }),
      { status: 201 },
    );
  });
  try {
    const el = renderEnrollmentKeyRotator({ ownerDocument: document });
    const rotateBtn = el.querySelector("[data-testid='ek-rotate-btn']");
    rotateBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    const confirmBtn = el.querySelector("[data-testid='ek-confirm-btn']");
    confirmBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    assert.ok(captured, "fetch was called");
    assert.equal(captured.url, "/api/grow/enrollment-key/rotate");
    assert.equal(captured.opts.method, "POST");

    const reveal = el.querySelector("[data-testid='ek-reveal']");
    assert.notEqual(reveal.style.display, "none");
    const keyInput = el.querySelector("[data-testid='ek-key-input']");
    // Copyable: an input element (or textarea), readonly so the user can't edit
    assert.ok(
      keyInput.tagName === "INPUT" || keyInput.tagName === "TEXTAREA",
      "key field is selectable input, not plain text"
    );
    assert.equal(keyInput.readOnly, true);
    assert.equal(keyInput.value, "fresh-key-12345-abcdefg");
  } finally {
    _setMockFetch(orig);
  }
});


test("ek rotator: clicking 'Done' clears the revealed key and returns to idle",
async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ key: "secret-key-xyz" }),
    { status: 201 },
  ));
  try {
    const el = renderEnrollmentKeyRotator({ ownerDocument: document });
    el.querySelector("[data-testid='ek-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='ek-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    const keyInput = el.querySelector("[data-testid='ek-key-input']");
    assert.equal(keyInput.value, "secret-key-xyz");

    const doneBtn = el.querySelector("[data-testid='ek-done-btn']");
    doneBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));

    // Reveal hidden, key cleared, rotate button visible again
    const reveal = el.querySelector("[data-testid='ek-reveal']");
    assert.equal(reveal.style.display, "none");
    assert.equal(keyInput.value, "");
    const rotateBtn = el.querySelector("[data-testid='ek-rotate-btn']");
    assert.notEqual(rotateBtn.style.display, "none");
  } finally {
    _setMockFetch(orig);
  }
});


test("ek rotator: surfaces server errors without revealing", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "Forbidden" }),
    { status: 403 },
  ));
  try {
    const el = renderEnrollmentKeyRotator({ ownerDocument: document });
    el.querySelector("[data-testid='ek-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='ek-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    // Reveal stays hidden; error message visible
    const reveal = el.querySelector("[data-testid='ek-reveal']");
    assert.equal(reveal.style.display, "none");
    const errEl = el.querySelector("[data-testid='ek-error']");
    assert.notEqual(errEl.style.display, "none");
    assert.match(errEl.textContent.toLowerCase(), /forbidden|error/);
  } finally {
    _setMockFetch(orig);
  }
});

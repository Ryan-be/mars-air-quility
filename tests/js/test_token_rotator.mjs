/**
 * Tests for the per-unit Token Rotator component.
 *
 * Mirrors the enrollment-key-rotator state machine (idle → armed →
 * reveal/error) but POSTs to /api/grow/units/<id>/rotate-token and
 * surfaces 403/404 inline rather than entering the reveal pane.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderTokenRotator } from
  "../../static/js/grow/components/token-rotator.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = { id: 7, label: "Tom 1" };


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
async function _flushMicro() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}


test("token rotator: renders danger button with warning label, no reveal yet", () => {
  const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
  const btn = el.querySelector("[data-testid='tr-rotate-btn']");
  assert.ok(btn, "rotate button present");
  assert.match(btn.textContent.toLowerCase(), /rotate.*token|token.*rotate/);
  // Danger styling applied — defence against accidental click on a non-danger
  // button. Class list must contain "danger" alongside the base px-btn.
  assert.ok(
    btn.classList.contains("danger"),
    "rotate button is styled as a danger action",
  );
  // Reveal pane hidden initially
  const reveal = el.querySelector("[data-testid='tr-reveal']");
  assert.equal(reveal.style.display, "none");
  // Blurb explains the operational impact (offline, /etc/mlss-grow/token.json)
  const blurb = el.querySelector(".tr-blurb");
  assert.ok(blurb);
  assert.match(blurb.textContent, /\/etc\/mlss-grow\/token\.json|offline/i);
});


test("token rotator: click 'Rotate' shows confirm modal first (no fetch yet)",
async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => { called = true; return new Response("{}"); });
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    const rotateBtn = el.querySelector("[data-testid='tr-rotate-btn']");
    rotateBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    assert.equal(called, false, "fetch must not fire on the first click");
    const confirmGroup = el.querySelector("[data-testid='tr-confirm-group']");
    assert.notEqual(confirmGroup.style.display, "none");
    assert.equal(rotateBtn.style.display, "none");
    assert.ok(el.querySelector("[data-testid='tr-confirm-btn']"));
    assert.ok(el.querySelector("[data-testid='tr-cancel-btn']"));
    // Warning copy mentions the consequences explicitly
    const warn = el.querySelector(".tr-warn");
    assert.match(warn.textContent, /invalidate|offline|continue/i);
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: cancel returns to idle without firing fetch", async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => { called = true; return new Response("{}"); });
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    const rotateBtn = el.querySelector("[data-testid='tr-rotate-btn']");
    rotateBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-cancel-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    assert.equal(called, false);
    assert.notEqual(rotateBtn.style.display, "none");
    const confirmGroup = el.querySelector("[data-testid='tr-confirm-group']");
    assert.equal(confirmGroup.style.display, "none");
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: confirm fires POST to /api/grow/units/<id>/rotate-token",
async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(
      JSON.stringify({ token: "fresh-token-43chars-long-enough-for-a-pretend-secret" }),
      { status: 201 },
    );
  });
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    el.querySelector("[data-testid='tr-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    assert.ok(captured, "fetch was called");
    assert.equal(captured.url, "/api/grow/units/7/rotate-token");
    assert.equal(captured.opts.method, "POST");
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: displays returned token in copy-friendly readonly field",
async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ token: "secret-token-xyz" }),
    { status: 201 },
  ));
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    el.querySelector("[data-testid='tr-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    const reveal = el.querySelector("[data-testid='tr-reveal']");
    assert.notEqual(reveal.style.display, "none");
    const tokenInput = el.querySelector("[data-testid='tr-token-input']");
    assert.ok(
      tokenInput.tagName === "INPUT" || tokenInput.tagName === "TEXTAREA",
      "token field is a selectable input element",
    );
    assert.equal(tokenInput.readOnly, true);
    assert.equal(tokenInput.value, "secret-token-xyz");
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: 'Done' clears revealed token + hides reveal pane",
async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ token: "token-to-be-cleared" }),
    { status: 201 },
  ));
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    el.querySelector("[data-testid='tr-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    const tokenInput = el.querySelector("[data-testid='tr-token-input']");
    assert.equal(tokenInput.value, "token-to-be-cleared");

    el.querySelector("[data-testid='tr-done-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));

    const reveal = el.querySelector("[data-testid='tr-reveal']");
    assert.equal(reveal.style.display, "none");
    assert.equal(tokenInput.value, "");
    const rotateBtn = el.querySelector("[data-testid='tr-rotate-btn']");
    assert.notEqual(rotateBtn.style.display, "none");
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: 403 surfaces inline error without revealing", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "Forbidden" }),
    { status: 403 },
  ));
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    el.querySelector("[data-testid='tr-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    const reveal = el.querySelector("[data-testid='tr-reveal']");
    assert.equal(reveal.style.display, "none");
    const errEl = el.querySelector("[data-testid='tr-error']");
    assert.notEqual(errEl.style.display, "none");
    assert.match(errEl.textContent.toLowerCase(), /forbidden|admin/);
  } finally {
    _setMockFetch(orig);
  }
});


test("token rotator: 404 surfaces 'unit deleted' message inline", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => new Response(
    JSON.stringify({ error: "unit_not_found" }),
    { status: 404 },
  ));
  try {
    const el = renderTokenRotator(sampleUnit, { ownerDocument: document });
    el.querySelector("[data-testid='tr-rotate-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    el.querySelector("[data-testid='tr-confirm-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    const reveal = el.querySelector("[data-testid='tr-reveal']");
    assert.equal(reveal.style.display, "none");
    const errEl = el.querySelector("[data-testid='tr-error']");
    assert.notEqual(errEl.style.display, "none");
    assert.match(errEl.textContent.toLowerCase(), /not found|deleted/);
  } finally {
    _setMockFetch(orig);
  }
});

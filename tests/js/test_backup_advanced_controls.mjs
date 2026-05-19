/**
 * Tests for the admin backup Advanced Controls component.
 *
 * Four confirm-gated buttons:
 *   - Pause shipping / Resume (toggled by current paused state)
 *   - Force re-bootstrap (confirm dialog)
 *   - Clear outbox     (confirm dialog with magic-word gate)
 *
 * Critical behaviour under test:
 *   - Destructive actions REQUIRE explicit confirmation; the
 *     confirm dialog uses a magic-word challenge (typing "CLEAR"
 *     or "BOOTSTRAP") so a stray double-click cannot wipe data.
 *   - The component NEVER calls window.confirm() — the design
 *     system has a proper dialog.
 *   - Pause/Resume toggles based on current state (no challenge —
 *     reversible).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderAdvancedControls }
  from "../../static/js/backup/components/advanced-controls.mjs";

const dom = new JSDOM();
global.document = dom.window.document;
global.window = dom.window;


async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


/**
 * Capture window.confirm calls. The component MUST NOT call
 * window.confirm — using the design system inline confirm panel
 * instead. If window.confirm fires, the test fails.
 */
function _failOnWindowConfirm() {
  const calls = [];
  const orig = global.window.confirm;
  global.window.confirm = (msg) => { calls.push(msg); return false; };
  return {
    calls,
    restore() { global.window.confirm = orig; },
  };
}


test("advanced controls: renders Pause button when paused=false", () => {
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
  });
  const btn = el.querySelector("[data-action='pause-resume']");
  assert.ok(btn);
  assert.match(btn.textContent, /pause/i);
});


test("advanced controls: renders Resume button when paused=true", () => {
  const el = renderAdvancedControls({
    paused: true,
    ownerDocument: document,
  });
  const btn = el.querySelector("[data-action='pause-resume']");
  assert.match(btn.textContent, /resume/i);
});


test("advanced controls: pause click sends action=pause + confirm=true", async () => {
  let captured = null;
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
    fetchFn: async (url, opts) => {
      captured = { url, opts: { ...opts, body: JSON.parse(opts.body) } };
      return new Response(JSON.stringify({ ok: true, action: "paused" }),
        { status: 200 });
    },
  });
  // Pause is reversible — no magic-word challenge.
  el.querySelector("[data-action='pause-resume']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.ok(captured);
  assert.match(captured.url, /\/api\/admin\/backup\/maintenance/);
  assert.equal(captured.opts.method, "POST");
  assert.equal(captured.opts.body.action, "pause");
  assert.equal(captured.opts.body.confirm, true);
});


test("advanced controls: resume click sends action=resume + confirm=true", async () => {
  let captured = null;
  const el = renderAdvancedControls({
    paused: true,
    ownerDocument: document,
    fetchFn: async (url, opts) => {
      captured = { url, opts: { ...opts, body: JSON.parse(opts.body) } };
      return new Response(JSON.stringify({ ok: true, action: "resumed" }),
        { status: 200 });
    },
  });
  el.querySelector("[data-action='pause-resume']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(captured.opts.body.action, "resume");
});


test("advanced controls: clear outbox click reveals confirm dialog, no fetch", async () => {
  let fetchCalls = 0;
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
    fetchFn: async () => { fetchCalls++; return new Response("{}"); },
  });
  el.querySelector("[data-action='clear-outbox']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const dialog = el.querySelector("[data-confirm='clear-outbox']");
  assert.ok(dialog, "confirm dialog must appear");
  assert.notEqual(dialog.style.display, "none");
  // Magic-word challenge field present
  const challenge = dialog.querySelector("[data-field='challenge']");
  assert.ok(challenge);
  assert.equal(fetchCalls, 0, "no API call until the magic word is entered");
});


test("advanced controls: clear outbox confirm button disabled until magic word typed",
async () => {
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
    fetchFn: async () => new Response("{}"),
  });
  el.querySelector("[data-action='clear-outbox']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const dialog = el.querySelector("[data-confirm='clear-outbox']");
  const confirmBtn = dialog.querySelector("[data-action='confirm']");
  assert.equal(confirmBtn.disabled, true,
    "confirm button must be disabled before challenge is typed");

  const challenge = dialog.querySelector("[data-field='challenge']");
  challenge.value = "wrong";
  challenge.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await _flushMicro();
  assert.equal(confirmBtn.disabled, true,
    "still disabled with wrong magic word");

  challenge.value = "CLEAR";
  challenge.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await _flushMicro();
  assert.equal(confirmBtn.disabled, false,
    "enabled once 'CLEAR' is typed exactly");
});


test("advanced controls: confirmed clear outbox sends action=clear_outbox", async () => {
  let captured = null;
  const guard = _failOnWindowConfirm();
  try {
    const el = renderAdvancedControls({
      paused: false,
      ownerDocument: document,
      fetchFn: async (url, opts) => {
        captured = { url, opts: { ...opts, body: JSON.parse(opts.body) } };
        return new Response(
          JSON.stringify({ ok: true, action: "outbox cleared" }),
          { status: 200 });
      },
    });
    // Open dialog
    el.querySelector("[data-action='clear-outbox']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    // Type magic word
    const challenge = el.querySelector(
      "[data-confirm='clear-outbox'] [data-field='challenge']");
    challenge.value = "CLEAR";
    challenge.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    await _flushMicro();
    // Confirm
    el.querySelector(
      "[data-confirm='clear-outbox'] [data-action='confirm']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    assert.ok(captured);
    assert.equal(captured.opts.body.action, "clear_outbox");
    assert.equal(captured.opts.body.confirm, true);
    assert.equal(guard.calls.length, 0,
      "window.confirm MUST NOT be called");
  } finally {
    guard.restore();
  }
});


test("advanced controls: force re-bootstrap requires BOOTSTRAP magic word", async () => {
  let captured = null;
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
    fetchFn: async (url, opts) => {
      captured = { url, opts: { ...opts, body: JSON.parse(opts.body) } };
      return new Response(
        JSON.stringify({ ok: true, action: "force_rebootstrap started" }),
        { status: 200 });
    },
  });
  el.querySelector("[data-action='force-rebootstrap']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const dialog = el.querySelector("[data-confirm='force-rebootstrap']");
  assert.ok(dialog);
  const confirmBtn = dialog.querySelector("[data-action='confirm']");
  assert.equal(confirmBtn.disabled, true);

  const challenge = dialog.querySelector("[data-field='challenge']");
  challenge.value = "BOOTSTRAP";
  challenge.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  await _flushMicro();
  assert.equal(confirmBtn.disabled, false);

  confirmBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.equal(captured.opts.body.action, "force_rebootstrap");
});


test("advanced controls: cancel dialog dismisses without fetch", async () => {
  let fetchCalls = 0;
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
    fetchFn: async () => { fetchCalls++; return new Response("{}"); },
  });
  el.querySelector("[data-action='clear-outbox']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const cancelBtn = el.querySelector(
    "[data-confirm='clear-outbox'] [data-action='cancel']");
  assert.ok(cancelBtn);
  cancelBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const dialog = el.querySelector("[data-confirm='clear-outbox']");
  assert.equal(dialog.style.display, "none");
  assert.equal(fetchCalls, 0);
});


test("advanced controls: setPaused() refreshes the pause/resume button", () => {
  // The orchestrator calls setPaused(true/false) after a successful
  // pause/resume action so the button label flips immediately without
  // waiting for the next /status poll.
  const el = renderAdvancedControls({
    paused: false,
    ownerDocument: document,
  });
  let btn = el.querySelector("[data-action='pause-resume']");
  assert.match(btn.textContent, /pause/i);
  el.setPaused(true);
  btn = el.querySelector("[data-action='pause-resume']");
  assert.match(btn.textContent, /resume/i);
});

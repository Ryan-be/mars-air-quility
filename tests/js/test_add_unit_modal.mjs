/**
 * Tests for the add-unit modal opened from the "+ Add Unit" button on
 * the /grow fleet view.
 *
 * State machine:
 *   admin idle → click reveal → fetch peek-once
 *                              → 200 → reveal pane (key visible + copy)
 *                              → 410 → already-revealed pane (link to Settings)
 *                              → 403 → inline error (no reveal)
 *   non-admin idle → reveal button hidden, "ask your admin" message shown
 *
 * Each test mounts a fresh JSDOM window so body.dataset.role gating
 * starts clean — the module reads `document.body.dataset.role` at the
 * moment the modal is opened, so tests must set it before opening.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openAddUnitModal } from
  "../../static/js/grow/components/add-unit-modal.mjs";


function _newDom(role = "admin") {
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="${role}"></body></html>`,
  );
  // We deliberately avoid reassigning `global.navigator` — Node 22+ makes
  // it a read-only getter. The component reads navigator via the page's
  // global, so tests stash the JSDOM document on global and let the
  // module pick that up instead.
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
async function _flushMicro() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}


test("add-unit modal: admin sees a Reveal button + brief explanation", () => {
  const dom = _newDom("admin");
  const { close, element } = openAddUnitModal({ ownerDocument: dom.window.document });
  try {
    const revealBtn = element.querySelector("[data-testid='add-unit-reveal-btn']");
    assert.ok(revealBtn, "reveal button present for admin");
    assert.notEqual(revealBtn.style.display, "none");
    assert.match(revealBtn.textContent.toLowerCase(), /reveal/);
    // Reveal pane hidden until peek-once succeeds
    const reveal = element.querySelector("[data-testid='add-unit-reveal']");
    assert.equal(reveal.style.display, "none");
    // Blurb explains the one-shot nature of the key
    const blurb = element.querySelector(".add-unit-blurb");
    assert.ok(blurb);
    assert.match(blurb.textContent.toLowerCase(), /enrollment|key|once/);
  } finally {
    close();
  }
});


test("add-unit modal: non-admin viewers don't see the reveal button", () => {
  const dom = _newDom("viewer");
  const { close, element } = openAddUnitModal({ ownerDocument: dom.window.document });
  try {
    const revealBtn = element.querySelector("[data-testid='add-unit-reveal-btn']");
    assert.equal(revealBtn.style.display, "none",
      "reveal button is hidden for viewers");
    const adminOnly = element.querySelector("[data-testid='add-unit-admin-only']");
    assert.ok(adminOnly, "admin-only stub message is shown instead");
    assert.match(adminOnly.textContent.toLowerCase(), /admin/);
  } finally {
    close();
  }
});


test("add-unit modal: clicking Reveal calls peek-once and shows the key", async () => {
  const dom = _newDom("admin");
  let captured = null;
  const fetchFn = async (url, opts) => {
    captured = { url, opts };
    return new Response(
      JSON.stringify({ key: "fresh-key-abc-123" }),
      { status: 200 },
    );
  };
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    assert.ok(captured, "fetch was called");
    assert.equal(captured.url, "/api/grow/enrollment-key/peek-once");

    const reveal = element.querySelector("[data-testid='add-unit-reveal']");
    assert.notEqual(reveal.style.display, "none");
    const keyInput = element.querySelector("[data-testid='add-unit-key-input']");
    assert.ok(
      keyInput.tagName === "INPUT" || keyInput.tagName === "TEXTAREA",
      "key field is selectable input element",
    );
    assert.equal(keyInput.readOnly, true);
    assert.equal(keyInput.value, "fresh-key-abc-123");
    // Idle row hidden after reveal
    const idleRow = element.querySelector("[data-testid='add-unit-idle']");
    assert.equal(idleRow.style.display, "none");
  } finally {
    close();
  }
});


test("add-unit modal: emits a complete YAML block pre-filled with host + key", async () => {
  const dom = _newDom("admin");
  const fetchFn = async () => new Response(
    JSON.stringify({ key: "real-enrollment-key-abc" }), { status: 200 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
    mlssHost: "mlss.local",
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    const yaml = element.querySelector("[data-testid='add-unit-yaml']");
    assert.ok(yaml, "yaml snippet block must be rendered");
    const text = yaml.textContent;
    assert.match(text, /mlss_host:\s*mlss\.local/);
    assert.match(text, /mlss_port:\s*5000/);
    assert.match(text, /enrollment_key:\s*real-enrollment-key-abc/);
    // Boot step is shown alongside the YAML
    const boot = element.querySelector("[data-testid='add-unit-boot-step']");
    assert.ok(boot);
    assert.match(boot.textContent.toLowerCase(), /boot the pi/);
    // Alt SSH/curl path is collapsed but available for re-provisioning
    const alt = element.querySelector("[data-testid='add-unit-alt']");
    assert.ok(alt);
    assert.match(alt.textContent, /curl/);
    assert.match(alt.textContent, /install\.sh/);
  } finally {
    close();
  }
});


test("add-unit modal: Copy YAML copies the complete block to clipboard", async () => {
  const dom = _newDom("admin");
  // Stub navigator.clipboard.writeText on the jsdom window. Node 22+ makes
  // globalThis.navigator a read-only getter, so define via Object.defineProperty.
  let copied = null;
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (s) => { copied = s; } },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    get: () => dom.window.navigator,
  });

  const fetchFn = async () => new Response(
    JSON.stringify({ key: "kkk" }), { status: 200 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
    mlssHost: "192.0.2.10",
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    const btn = element.querySelector("[data-testid='add-unit-yaml-copy-btn']");
    assert.ok(btn);
    btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    assert.ok(copied);
    assert.match(copied, /mlss_host:\s*192\.0\.2\.10/);
    assert.match(copied, /enrollment_key:\s*kkk/);
  } finally {
    close();
  }
});


test("add-unit modal: closing clears the rendered enrollment_key from YAML block", async () => {
  const dom = _newDom("admin");
  const fetchFn = async () => new Response(
    JSON.stringify({ key: "secret-key-xyz" }), { status: 200 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
    mlssHost: "mlss.local",
  });
  element.querySelector("[data-testid='add-unit-reveal-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const yaml = element.querySelector("[data-testid='add-unit-yaml']");
  assert.match(yaml.textContent, /secret-key-xyz/);
  close();
  // After close the overlay is detached, but if anything still holds a
  // reference to the block (history, dev tools, etc.) it must not
  // contain the revealed key.
  assert.doesNotMatch(yaml.textContent, /secret-key-xyz/);
  assert.match(yaml.textContent, /REPLACE_ME/);
});


test("add-unit modal: 410 (already revealed) shows Settings link, no key", async () => {
  const dom = _newDom("admin");
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "already_revealed" }),
    { status: 410 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    // Reveal pane hidden, gone pane visible
    const reveal = element.querySelector("[data-testid='add-unit-reveal']");
    assert.equal(reveal.style.display, "none");
    const gone = element.querySelector("[data-testid='add-unit-gone']");
    assert.notEqual(gone.style.display, "none");
    const link = element.querySelector("[data-testid='add-unit-rotate-link']");
    assert.ok(link, "link to Settings is shown so admin can mint a fresh key");
    assert.equal(link.getAttribute("href"), "/grow/settings");
    // Idle row hidden too
    const idleRow = element.querySelector("[data-testid='add-unit-idle']");
    assert.equal(idleRow.style.display, "none");
  } finally {
    close();
  }
});


test("add-unit modal: 403 surfaces inline error without revealing", async () => {
  const dom = _newDom("admin");
  const fetchFn = async () => new Response(
    JSON.stringify({ error: "Forbidden" }),
    { status: 403 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    const reveal = element.querySelector("[data-testid='add-unit-reveal']");
    assert.equal(reveal.style.display, "none");
    const errEl = element.querySelector("[data-testid='add-unit-error']");
    assert.notEqual(errEl.style.display, "none");
    assert.match(errEl.textContent.toLowerCase(), /forbidden|admin/);
    // Reveal button is re-enabled so the user can retry
    const revealBtn = element.querySelector("[data-testid='add-unit-reveal-btn']");
    assert.equal(revealBtn.disabled, false);
  } finally {
    close();
  }
});


test("add-unit modal: copy button copies the revealed key to the clipboard", async () => {
  const dom = _newDom("admin");
  // Hand-rolled clipboard mock — JSDOM doesn't ship one out of the box.
  // The component reads from globalThis.navigator (via `navigator.clipboard`
  // in browser code), so we stub on the JSDOM window and shim global.
  const written = [];
  Object.defineProperty(dom.window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (s) => { written.push(s); } },
  });
  // Component code references `navigator` (lexical), which in Node maps
  // to globalThis.navigator. That's locked in Node 22+, so we override
  // the property descriptor via Object.defineProperty.
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: dom.window.navigator,
  });
  const fetchFn = async () => new Response(
    JSON.stringify({ key: "key-to-copy" }), { status: 200 },
  );
  const { close, element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
  });
  try {
    element.querySelector("[data-testid='add-unit-reveal-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    element.querySelector("[data-testid='add-unit-copy-btn']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();

    assert.deepEqual(written, ["key-to-copy"]);
  } finally {
    close();
  }
});


test("add-unit modal: Done clears the key from the DOM and removes overlay", async () => {
  const dom = _newDom("admin");
  const fetchFn = async () => new Response(
    JSON.stringify({ key: "transient-key" }), { status: 200 },
  );
  const { element } = openAddUnitModal({
    ownerDocument: dom.window.document,
    fetchFn,
  });

  element.querySelector("[data-testid='add-unit-reveal-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();

  const keyInput = element.querySelector("[data-testid='add-unit-key-input']");
  assert.equal(keyInput.value, "transient-key");

  element.querySelector("[data-testid='add-unit-done-btn']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));

  // Key cleared (defence in depth) + overlay removed from the DOM
  assert.equal(keyInput.value, "");
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-unit-overlay']"),
    null,
  );
});


test("add-unit modal: ESC key closes the modal", () => {
  const dom = _newDom("admin");
  openAddUnitModal({ ownerDocument: dom.window.document });
  assert.ok(dom.window.document.querySelector("[data-testid='add-unit-overlay']"));
  // Use the JSDOM constructor so the event has the expected key prop
  const ev = new dom.window.KeyboardEvent("keydown", { key: "Escape" });
  dom.window.document.dispatchEvent(ev);
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-unit-overlay']"),
    null,
  );
});


test("add-unit modal: clicking the dim backdrop closes; clicking the box doesn't", () => {
  const dom = _newDom("admin");
  const { element } = openAddUnitModal({ ownerDocument: dom.window.document });

  // Click on the inner box — modal stays open
  element.querySelector("[data-testid='add-unit-box']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.ok(dom.window.document.querySelector("[data-testid='add-unit-overlay']"),
    "modal stays open when clicking inside the box");

  // Click on the overlay itself — modal closes
  element.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-unit-overlay']"),
    null,
  );
});

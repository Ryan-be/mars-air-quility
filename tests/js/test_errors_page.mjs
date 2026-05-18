/**
 * Tests for the /grow/errors orchestrator (errors.mjs).
 *
 * The orchestrator owns filter state (severity chips, kind dropdown,
 * unresolved-only toggle, refresh button), fetches /api/grow/errors with
 * the right query string on every change, and renders one error-row per
 * result (with an empty-state when zero rows come back).
 *
 * Tests inject a fake fetcher and inspect the JSDOM after each
 * orchestration step.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";


function _newDom(role = "admin") {
  const dom = new JSDOM(`<!doctype html><html><body data-role="${role}">
    <div id="grow-errors-filter"></div>
    <div id="grow-errors-list"></div>
  </body></html>`);
  global.document = dom.window.document;
  global.window = dom.window;
  global.CustomEvent = dom.window.CustomEvent;
  return dom;
}


function _row(overrides = {}) {
  return {
    id: 1,
    unit_id: 1,
    unit_label: "Tomato 1",
    timestamp_utc: "2026-05-06T12:00:00",
    severity: "warning",
    kind: "sensor_degraded",
    message: "msg",
    subject_sensor: null,
    details_json: null,
    resolved_at: null,
    snoozed_until: null,
    ...overrides,
  };
}


/** Cache-bust the module so STATE is reset between tests. */
async function _loadModule() {
  return await import(`../../static/js/grow/errors.mjs?bust=${Math.random()}`);
}


function _flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }


// ─────────────────────────────────────────────────────────────────────
// 1. Initial fetch goes to ?unresolved_only=true&limit=100
// ─────────────────────────────────────────────────────────────────────
test("errors page initial fetch uses ?unresolved_only=true&limit=100", async () => {
  const dom = _newDom("viewer");
  const calls = [];
  const fetcher = async (url) => {
    calls.push(url);
    return new Response(JSON.stringify([]), { status: 200 });
  };
  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();
  assert.equal(calls.length, 1);
  const url = calls[0];
  assert.match(url, /unresolved_only=true/);
  assert.match(url, /limit=100/);
});


// ─────────────────────────────────────────────────────────────────────
// 2. Toggling unresolved-only refetches with ?unresolved_only=false
// ─────────────────────────────────────────────────────────────────────
test("errors page unresolved-toggle off refetches with unresolved_only=false", async () => {
  const dom = _newDom("viewer");
  const calls = [];
  const fetcher = async (url) => {
    calls.push(url);
    return new Response(JSON.stringify([_row()]), { status: 200 });
  };
  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();
  // Initial call
  assert.match(calls[0], /unresolved_only=true/);

  // Flip the toggle
  const cb = dom.window.document.querySelector(
    "[data-testid='grow-errors-unresolved-toggle']",
  );
  assert.ok(cb, "toggle present");
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await _flush();
  await _flush();

  assert.equal(calls.length, 2);
  assert.match(calls[1], /unresolved_only=false/);
});


// ─────────────────────────────────────────────────────────────────────
// 3. Clicking the warning severity chip adds severity=warning to query
// ─────────────────────────────────────────────────────────────────────
test("errors page severity chip click adds severity=warning to qs", async () => {
  const dom = _newDom("viewer");
  const calls = [];
  const fetcher = async (url) => {
    calls.push(url);
    return new Response(JSON.stringify([_row()]), { status: 200 });
  };
  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();

  // Click warning chip
  const chip = dom.window.document.querySelector(
    "[data-testid='grow-errors-sev-chip-warning']",
  );
  assert.ok(chip);
  chip.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flush();
  await _flush();

  assert.equal(calls.length, 2);
  assert.match(calls[1], /severity=warning/);
});


// ─────────────────────────────────────────────────────────────────────
// 4. Renders error-row per result
// ─────────────────────────────────────────────────────────────────────
test("errors page renders one error-row per result", async () => {
  const dom = _newDom("viewer");
  const rows = [
    _row({ id: 1, message: "first" }),
    _row({ id: 2, message: "second" }),
    _row({ id: 3, message: "third" }),
  ];
  const fetcher = async () =>
    new Response(JSON.stringify(rows), { status: 200 });

  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();

  const renderedRows = dom.window.document.querySelectorAll(
    "[data-testid='error-row']",
  );
  assert.equal(renderedRows.length, 3);
  // And the first one's text contains its message
  assert.match(renderedRows[0].textContent, /first/);
});


// ─────────────────────────────────────────────────────────────────────
// 5. Empty-state message when zero results
// ─────────────────────────────────────────────────────────────────────
test("errors page empty-state message when zero results", async () => {
  const dom = _newDom("viewer");
  const fetcher = async () =>
    new Response(JSON.stringify([]), { status: 200 });

  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();

  const empty = dom.window.document.querySelector(
    "[data-testid='grow-errors-empty']",
  );
  assert.ok(empty, "empty-state mounted");
  assert.match(empty.textContent, /No\s+(unresolved\s+)?errors/i);
});


// ─────────────────────────────────────────────────────────────────────
// 6. Kind dropdown contains the unique kinds from the response
// ─────────────────────────────────────────────────────────────────────
test("errors page kind dropdown contains response's unique kinds", async () => {
  const dom = _newDom("viewer");
  const rows = [
    _row({ id: 1, kind: "online" }),
    _row({ id: 2, kind: "offline" }),
    _row({ id: 3, kind: "online" }),     // duplicate; should produce 1 option
    _row({ id: 4, kind: "sensor_degraded" }),
  ];
  const fetcher = async () =>
    new Response(JSON.stringify(rows), { status: 200 });

  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();

  const sel = dom.window.document.querySelector(
    "[data-testid='grow-errors-kind-select']",
  );
  assert.ok(sel);
  const options = Array.from(sel.querySelectorAll("option"))
    .map((o) => o.value)
    .filter(Boolean)
    .sort();
  assert.deepEqual(options, ["offline", "online", "sensor_degraded"]);
});


// ─────────────────────────────────────────────────────────────────────
// 7. Refresh button refetches without changing filter state
// ─────────────────────────────────────────────────────────────────────
test("errors page refresh button refetches", async () => {
  const dom = _newDom("viewer");
  const calls = [];
  const fetcher = async (url) => {
    calls.push(url);
    return new Response(JSON.stringify([]), { status: 200 });
  };
  const mod = await _loadModule();
  mod.resetState();
  mod.boot({ ownerDocument: dom.window.document, fetcher });
  await _flush();
  await _flush();

  const refresh = dom.window.document.querySelector(
    "[data-testid='grow-errors-refresh']",
  );
  assert.ok(refresh);
  refresh.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flush();
  await _flush();
  assert.equal(calls.length, 2);
});

/**
 * Tests for the Diagnostics tab orchestrator (Phase 3 Task 4).
 *
 * The orchestrator is intentionally thin: one fetch to the consolidated
 * /api/grow/units/<id>/diagnostics endpoint, then four child renders.
 * Tests focus on:
 *   1. fetch URL + method (GET) include the unit id
 *   2. all four child sections mount on success
 *   3. fetch failure falls back to "failed to load" + still mounts
 *      danger-zone (so an admin can recover from a broken row)
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderDiagnosticsPanel } from
  "../../static/js/grow/components/diagnostics-panel.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit() {
  return { id: 11, label: "Basil 2" };
}


function _diagBody(overrides = {}) {
  return {
    firmware_version: "0.3.1",
    uptime_s: 3661,
    buffer_size: 0,
    connection_log: [],
    sensor_sanity: [],
    open_errors: [],
    ...overrides,
  };
}


function _ok(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


test("diagnostics panel: fetches the diagnostics endpoint with unit id", async () => {
  const calls = [];
  const fetchFn = async (url, opts) => {
    calls.push({ url, opts });
    return _ok(_diagBody());
  };
  await renderDiagnosticsPanel(_unit(), {
    ownerDocument: document, fetchFn,
  });
  // Find the diagnostics call (the danger-zone token-rotator + others
  // don't fire on render; only the orchestrator's fetch should land).
  const diagCall = calls.find((c) =>
    String(c.url).includes("/diagnostics"));
  assert.ok(diagCall, "diagnostics endpoint was fetched");
  assert.match(String(diagCall.url),
    /\/api\/grow\/units\/11\/diagnostics/);
  // Default GET (no method override needed)
  assert.ok(!diagCall.opts || !diagCall.opts.method
    || diagCall.opts.method === "GET");
});


test("diagnostics panel: renders all five child sections on success", async () => {
  const fetchFn = async () => _ok(_diagBody({
    firmware_version: "1.2.3",
    uptime_s: 100,
    buffer_size: 5,
    connection_log: [
      { id: 2, timestamp_utc: "2026-05-06T12:00:00", kind: "online", resolved_at: null },
    ],
    sensor_sanity: [
      { channel: "soil_moisture", last_seen_at: "2026-05-06T12:00:00",
        minutes_ago: 0.5, is_stale: false, stale_threshold_min: 5 },
    ],
  }));
  const el = await renderDiagnosticsPanel(_unit(), {
    ownerDocument: document, fetchFn,
  });
  assert.equal(el.dataset.testid, "diagnostics-panel");
  assert.ok(el.querySelector("[data-testid='diag-firmware']"),
    "firmware-info child present");
  assert.ok(el.querySelector("[data-testid='diag-buffer-inspector']"),
    "buffer-inspector child present");
  assert.ok(el.querySelector("[data-testid='diag-connection-log']"),
    "connection-log child present");
  assert.ok(el.querySelector("[data-testid='diag-sensor-sanity']"),
    "sensor-sanity child present");
  assert.ok(el.querySelector("[data-testid='diag-danger-zone']"),
    "danger-zone child present");
});


test("diagnostics panel: handles fetch failure gracefully", async () => {
  const fetchFn = async () =>
    new Response(JSON.stringify({ error: "server_down" }), { status: 500 });
  const el = await renderDiagnosticsPanel(_unit(), {
    ownerDocument: document, fetchFn,
  });
  const err = el.querySelector("[data-testid='diag-error']");
  assert.ok(err, "error placeholder rendered");
  assert.match(err.textContent, /failed to load/i);
  // Even on fetch failure, danger-zone still mounts so an admin can
  // recover (decommission a broken row, etc).
  assert.ok(el.querySelector("[data-testid='diag-danger-zone']"),
    "danger-zone still mounts on fetch failure");
  // The success-path children should NOT be present.
  assert.equal(el.querySelector("[data-testid='diag-firmware']"), null);
  assert.equal(el.querySelector("[data-testid='diag-connection-log']"), null);
});


test("diagnostics panel: fetch network exception → error message + danger-zone fallback",
async () => {
  const fetchFn = async () => { throw new Error("network down"); };
  const el = await renderDiagnosticsPanel(_unit(), {
    ownerDocument: document, fetchFn,
  });
  const err = el.querySelector("[data-testid='diag-error']");
  assert.ok(err);
  assert.match(err.textContent, /network down/);
  assert.ok(el.querySelector("[data-testid='diag-danger-zone']"));
});

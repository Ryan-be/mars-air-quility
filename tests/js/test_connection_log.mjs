/**
 * Tests for the connection-log table — second section of the Diagnostics tab.
 *
 * Tests:
 *   - rows render in id-DESC (newest first) order received from server
 *   - offline-online pairing computes "duration offline" gap correctly
 *   - unresolved offline (no later online) shows "ongoing" badge
 *   - _pairOfflineToOnline pure helper coverage
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderConnectionLog, _pairOfflineToOnline } from
  "../../static/js/grow/components/connection-log.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("connection log: renders rows in the order received (newest first)", () => {
  // Server hands us id-DESC. We render in that order so the most recent
  // event sits at the top.
  const log = [
    { id: 5, timestamp_utc: "2026-05-06T14:32:01", kind: "online", resolved_at: null },
    { id: 4, timestamp_utc: "2026-05-06T14:20:00", kind: "offline", resolved_at: null },
    { id: 3, timestamp_utc: "2026-05-06T13:00:00", kind: "online", resolved_at: null },
  ];
  const el = renderConnectionLog(log, { ownerDocument: document });
  const rows = el.querySelectorAll("tbody tr");
  assert.equal(rows.length, 3);
  // First row in render order is id=5 (newest).
  assert.equal(rows[0].dataset.testid, "conn-row-5");
  assert.equal(rows[1].dataset.testid, "conn-row-4");
  assert.equal(rows[2].dataset.testid, "conn-row-3");
});


test("connection log: pairs offline with resolving online for duration",
() => {
  // offline at 14:20, online at 14:32 → 12 minute gap.
  const log = [
    { id: 5, timestamp_utc: "2026-05-06T14:32:00Z", kind: "online", resolved_at: null },
    { id: 4, timestamp_utc: "2026-05-06T14:20:00Z", kind: "offline", resolved_at: null },
  ];
  const el = renderConnectionLog(log, { ownerDocument: document });
  const dur = el.querySelector("[data-testid='conn-row-4-duration']");
  assert.equal(dur.textContent, "12m");
});


test("connection log: handles unresolved offline → 'ongoing' badge",
() => {
  // Just an offline at 14:20 with no later online — render with
  // "ongoing" instead of a duration.
  const log = [
    { id: 4, timestamp_utc: "2026-05-06T14:20:00Z", kind: "offline", resolved_at: null },
  ];
  const el = renderConnectionLog(log, { ownerDocument: document });
  const dur = el.querySelector("[data-testid='conn-row-4-duration']");
  assert.match(dur.textContent.toLowerCase(), /ongoing/);
});


test("connection log: online row's duration cell is em-dash", () => {
  // The "duration offline" only meaningful for offline rows; online
  // rows get a placeholder so the column stays balanced visually.
  const log = [
    { id: 5, timestamp_utc: "2026-05-06T14:32:00Z", kind: "online", resolved_at: null },
  ];
  const el = renderConnectionLog(log, { ownerDocument: document });
  const dur = el.querySelector("[data-testid='conn-row-5-duration']");
  assert.equal(dur.textContent, "—");
});


test("connection log: empty log shows 'no events' message", () => {
  const el = renderConnectionLog([], { ownerDocument: document });
  // No table — just an empty placeholder.
  assert.equal(el.querySelector("table"), null);
  const empty = el.querySelector(".diag-empty");
  assert.ok(empty);
  assert.match(empty.textContent.toLowerCase(), /no.*events/);
});


test("_pairOfflineToOnline: simple offline→online pair", () => {
  const log = [
    { id: 2, timestamp_utc: "2026-05-06T14:32:00Z", kind: "online" },
    { id: 1, timestamp_utc: "2026-05-06T14:20:00Z", kind: "offline" },
  ];
  const pairs = _pairOfflineToOnline(log);
  const resolver = pairs.get(1);
  assert.ok(resolver, "offline id=1 has a resolver");
  assert.equal(resolver.id, 2);
});


test("_pairOfflineToOnline: unresolved offline has null resolver", () => {
  const log = [
    { id: 1, timestamp_utc: "2026-05-06T14:20:00Z", kind: "offline" },
  ];
  const pairs = _pairOfflineToOnline(log);
  assert.equal(pairs.get(1), null);
});


test("_pairOfflineToOnline: online rows aren't keyed in the map", () => {
  // The map keys offline rows; online rows are values (or absent).
  const log = [
    { id: 2, timestamp_utc: "2026-05-06T14:32:00Z", kind: "online" },
    { id: 1, timestamp_utc: "2026-05-06T14:20:00Z", kind: "offline" },
  ];
  const pairs = _pairOfflineToOnline(log);
  assert.equal(pairs.has(2), false, "online rows are not keys");
  assert.equal(pairs.has(1), true, "offline rows are keys");
});

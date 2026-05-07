/**
 * Tests for the sensor-sanity list — third section of the Diagnostics tab.
 *
 * Three icon classes (✅ fresh / ⚠ stale / 🔌 never-seen) driven by
 * is_stale + last_seen_at. Pure render — orchestrator hands us the slice.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderSensorSanity, _classifySensor } from
  "../../static/js/grow/components/sensor-sanity.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("sensor sanity: renders ✅ icon for fresh sensor", () => {
  const sanity = [
    { channel: "soil_moisture", last_seen_at: "2026-05-06T14:00:00Z",
      minutes_ago: 0.5, is_stale: false, stale_threshold_min: 5 },
  ];
  const el = renderSensorSanity(sanity, { ownerDocument: document });
  const row = el.querySelector("[data-testid='sanity-soil_moisture']");
  assert.ok(row);
  assert.equal(row.dataset.severity, "ok");
  const icon = row.querySelector(".diag-sanity-icon");
  assert.match(icon.textContent, /✅/);
  // Detail text mentions the staleness window
  assert.match(row.textContent, /0\.5/);
  assert.match(row.textContent, /min ago/);
});


test("sensor sanity: renders ⚠ icon for stale sensor", () => {
  const sanity = [
    { channel: "ambient_lux", last_seen_at: "2026-05-06T14:00:00Z",
      minutes_ago: 10.2, is_stale: true, stale_threshold_min: 5 },
  ];
  const el = renderSensorSanity(sanity, { ownerDocument: document });
  const row = el.querySelector("[data-testid='sanity-ambient_lux']");
  assert.ok(row);
  assert.equal(row.dataset.severity, "stale");
  const icon = row.querySelector(".diag-sanity-icon");
  assert.match(icon.textContent, /⚠/);
  // The stale row must also include the threshold for context
  assert.match(row.textContent, /threshold 5/);
  assert.match(row.textContent, /STALE/);
});


test("sensor sanity: renders 🔌 icon for never-seen sensor", () => {
  const sanity = [
    { channel: "air_temp_c", last_seen_at: null,
      minutes_ago: null, is_stale: true, stale_threshold_min: 5 },
  ];
  const el = renderSensorSanity(sanity, { ownerDocument: document });
  const row = el.querySelector("[data-testid='sanity-air_temp_c']");
  assert.ok(row);
  assert.equal(row.dataset.severity, "never_seen");
  const icon = row.querySelector(".diag-sanity-icon");
  assert.match(icon.textContent, /🔌/);
  assert.match(row.textContent.toLowerCase(), /never seen/);
});


test("sensor sanity: empty list renders placeholder", () => {
  const el = renderSensorSanity([], { ownerDocument: document });
  const empty = el.querySelector(".diag-empty");
  assert.ok(empty);
  assert.match(empty.textContent.toLowerCase(),
    /no capabilities|never reported/);
});


test("_classifySensor: pure helper returns the right tuple", () => {
  // Fresh
  assert.deepEqual(
    _classifySensor({
      channel: "x", last_seen_at: "2026-05-06T14:00:00Z",
      minutes_ago: 0.5, is_stale: false,
    }),
    { icon: "✅", label: "fresh", severity: "ok" },
  );
  // Stale
  assert.deepEqual(
    _classifySensor({
      channel: "x", last_seen_at: "2026-05-06T14:00:00Z",
      minutes_ago: 12, is_stale: true,
    }),
    { icon: "⚠", label: "STALE", severity: "stale" },
  );
  // Never seen
  assert.deepEqual(
    _classifySensor({
      channel: "x", last_seen_at: null, minutes_ago: null, is_stale: true,
    }),
    { icon: "🔌", label: "never seen", severity: "never_seen" },
  );
});

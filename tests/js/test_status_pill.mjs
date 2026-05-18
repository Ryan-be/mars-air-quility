import { test } from "node:test";
import assert from "node:assert/strict";
import { renderStatusPill, classifyUnitStatus } from "../../static/js/grow/components/status-pill.mjs";


test("classifyUnitStatus: online when last seen recently", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:59:50Z");  // 10s ago
  assert.equal(classifyUnitStatus(lastSeen, now), "online");
});

test("classifyUnitStatus: stale between 30s and 5min", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:58:00Z");  // 2min ago
  assert.equal(classifyUnitStatus(lastSeen, now), "stale");
});

test("classifyUnitStatus: offline after 5min", () => {
  const now = new Date("2026-05-03T12:00:00Z");
  const lastSeen = new Date("2026-05-03T11:50:00Z");  // 10min ago
  assert.equal(classifyUnitStatus(lastSeen, now), "offline");
});

test("classifyUnitStatus: offline when null", () => {
  assert.equal(classifyUnitStatus(null, new Date()), "offline");
});

test("renderStatusPill: returns HTML element with correct class", () => {
  const el = renderStatusPill("online");
  assert.equal(el.tagName, "SPAN");
  assert.match(el.className, /st-normal/);
  assert.match(el.textContent, /Nominal/i);
});

test("renderStatusPill: caution status", () => {
  const el = renderStatusPill("caution");
  assert.match(el.className, /st-caution/);
  assert.match(el.textContent, /Caution/i);
});

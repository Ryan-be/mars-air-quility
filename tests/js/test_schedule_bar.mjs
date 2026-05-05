import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderScheduleBar, computeOnSegments } from "../../static/js/grow/components/schedule-bar.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("computeOnSegments: single window 06:00-22:00 → one segment 25%-91.67%", () => {
  const segs = computeOnSegments([{ start: "06:00", end: "22:00" }]);
  assert.equal(segs.length, 1);
  assert.equal(Math.round(segs[0].leftPct * 10000), 2500);   // 6/24
  assert.equal(Math.round(segs[0].widthPct * 10000), 6667);  // 16/24
});


test("computeOnSegments: overnight window 22:00-06:00 → two segments", () => {
  const segs = computeOnSegments([{ start: "22:00", end: "06:00" }]);
  assert.equal(segs.length, 2);
});


test("computeOnSegments: empty → no segments", () => {
  assert.deepEqual(computeOnSegments([]), []);
});


test("renderScheduleBar shows 'NOW' indicator at correct position", () => {
  const now = new Date("2026-05-03T12:00:00Z");  // 50% of day
  const el = renderScheduleBar([{ start: "06:00", end: "22:00" }], now, document);
  const nowMarker = el.querySelector(".du-schedule-now");
  assert.ok(nowMarker);
  // Left position should be roughly 50%
  assert.match(nowMarker.style.left, /5[0-1]/);
});

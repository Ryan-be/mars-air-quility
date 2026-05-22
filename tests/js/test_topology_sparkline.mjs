/**
 * Tests for the topology sparkline primitive (Phase 6 Task 6.1).
 *
 * Port of `docs/assets/effector-map-handoff/nodes.jsx::Sparkline`:
 * a tiny inline SVG line chart. Renders a `<polyline>` whose points
 * map linearly across the value range — no axes, no labels, just the
 * trend line. Used inside hub + grow cards for the last 30 readings.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderSparkline } from "../../static/js/topology/components/sparkline.mjs";


function _newDom() {
  return new JSDOM("<!doctype html><html><body></body></html>");
}


test("sparkline: renders an SVG with class tp-spark", () => {
  const dom = _newDom();
  const svg = renderSparkline({
    values: [1, 2, 3], color: "#0f0", height: 24,
    ownerDocument: dom.window.document,
  });
  assert.equal(svg.tagName.toLowerCase(), "svg");
  assert.ok(svg.classList.contains("tp-spark"));
});


test("sparkline: emits a single <polyline> with points spanning the value range", () => {
  const dom = _newDom();
  const svg = renderSparkline({
    values: [1, 2, 3], color: "#0f0", height: 24,
    ownerDocument: dom.window.document,
  });
  const polylines = svg.querySelectorAll("polyline");
  assert.equal(polylines.length, 1, "expected exactly one <polyline>");
  // 3 values → 3 points "x,y x,y x,y".
  const pts = polylines[0].getAttribute("points").trim().split(/\s+/);
  assert.equal(pts.length, 3);
  // First point's x = 0, last point's x = viewBox width (100).
  const [firstX] = pts[0].split(",").map(Number);
  const [lastX] = pts[pts.length - 1].split(",").map(Number);
  assert.equal(firstX, 0);
  assert.equal(lastX, 100);
});


test("sparkline: returns null-ish for <2 values (nothing to plot)", () => {
  const dom = _newDom();
  // Single-element series can't make a line — the renderer returns an
  // empty placeholder span so the caller can still append it without
  // a null-check.
  const out = renderSparkline({
    values: [42], color: "#0f0",
    ownerDocument: dom.window.document,
  });
  assert.ok(out, "should return a node, not undefined");
  assert.equal(out.querySelectorAll("polyline").length, 0,
    "no polyline should be drawn for a single value");
});

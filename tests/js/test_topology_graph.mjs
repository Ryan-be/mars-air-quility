/**
 * Tests for the topology graph rendering primitives (Phase 5 Tasks
 * 5.2 + 5.3 + 5.4 + 5.5 + 5.6).
 *
 * The pure maths (`edgePath`, `anchorOn`, `edgeColorFor`) are simple
 * deterministic functions — no DOM needed. The `renderGraph`,
 * `setupPan`, `setupZoom`, and `setupNodeDrag` helpers are exercised
 * under JSDOM the same way Phase 4's boot test does it.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import {
  edgePath,
  anchorOn,
  edgeColorFor,
} from "../../static/js/topology/graph.mjs";


test("edgePath: straight produces M…L…", () => {
  const d = edgePath({ x: 0, y: 0 }, { x: 100, y: 0 }, "straight");
  // M and L coordinates appear in the path string with the input values
  // intact (no transforms applied — pure passthrough at this layer).
  assert.match(d, /^M0 0 L100 0$/);
});


test("edgePath: bezier produces M…C…", () => {
  const d = edgePath({ x: 0, y: 0 }, { x: 200, y: 0 }, "bezier");
  // Cubic Bézier emits one "C" command with three control coords.
  assert.match(d, /^M0 0 C/);
});


test("anchorOn: leftward target exits the node's left side", () => {
  // Node centred at origin, 200×100 box (half=100,50). Target is
  // straight left → ray exits left edge, x = -100.
  const anchor = anchorOn({ x: 0, y: 0 }, { x: -100, y: 0 }, 100, 50);
  assert.equal(anchor.x, -100);
});


test("edgeColorFor: hub→on-effector returns the on-state colour token", () => {
  const colour = edgeColorFor(
    { kind: "hub" },
    { kind: "effector", state: "on", mode: "on" },
  );
  // Per plan §5.2 we use the existing --color-status-normal token
  // (green) so the edge tracks the same colour as the on-state pill.
  assert.match(colour, /color-status-normal/);
});


test("edgeColorFor: hub→off-effector returns the off-state colour token", () => {
  const colour = edgeColorFor(
    { kind: "hub" },
    { kind: "effector", state: "off", mode: "off" },
  );
  // Off uses --color-status-off (grey) — same token the bottom-row
  // dot in base.css uses for offline subsystems.
  assert.match(colour, /color-status-off/);
});


test("edgeColorFor: hub→grow uses standby blue", () => {
  const colour = edgeColorFor(
    { kind: "hub" },
    { kind: "grow" },
  );
  // The hub→grow wire is the standard blue from the topology spec.
  assert.match(colour, /color-status-standby/);
});


// ─── Task 5.3 — renderGraph ───────────────────────────────────────────


test("renderGraph: a hub + one effector produces exactly one tp-edge path", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  const { renderGraph } = await import("../../static/js/topology/graph.mjs");
  const nodes = [
    { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    {
      id: "effector:1",
      kind: "effector",
      parent: "hub",
      label: "Room fan",
      effector_type: "fan",
      mode: "auto",
      current_state: "off",
    },
  ];
  const positions = {
    hub: { x: 0, y: 0 },
    "effector:1": { x: 0, y: -320 },
  };
  const wrap = renderGraph({
    nodes,
    positions,
    viewport: { x: 0, y: 0, k: 1 },
    ownerDocument: dom.window.document,
  });
  const edges = wrap.querySelectorAll("path.tp-edge");
  assert.equal(edges.length, 1, "expected exactly 1 edge path");
  // The node layer should also exist with two placeholder divs.
  const nodeDivs = wrap.querySelectorAll(".tp-node");
  assert.equal(nodeDivs.length, 2, "expected 2 node divs (hub + effector)");
});


test("renderGraph: applies viewport via inner transform", async () => {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  const { renderGraph } = await import("../../static/js/topology/graph.mjs");
  const wrap = renderGraph({
    nodes: [{ id: "hub", kind: "hub", sensors: {} }],
    positions: { hub: { x: 0, y: 0 } },
    viewport: { x: 100, y: 200, k: 0.5 },
    ownerDocument: dom.window.document,
  });
  // renderGraph returns the .tp-graph-inner element directly — the
  // caller is responsible for mounting it inside the host. The
  // transform sits on the returned element itself.
  assert.ok(wrap.classList.contains("tp-graph-inner"),
    "returned element should be the .tp-graph-inner wrapper");
  assert.match(wrap.style.transform, /translate\(100px,\s*200px\)/);
  assert.match(wrap.style.transform, /scale\(0\.5\)/);
});


// ─── Task 5.4 — setupPan ───────────────────────────────────────────────


test("setupPan: mousedown→move→up on the SVG fires onChange with delta", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div id="wrap"><svg class="tp-graph-svg"></svg></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupPan } = await import("../../static/js/topology/graph.mjs");
  const wrapEl = dom.window.document.getElementById("wrap");
  let viewport = { x: 0, y: 0, k: 1 };
  const calls = [];
  setupPan({
    wrapEl,
    getViewport: () => viewport,
    onChange: (vp) => {
      viewport = vp;
      calls.push(vp);
    },
  });
  // Dispatch mousedown on the SVG (panning target).
  const svg = wrapEl.querySelector("svg.tp-graph-svg");
  svg.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, clientX: 50, clientY: 50, button: 0,
  }));
  // Move 30px right + 20px down — well over the 2px click threshold.
  dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
    bubbles: true, clientX: 80, clientY: 70,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, clientX: 80, clientY: 70,
  }));
  assert.ok(calls.length >= 1, "onChange should fire at least once");
  const last = calls[calls.length - 1];
  // Pan delta should be added to the starting viewport — so the new
  // viewport.x ends near +30, y near +20.
  assert.equal(last.x, 30);
  assert.equal(last.y, 20);
});


test("setupPan: <2px movement is treated as a click (no onChange)", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div id="wrap"><svg class="tp-graph-svg"></svg></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupPan } = await import("../../static/js/topology/graph.mjs");
  const wrapEl = dom.window.document.getElementById("wrap");
  const calls = [];
  setupPan({
    wrapEl,
    getViewport: () => ({ x: 0, y: 0, k: 1 }),
    onChange: (vp) => calls.push(vp),
  });
  const svg = wrapEl.querySelector("svg.tp-graph-svg");
  svg.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, clientX: 50, clientY: 50, button: 0,
  }));
  // Move 1px — treated as click, no onChange.
  dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
    bubbles: true, clientX: 51, clientY: 50,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, clientX: 51, clientY: 50,
  }));
  assert.equal(calls.length, 0, "click-not-drag should fire NO onChange");
});


// ─── Task 5.5 — setupZoom ──────────────────────────────────────────────


test("setupZoom: 10 wheel-down events clamp k at 0.3", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div id="wrap" style="width: 800px; height: 600px;">` +
    `<svg class="tp-graph-svg"></svg></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupZoom } = await import("../../static/js/topology/graph.mjs");
  const wrapEl = dom.window.document.getElementById("wrap");
  let viewport = { x: 400, y: 300, k: 1 };
  setupZoom({
    wrapEl,
    getViewport: () => viewport,
    onChange: (vp) => { viewport = vp; },
  });
  // 10 successive zoom-OUT events (positive deltaY) should clamp at 0.3.
  for (let i = 0; i < 10; i++) {
    wrapEl.dispatchEvent(new dom.window.WheelEvent("wheel", {
      bubbles: true, cancelable: true,
      deltaY: 1000, clientX: 400, clientY: 300,
    }));
  }
  assert.ok(
    viewport.k >= 0.299 && viewport.k <= 0.301,
    `expected k clamped to 0.3, got ${viewport.k}`,
  );
});


test("setupZoom: 10 wheel-up events clamp k at 2.5", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div id="wrap" style="width: 800px; height: 600px;">` +
    `<svg class="tp-graph-svg"></svg></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupZoom } = await import("../../static/js/topology/graph.mjs");
  const wrapEl = dom.window.document.getElementById("wrap");
  let viewport = { x: 400, y: 300, k: 1 };
  setupZoom({
    wrapEl,
    getViewport: () => viewport,
    onChange: (vp) => { viewport = vp; },
  });
  for (let i = 0; i < 10; i++) {
    wrapEl.dispatchEvent(new dom.window.WheelEvent("wheel", {
      bubbles: true, cancelable: true,
      deltaY: -1000, clientX: 400, clientY: 300,
    }));
  }
  assert.ok(
    viewport.k >= 2.499 && viewport.k <= 2.501,
    `expected k clamped to 2.5, got ${viewport.k}`,
  );
});


// ─── Task 5.6 — setupNodeDrag ─────────────────────────────────────────


test("setupNodeDrag: dragging updates position by delta/viewport.k", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div class="tp-node" id="node" data-node-id="grow:1"></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupNodeDrag } = await import("../../static/js/topology/graph.mjs");
  const nodeEl = dom.window.document.getElementById("node");
  const startPos = { x: 100, y: 200 };
  let updatedPos = null;
  setupNodeDrag({
    nodeEl,
    nodeId: "grow:1",
    getPos: () => startPos,
    getViewport: () => ({ x: 0, y: 0, k: 2 }), // k=2 → delta halved
    onChange: (id, pos) => { updatedPos = pos; },
    onClick: () => {},
  });
  nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, clientX: 0, clientY: 0, button: 0,
  }));
  // Move 40px right, 20px down. With viewport.k=2 the world delta is
  // (20, 10) — half the screen delta.
  dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
    bubbles: true, clientX: 40, clientY: 20,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, clientX: 40, clientY: 20,
  }));
  assert.ok(updatedPos, "onChange should have fired");
  assert.equal(updatedPos.x, 120, "x should be 100 + 40/2 = 120");
  assert.equal(updatedPos.y, 210, "y should be 200 + 20/2 = 210");
});


test("setupNodeDrag: <2px movement triggers onClick(nodeId) instead of onChange", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body>" +
    `<div class="tp-node" id="node" data-node-id="grow:1"></div>` +
    "</body></html>",
  );
  global.window = dom.window;
  global.document = dom.window.document;
  const { setupNodeDrag } = await import("../../static/js/topology/graph.mjs");
  const nodeEl = dom.window.document.getElementById("node");
  let clickedId = null;
  let changeCalls = 0;
  setupNodeDrag({
    nodeEl,
    nodeId: "grow:1",
    getPos: () => ({ x: 0, y: 0 }),
    getViewport: () => ({ x: 0, y: 0, k: 1 }),
    onChange: () => { changeCalls += 1; },
    onClick: (id) => { clickedId = id; },
  });
  nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, clientX: 50, clientY: 50, button: 0,
  }));
  // 1px move — under the 2px click threshold.
  dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
    bubbles: true, clientX: 51, clientY: 50,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, clientX: 51, clientY: 50,
  }));
  assert.equal(clickedId, "grow:1", "onClick should fire with nodeId");
  assert.equal(changeCalls, 0, "onChange should NOT fire on click");
});

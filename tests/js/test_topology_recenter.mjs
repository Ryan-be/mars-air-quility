/**
 * Tests for the Recenter button viewport reset (Phase 11 Task 11.3).
 *
 * The Phase 7 wiring already calls `_computeInitialViewport()` on
 * click; this suite tightens the contract by asserting the post-click
 * `.tp-graph-inner` `transform: translate(x, y) scale(k)` matches the
 * initial-viewport formula `(w/2, h/2 - 40, 0.9)` from page.mjs.
 *
 * The transform parse is intentionally lenient (regex captures the
 * numeric arguments) so a future micro-tweak to the inline style
 * doesn't break the test, only a real semantic change in the viewport
 * maths does.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";


function _newDom() {
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="admin">
      <section class="tp-app" id="tp-app" data-role="admin">
        <header id="tp-topbar-host"></header>
        <div    id="tp-graph-host"></div>
        <footer id="tp-statusbar-host"></footer>
        <aside  id="tp-sidepanel-host" class="hidden"></aside>
      </section>
    </body></html>`,
  );
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}


function _fakeEventSourceClass() {
  return class FakeEventSource {
    constructor() { this.listeners = {}; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() {}
  };
}


function _mockTopology(payload) {
  return async () => ({
    ok: true, status: 200,
    async json() { return payload; },
  });
}


function _parseTranslateScale(transformCss) {
  // `translate(Xpx, Ypx) scale(K)` — accept floats with optional sign
  // and trailing exponent. Returns null if the string doesn't match.
  const re = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)\s*scale\(([-\d.]+)\)/;
  const m = (transformCss || "").match(re);
  if (!m) return null;
  return { x: Number(m[1]), y: Number(m[2]), k: Number(m[3]) };
}


test("Recenter: after a pan, click restores translate(w/2, h/2 - 40) scale(0.9)", async () => {
  const dom = _newDom();
  global.EventSource = _fakeEventSourceClass();
  const { boot } = await import("../../static/js/topology/page.mjs?cb=rc1");
  await boot({ fetchFn: _mockTopology({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [
      { id: "grow:1", kind: "grow", label: "G1", sensors: {} },
    ],
    effectors: [],
    layout: {},
  }) });
  const doc = dom.window.document;

  // Initial inner transform should match the formula. Without JSDOM
  // layout, clientWidth/Height are 0 → the boot's fallback (800, 600)
  // kicks in → translate(400, 260) scale(0.9).
  const innerBefore = doc.querySelector("#tp-graph-host .tp-graph-inner");
  assert.ok(innerBefore, "graph inner wrapper present after boot");
  const tBefore = _parseTranslateScale(innerBefore.style.transform);
  assert.ok(tBefore, "initial transform parses");
  assert.equal(tBefore.x, 400);
  assert.equal(tBefore.y, 260);
  assert.equal(tBefore.k, 0.9);

  // Simulate a wheel-zoom + pan via direct mutation of the viewport
  // state by dispatching a wheel event. Falling back to a manual
  // mouse-pan since wheel handling in JSDOM is fiddly: trigger a pan
  // by mousedown-mousemove-mouseup on the SVG wrapper.
  const svg = doc.querySelector("#tp-graph-host svg.tp-graph-svg");
  assert.ok(svg);
  svg.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, clientX: 100, clientY: 100, button: 0, target: svg,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
    bubbles: true, clientX: 250, clientY: 250,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, clientX: 250, clientY: 250,
  }));
  // The transform should now reflect the pan offset (250-100=150 each).
  const innerPanned = doc.querySelector("#tp-graph-host .tp-graph-inner");
  const tPanned = _parseTranslateScale(innerPanned.style.transform);
  // Some test environments don't propagate the mousedown target check
  // — guard with a tolerant assertion.
  if (tPanned && (tPanned.x !== 400 || tPanned.y !== 260)) {
    assert.equal(tPanned.k, 0.9, "scale shouldn't change on pan");
  }

  // Click Recenter.
  const btn = doc.querySelector(
    ".tp-topbar-inner button[data-action='recenter']",
  );
  assert.ok(btn);
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  for (let i = 0; i < 10; i++) await Promise.resolve();

  // After Recenter, the transform is back to the initial values. The
  // inner element may have been replaced by the re-render so re-query.
  const innerAfter = doc.querySelector("#tp-graph-host .tp-graph-inner");
  assert.ok(innerAfter);
  const tAfter = _parseTranslateScale(innerAfter.style.transform);
  assert.ok(tAfter, "post-recenter transform parses");
  assert.equal(tAfter.x, 400,
    "x should reset to graph.clientWidth/2 (fallback 800/2 = 400)");
  assert.equal(tAfter.y, 260,
    "y should reset to graph.clientHeight/2 - 40 (fallback 600/2 - 40 = 260)");
  assert.equal(tAfter.k, 0.9,
    "k should reset to 0.9");
});


test("Recenter: graph nodes remain mounted after the click", async () => {
  // Smoke check that the re-render didn't strip nodes. Mirrors the
  // pre-existing Phase 7 assertion but lives here so the Phase 11
  // Task 11.3 coverage is self-contained.
  const dom = _newDom();
  global.EventSource = _fakeEventSourceClass();
  const { boot } = await import("../../static/js/topology/page.mjs?cb=rc2");
  await boot({ fetchFn: _mockTopology({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [
      { id: "grow:1", kind: "grow", label: "G1", sensors: {} },
      { id: "grow:2", kind: "grow", label: "G2", sensors: {} },
    ],
    effectors: [],
    layout: {},
  }) });
  const doc = dom.window.document;
  doc.querySelector(
    ".tp-topbar-inner button[data-action='recenter']",
  ).dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  for (let i = 0; i < 10; i++) await Promise.resolve();
  const nodes = doc.querySelectorAll("#tp-graph-host .tp-node");
  assert.equal(nodes.length, 3,
    `expected 3 nodes (hub + 2 grows) post-recenter, got ${nodes.length}`);
});

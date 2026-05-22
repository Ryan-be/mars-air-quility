/**
 * Tests for the topology side panel (Phase 8).
 *
 * The side panel is the slide-out config surface anchored to the right
 * edge of the /controls viewport. Clicking a node in the graph (or the
 * admin cog on an effector card) opens the panel populated with the
 * selected node's configuration. The panel switches its rendered content
 * on `node.kind` (hub / grow / effector).
 *
 * Task 8.1 covers the open/close mechanics + the close × button.
 * Tasks 8.2-8.5 cover the per-node-kind sections (asserted below in
 * separate test() blocks once those tasks land).
 *
 * The panel host (#tp-sidepanel-host) is already in the page scaffold —
 * `renderSidePanel({...})` returns a fresh <aside class="tp-sidepanel">
 * element which the page boot mounts (replaces children of the host)
 * on each selection change.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderSidePanel } from
  "../../static/js/topology/components/side-panel.mjs";
import { boot } from "../../static/js/topology/page.mjs";


function _newDom() {
  return new JSDOM(`<!doctype html><html><body data-role="admin"></body></html>`);
}


const sampleEffector = {
  id: "effector:7",
  kind: "effector",
  parent: "hub",
  label: "Room fan",
  effector_type: "fan",
  mode: "auto",
  current_state: "off",
  is_enabled: 1,
};


const sampleHub = {
  id: "hub",
  kind: "hub",
  label: "MLSS Hub",
  sub: "central coordinator",
  sensors: { temp: 22.5, rh: 55, co2: 700 },
  notes: "Whole-room sensors. Coordinates room-level effectors.",
};


const sampleGrow = {
  id: "grow:1",
  kind: "grow",
  parent: "hub",
  label: "Grow #1",
  plant_type: "tomato",
  phase: "vegetative",
  medium: "soil",
  sensors: {
    soil_moisture: 60,
    soil_temp_c: 21,
    air_temp_c: 22,
    air_humidity_pct: 55,
  },
};


// ─── Task 8.1 — open/close + selected-node state ───────────────────────


test("side panel: with node=null returns aside.tp-sidepanel.hidden", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: null, allNodes: [], doc: dom.window.document,
    isAdmin: false, callbacks: {},
  });
  assert.equal(el.tagName, "ASIDE");
  assert.ok(el.classList.contains("tp-sidepanel"),
    "panel always has .tp-sidepanel class");
  assert.ok(el.classList.contains("hidden"),
    "panel is hidden when no node is selected");
});


test("side panel: with a node returns aside.tp-sidepanel (visible)", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  assert.equal(el.tagName, "ASIDE");
  assert.ok(el.classList.contains("tp-sidepanel"));
  assert.ok(!el.classList.contains("hidden"),
    "panel is visible when a node is selected");
});


test("side panel: close × button fires onClose() callback", () => {
  const dom = _newDom();
  let closeCalls = 0;
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true,
    callbacks: { onClose: () => { closeCalls += 1; } },
  });
  const closeBtn = el.querySelector("[data-testid='tp-sidepanel-close']");
  assert.ok(closeBtn, "close × button is present");
  closeBtn.dispatchEvent(
    new dom.window.MouseEvent("click", { bubbles: true }),
  );
  assert.equal(closeCalls, 1, "onClose fires once per click");
});


test("side panel: header shows the node label", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  assert.match(el.textContent, /Room fan/,
    "panel header surfaces the selected node's label");
});


// ─── Task 8.1 wiring — boot mounts side panel into #tp-sidepanel-host ──


function _bootDom(role = "admin") {
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="${role}">
      <section class="tp-app" id="tp-app" data-role="${role}">
        <header id="tp-topbar-host"></header>
        <div    id="tp-graph-host"></div>
        <footer id="tp-statusbar-host"></footer>
        <aside  id="tp-sidepanel-host" class="hidden"></aside>
      </section>
    </body></html>`,
  );
  global.document = dom.window.document;
  global.window = dom.window;
  global.EventSource = class { constructor() {} addEventListener() {} close() {} };
  return dom;
}


function _mockFetch(payload) {
  return async () => ({
    ok: true, status: 200,
    async json() { return payload; },
  });
}


test("boot: initial mount paints the panel host as hidden shell", async () => {
  const dom = _bootDom();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [], effectors: [], layout: {},
  }) });
  const host = dom.window.document.getElementById("tp-sidepanel-host");
  const aside = host.querySelector("aside.tp-sidepanel");
  assert.ok(aside, "panel <aside> is mounted into the host");
  assert.ok(aside.classList.contains("hidden"),
    "panel is hidden when nothing is selected");
});


test("boot: clicking a node opens the side panel populated with that node", async () => {
  const dom = _bootDom();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:7", kind: "effector", parent: "hub",
      label: "Room fan", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  const doc = dom.window.document;
  // The .tp-node wrapper for the effector — clicking simulates the
  // < 2px movement heuristic in setupNodeDrag by issuing mousedown +
  // mouseup with no mousemove in between.
  const nodeEl = doc.querySelector(".tp-node[data-node-id='effector:7']");
  assert.ok(nodeEl, "effector node mounted in the graph");
  nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  // Panel host now contains a visible panel for the clicked node.
  const aside = doc.querySelector("#tp-sidepanel-host aside.tp-sidepanel");
  assert.ok(aside, "panel mounted");
  assert.ok(!aside.classList.contains("hidden"),
    "panel is visible after click");
  assert.equal(aside.dataset.nodeId, "effector:7",
    "panel tracks the selected node's id");
});


test("boot: clicking close × on an open panel re-hides it", async () => {
  const dom = _bootDom();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:9", kind: "effector", parent: "hub",
      label: "Cabinet fan", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  const doc = dom.window.document;
  const nodeEl = doc.querySelector(".tp-node[data-node-id='effector:9']");
  nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  let aside = doc.querySelector("#tp-sidepanel-host aside.tp-sidepanel");
  assert.ok(aside && !aside.classList.contains("hidden"),
    "panel is visible after click");
  // Click the close × button.
  aside.querySelector("[data-testid='tp-sidepanel-close']")
    .dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  aside = doc.querySelector("#tp-sidepanel-host aside.tp-sidepanel");
  assert.ok(aside.classList.contains("hidden"),
    "panel hides after close × click");
});

/**
 * Tests for the /controls page boot module (Phase 4 Task 4.2).
 *
 * The module must:
 *   1. Locate the four host elements stamped by templates/controls.html
 *      (tp-topbar-host, tp-graph-host, tp-statusbar-host, tp-sidepanel-host).
 *   2. Fetch GET /api/topology and paint placeholder chrome into the
 *      first three hosts (Phase 5/6 own the real graph + side panel).
 *   3. On fetch failure, surface a single status-bar error message
 *      rather than throwing — a 5xx hub shouldn't crash the whole
 *      module-loader chain (subsequent SSE wiring still needs to run).
 *
 * The boot function is exported AND auto-runs in a browser; tests
 * call boot() directly so they can mock fetch + EventSource without
 * relying on the auto-boot guard. JSDOM is the standard test
 * environment everywhere else in static/js — see test_grow_card.mjs
 * + test_backup_status_panel.mjs for prior art.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { boot } from "../../static/js/topology/page.mjs";


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


function _mockFetch(payload, { ok = true, status = 200 } = {}) {
  return async () => ({
    ok,
    status,
    async json() { return payload; },
  });
}


function _mockEventSourceCtor() {
  // Bare placeholder — boot may open an EventSource for future SSE
  // wiring (Phase 10). We never assert on it here, but we still need
  // the constructor present so the module-load doesn't throw.
  return class FakeEventSource {
    constructor() {
      this.readyState = 0;
      this.onerror = null;
    }
    addEventListener() {}
    close() {}
  };
}


test("boot: fetches /api/topology and paints all four hosts", async () => {
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  const stub = {
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [],
    layout: {},
  };
  await boot({ fetchFn: _mockFetch(stub) });
  const topbar = dom.window.document.getElementById("tp-topbar-host");
  const graph = dom.window.document.getElementById("tp-graph-host");
  const status = dom.window.document.getElementById("tp-statusbar-host");
  // Topbar paints the brand. Plan §4.2 spec.
  assert.match(topbar.textContent, /MLSS/);
  assert.match(topbar.textContent, /NODE MAP/);
  // Statusbar paints the SSE connectivity indicator.
  assert.match(status.textContent, /SSE/);
  // Graph host gets the placeholder SVG canvas — Phase 5 renders nodes
  // into it.
  assert.ok(graph.querySelector("svg.tp-graph-svg"),
    "graph host should contain the placeholder svg");
});


test("boot: paints error into statusbar when topology fetch fails", async () => {
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  const failingFetch = async () => ({
    ok: false, status: 500,
    async json() { return { error: "boom" }; },
  });
  // Must NOT throw — the page should degrade gracefully so the
  // initial paint at least tells the operator something is wrong.
  await boot({ fetchFn: failingFetch });
  const status = dom.window.document.getElementById("tp-statusbar-host");
  assert.match(status.textContent.toLowerCase(),
    /error|failed|unavail/,
    "statusbar should surface the failure");
});


test("boot: tolerates missing hosts without throwing", async () => {
  // Defensive: if a future template change forgets one of the host
  // IDs, the boot function should log + skip rather than blow up the
  // whole module chain. Mount a DOM with NO hosts.
  const dom = new JSDOM(`<!doctype html><html><body></body></html>`);
  global.document = dom.window.document;
  global.window = dom.window;
  global.EventSource = _mockEventSourceCtor();
  // The exact behaviour is "doesn't throw" — we don't care what the
  // body looks like afterwards.
  await boot({ fetchFn: _mockFetch({hub:{},grows:[],effectors:[],layout:{}}) });
});


test("boot: with hub + grow + effector, renders 2 edges and 3 nodes", async () => {
  // Phase 5 Task 5.7: after a successful /api/topology fetch the
  // graph host should contain a fully-rendered topology — one edge
  // per parent-child pair, one node div per node. This is the
  // integration test that proves boot wires renderGraph into the
  // host correctly.
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  const payload = {
    hub: {
      id: "hub", kind: "hub", label: "MLSS Hub",
      sensors: { temp: 22.5, rh: 55, co2: 700 },
    },
    grows: [
      {
        id: "grow:1", kind: "grow", label: "Grow #1",
        plant_type: "tomato", phase: "vegetative",
        sensors: { soil_moisture: 60, soil_temp_c: 21, air_temp_c: 22 },
      },
    ],
    effectors: [
      {
        id: "effector:1", kind: "effector", parent: "hub",
        label: "Room fan", effector_type: "fan",
        mode: "auto", current_state: "off", is_enabled: 1,
      },
    ],
    layout: {},
  };
  // Note: boot wires up edges parent→child. With 1 hub-effector
  // (parent=hub) we get one edge; the grow has parent=hub implicitly
  // via the topology endpoint (the boot adds it). Total = 2 edges.
  await boot({ fetchFn: _mockFetch(payload) });
  const graph = dom.window.document.getElementById("tp-graph-host");
  const edges = graph.querySelectorAll("path.tp-edge");
  const nodes = graph.querySelectorAll(".tp-node");
  assert.equal(edges.length, 2, `expected 2 edges, got ${edges.length}`);
  assert.equal(nodes.length, 3, `expected 3 node divs, got ${nodes.length}`);
});

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


test("boot: card components are mounted inside each node div (Phase 6 Task 6.7)", async () => {
  // Each .tp-node div hosts a card; the renderer switches on
  // node.kind and delegates to the appropriate card module. With a
  // hub + grow + effector we expect one of each card class present
  // in the document.
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
  await boot({ fetchFn: _mockFetch(payload) });
  const doc = dom.window.document;
  assert.ok(doc.querySelector(".tp-card-hub"),
    "hub card should mount inside its .tp-node");
  assert.ok(doc.querySelector(".tp-card-grow"),
    "grow card should mount inside its .tp-node");
  assert.ok(doc.querySelector(".tp-card-effector"),
    "effector card should mount inside its .tp-node");
});


test("boot: topbar contains at least 5 stat cells after mount (Phase 7 Task 7.3)", async () => {
  // After boot the topbar host should contain the renderTopbar()
  // output: a `tp-topbar-inner` header with at minimum the 5 telemetry
  // cells (Hub Status / Grows / Effectors / Active / Auto vs Forced).
  // (Mission Time was removed per operator feedback.) The Recenter /
  // Re-arrange buttons are also expected but tested separately below.
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  const payload = {
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [],
    layout: {},
  };
  await boot({ fetchFn: _mockFetch(payload) });
  const doc = dom.window.document;
  const topbar = doc.getElementById("tp-topbar-host");
  const inner = topbar.querySelector(".tp-topbar-inner");
  assert.ok(inner, "topbar host contains a .tp-topbar-inner header");
  const cells = inner.querySelectorAll(".tp-stat");
  assert.ok(cells.length >= 5,
    `expected at least 5 .tp-stat cells, got ${cells.length}`);
});


test("boot: topbar shows + Add effector button only for admin role", async () => {
  // body.dataset.role = "admin" — admin sees the button.
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [], effectors: [], layout: {},
  }) });
  let doc = dom.window.document;
  assert.ok(
    doc.querySelector(".tp-topbar-inner button[data-action='add-effector']"),
    "admin sees the + Add effector button",
  );

  // Re-mount with role="viewer" — button must not render.
  const dom2 = new JSDOM(
    `<!doctype html><html><body data-role="viewer">
      <section class="tp-app" id="tp-app" data-role="viewer">
        <header id="tp-topbar-host"></header>
        <div    id="tp-graph-host"></div>
        <footer id="tp-statusbar-host"></footer>
        <aside  id="tp-sidepanel-host" class="hidden"></aside>
      </section>
    </body></html>`,
  );
  global.document = dom2.window.document;
  global.window = dom2.window;
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [], effectors: [], layout: {},
  }) });
  doc = dom2.window.document;
  assert.equal(
    doc.querySelector(".tp-topbar-inner button[data-action='add-effector']"),
    null,
    "viewer does not see the + Add effector button",
  );
});


test("boot: Recenter button resets the viewport (Phase 7 Task 7.3)", async () => {
  // Clicking Recenter must restore the viewport to the initial
  // {x: w/2, y: h/2 - 40, k: 0.9}. We can't easily inspect the
  // computed transform from JSDOM, but the boot exposes the click
  // wiring by re-rendering the graph after a viewport reset, so the
  // test asserts the graph re-mounts cleanly (no thrown error +
  // nodes still present).
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [{
      id: "grow:1", kind: "grow", label: "Grow #1",
      sensors: { soil_moisture: 60 },
    }],
    effectors: [],
    layout: {},
  }) });
  const doc = dom.window.document;
  const recenter = doc.querySelector(
    ".tp-topbar-inner button[data-action='recenter']",
  );
  assert.ok(recenter);
  recenter.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  // After the click the graph host still contains rendered nodes
  // (i.e. the boot's onRecenter callback didn't throw).
  const nodes = doc.querySelectorAll("#tp-graph-host .tp-node");
  assert.equal(nodes.length, 2,
    `expected 2 nodes (hub + grow) after recenter click, got ${nodes.length}`);
});


test("boot: + Add effector → modal → 201 → new node appears (Phase 9 Task 9.2)", async () => {
  // After clicking + Add effector, the topbar calls openAddEffectorModal
  // which mounts a modal in document.body. Filling the form + clicking
  // Submit issues POST /api/effectors; on 201 the boot orchestrator
  // pushes the new effector into the store + re-renders the graph so
  // the operator sees the new card without a page refresh.
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();

  let postedBody = null;
  const fetchFn = async (url, opts) => {
    if (url === "/api/topology") {
      return {
        ok: true, status: 200,
        async json() {
          return {
            hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
            grows: [],
            effectors: [],
            layout: {},
          };
        },
      };
    }
    if (url === "/api/grow/units") {
      return new Response(JSON.stringify({ units: [] }),
        { status: 200 });
    }
    if (url === "/api/effectors" && opts && opts.method === "POST") {
      postedBody = JSON.parse(opts.body);
      return new Response(JSON.stringify({
        id: 5,
        label: "Cabinet fan",
        effector_type: "fan",
        scope: "hub",
        grow_unit_id: null,
        kasa_host: "192.0.2.42",
        is_enabled: 1,
        auto_mode: 1,
        current_state: "unknown",
        rules: {},
        layout: null,
      }), { status: 201 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };

  await boot({ fetchFn });
  const doc = dom.window.document;
  // No effectors before the click — only the hub card is rendered.
  assert.equal(doc.querySelectorAll(".tp-card-effector").length, 0);

  // Click + Add effector → opens the modal.
  doc.querySelector(
    ".tp-topbar-inner button[data-action='add-effector']",
  ).dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const overlay = doc.querySelector("[data-testid='add-effector-overlay']");
  assert.ok(overlay, "modal opens on + Add effector click");

  // Fill the form. Default scope=hub + default type=fan are correct
  // for this test, so we only need label + kasa_host.
  overlay.querySelector("input[name='label']").value = "Cabinet fan";
  overlay.querySelector("input[name='kasa_host']").value = "192.0.2.42";
  overlay.querySelector("button[data-action='submit']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));

  // Allow the POST + onCreated callback + re-render to flush.
  // Multiple await ticks because the chain is:
  //   click → fetchFn (1 microtask) → .json() (1) → onCreated (sync) →
  //   close() (sync). Twenty ticks is well over the worst case.
  for (let i = 0; i < 20; i++) await Promise.resolve();

  // Modal closed.
  assert.equal(
    doc.querySelector("[data-testid='add-effector-overlay']"),
    null,
    "modal closes after successful 201",
  );
  // POST issued with the expected body.
  assert.ok(postedBody);
  assert.equal(postedBody.label, "Cabinet fan");
  assert.equal(postedBody.kasa_host, "192.0.2.42");
  assert.equal(postedBody.effector_type, "fan");
  assert.equal(postedBody.scope, "hub");
  // New effector card present in the graph.
  const effectorCards = doc.querySelectorAll(".tp-card-effector");
  assert.equal(effectorCards.length, 1,
    `expected 1 effector card after + Add effector → submit, got ` +
    `${effectorCards.length}`);
});


test("boot: Re-arrange button re-runs auto-layout (Phase 7 Task 7.3)", async () => {
  // Re-arrange clears the persisted positions object + re-runs
  // autoLayout. Same visibility-check assertion as Recenter — the
  // post-click graph host stays populated (i.e. no throw).
  const dom = _newDom();
  global.EventSource = _mockEventSourceCtor();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [
      { id: "grow:1", kind: "grow", label: "Grow #1", sensors: {} },
      { id: "grow:2", kind: "grow", label: "Grow #2", sensors: {} },
    ],
    effectors: [],
    // Pre-seed a custom position for grow:1 so we can confirm the
    // click effectively wipes it. (We don't inspect positions
    // directly — the assertion is that nodes are still rendered
    // afterwards, mirroring the Recenter check.)
    layout: { "grow:1": { x: 999, y: 999 } },
  }) });
  const doc = dom.window.document;
  const btn = doc.querySelector(
    ".tp-topbar-inner button[data-action='rearrange']",
  );
  assert.ok(btn);
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  const nodes = doc.querySelectorAll("#tp-graph-host .tp-node");
  assert.equal(nodes.length, 3,
    `expected 3 nodes (hub + 2 grows) after rearrange, got ${nodes.length}`);
});

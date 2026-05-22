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


// ─── Task 8.2 — Effector panel: Mode + Power + Hardware ─────────────────


test("effector panel: renders the Mode / Power / Hardware sections", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // Each section has a heading; we assert presence rather than exact
  // copy so the test is robust to wording polish.
  const headings = Array.from(el.querySelectorAll(".tp-sect-h"))
    .map((h) => h.textContent.trim().toLowerCase());
  assert.ok(headings.some((h) => h === "mode"),
    `expected a "Mode" section heading, got ${JSON.stringify(headings)}`);
  assert.ok(headings.some((h) => h === "power"),
    `expected a "Power" section heading, got ${JSON.stringify(headings)}`);
  assert.ok(headings.some((h) => h === "hardware"),
    `expected a "Hardware" section heading, got ${JSON.stringify(headings)}`);
});


test("effector panel: Mode bar has AUTO/ON/OFF buttons matching current mode", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // Reuses the same .tp-modebar from the on-card mode-bar so styling
  // stays in lockstep across the card + the panel.
  const bar = el.querySelector(".tp-modebar");
  assert.ok(bar, "panel contains a .tp-modebar");
  const auto = bar.querySelector("[data-mode='auto']");
  const on   = bar.querySelector("[data-mode='on']");
  const off  = bar.querySelector("[data-mode='off']");
  assert.ok(auto && on && off, "all three mode buttons present");
  // sampleEffector.mode === "auto"
  assert.equal(auto.getAttribute("aria-pressed"), "true");
  assert.equal(on.getAttribute("aria-pressed"), "false");
});


test("effector panel: clicking a Mode button fires onModeChange(id, mode)", () => {
  const dom = _newDom();
  const calls = [];
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true,
    callbacks: { onModeChange: (id, m) => calls.push([id, m]) },
  });
  el.querySelector(".tp-modebar [data-mode='on']")
    .dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(calls, [["effector:7", "on"]]);
});


test("effector panel: Power section contains a slider input (range)", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // The slider lives inside the Power section. AstroUX <rux-slider>
  // upgrades from a vanilla <input type=range> for JSDOM compatibility.
  const slider = el.querySelector(".tp-sidepanel-body input[type='range'], rux-slider");
  assert.ok(slider, "Power section contains a range slider element");
});


test("effector panel: Hardware section surfaces effector_type, kasa_host, protocol", () => {
  const dom = _newDom();
  const node = {
    ...sampleEffector,
    kasa_host: "192.0.2.10",
    protocol: "kasa",
  };
  const el = renderSidePanel({
    node, allNodes: [sampleHub, node],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // The Hardware kv-grid surfaces type / host / protocol.
  const txt = el.textContent;
  assert.match(txt, /fan/i, "shows effector_type");
  assert.match(txt, /192\.0\.2\.10/, "shows kasa_host");
  assert.match(txt, /kasa/i, "shows protocol");
});


// ─── Task 8.3 — Effector panel: Belongs-to (re-parent) picker ──────────


test("effector panel: Belongs-to picker lists hub + every grow as candidates", () => {
  const dom = _newDom();
  const grow1 = { ...sampleGrow };
  const grow2 = { ...sampleGrow, id: "grow:2", label: "Grow #2" };
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, grow1, grow2, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // Buttons inside the .tp-target-pick section.
  const pick = el.querySelector(".tp-target-pick");
  assert.ok(pick, "panel contains a .tp-target-pick block");
  const targets = pick.querySelectorAll("[data-parent-id]");
  // Hub + 2 grows = 3 candidates.
  assert.equal(targets.length, 3,
    `expected 3 candidate parents (hub + 2 grows), got ${targets.length}`);
  const ids = Array.from(targets).map((t) => t.dataset.parentId);
  assert.ok(ids.includes("hub"));
  assert.ok(ids.includes("grow:1"));
  assert.ok(ids.includes("grow:2"));
});


test("effector panel: current parent is marked as the selected target", () => {
  const dom = _newDom();
  const grow1 = { ...sampleGrow };
  const el = renderSidePanel({
    node: sampleEffector,  // parent=hub
    allNodes: [sampleHub, grow1, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const hubBtn = el.querySelector(".tp-target-pick [data-parent-id='hub']");
  assert.ok(hubBtn.classList.contains("selected") ||
            hubBtn.getAttribute("aria-pressed") === "true",
    "current parent (hub) is marked as selected");
});


test("effector panel: clicking a candidate fires onReparent(id, newParentId)", () => {
  const dom = _newDom();
  const grow1 = { ...sampleGrow };
  const calls = [];
  const el = renderSidePanel({
    node: sampleEffector,
    allNodes: [sampleHub, grow1, sampleEffector],
    doc: dom.window.document, isAdmin: true,
    callbacks: { onReparent: (id, p) => calls.push([id, p]) },
  });
  const growBtn = el.querySelector(".tp-target-pick [data-parent-id='grow:1']");
  growBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(calls, [["effector:7", "grow:1"]]);
});


test("effector panel: clicking the current parent does NOT fire onReparent", () => {
  const dom = _newDom();
  const grow1 = { ...sampleGrow };
  const calls = [];
  const el = renderSidePanel({
    node: sampleEffector,
    allNodes: [sampleHub, grow1, sampleEffector],
    doc: dom.window.document, isAdmin: true,
    callbacks: { onReparent: (id, p) => calls.push([id, p]) },
  });
  el.querySelector(".tp-target-pick [data-parent-id='hub']")
    .dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(calls.length, 0,
    "no-op when the operator clicks the already-selected parent");
});


test("effector panel: incompatible candidates rendered with [disabled]", () => {
  const dom = _newDom();
  // heat_pad is grow-unit-only per COMPATIBLE_SCOPES; the Hub candidate
  // must be greyed out.
  const heatPad = {
    ...sampleEffector,
    id: "effector:11",
    label: "Heating mat",
    effector_type: "heat_pad",
    parent: "grow:1",
  };
  const grow1 = { ...sampleGrow };
  const el = renderSidePanel({
    node: heatPad, allNodes: [sampleHub, grow1, heatPad],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const hubBtn = el.querySelector(".tp-target-pick [data-parent-id='hub']");
  assert.ok(hubBtn.disabled,
    "Hub candidate should be disabled for a heat_pad effector");
});


test("effector panel: Belongs-to inline error surface exists, hidden by default", () => {
  const dom = _newDom();
  const grow1 = { ...sampleGrow };
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, grow1, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const err = el.querySelector(".tp-target-pick [data-testid='tp-reparent-error']");
  assert.ok(err, "Belongs-to error surface present in the DOM");
  // Hidden initially — the picker exposes a .show()/.hide() pattern
  // surfaced via the .hidden helper class.
  assert.ok(err.classList.contains("hidden") || err.style.display === "none",
    "error surface starts hidden until a server 400 lands");
});


test("boot: onReparent posts PATCH /api/effectors/<id> with the right body", async () => {
  const dom = _bootDom();
  const calls = [];
  const fetchFn = async (url, opts) => {
    calls.push({ url, opts });
    if (url === "/api/topology") {
      return {
        ok: true, status: 200,
        async json() {
          return {
            hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
            grows: [
              { id: "grow:3", kind: "grow", label: "Grow #3",
                plant_type: "basil", phase: "veg", medium: "soil",
                sensors: {} },
            ],
            effectors: [
              { id: "effector:4", kind: "effector", parent: "hub",
                label: "Humidifier", effector_type: "humidifier",
                mode: "auto", current_state: "off", is_enabled: 1,
                kasa_host: "192.0.2.4", protocol: "kasa" },
            ],
            layout: {},
          };
        },
      };
    }
    if (url === "/api/effectors/4" && opts && opts.method === "PATCH") {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };
  await boot({ fetchFn });
  const doc = dom.window.document;
  // Open the panel for effector:4 by clicking the node.
  const nodeEl = doc.querySelector(".tp-node[data-node-id='effector:4']");
  nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
    bubbles: true, button: 0, clientX: 0, clientY: 0,
  }));
  // Click the grow:3 candidate to re-parent.
  const grow3Btn = doc.querySelector(
    "#tp-sidepanel-host .tp-target-pick [data-parent-id='grow:3']",
  );
  assert.ok(grow3Btn, "grow:3 candidate present");
  grow3Btn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  for (let i = 0; i < 20; i++) await Promise.resolve();
  // PATCH /api/effectors/4 should have fired with the grow-unit body.
  const patch = calls.find(
    (c) => c.url === "/api/effectors/4" && c.opts && c.opts.method === "PATCH",
  );
  assert.ok(patch, "PATCH /api/effectors/4 was issued");
  const body = JSON.parse(patch.opts.body);
  assert.equal(body.scope, "grow_unit");
  assert.equal(body.grow_unit_id, 3);
});


// ─── Task 8.4 — Schedule grid placeholder ───────────────────────────────


test("effector panel: schedule renders a 24-cell hour grid", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const cells = el.querySelectorAll(".tp-sched-cell");
  assert.equal(cells.length, 24,
    `expected 24 schedule cells, got ${cells.length}`);
});


test("effector panel: schedule includes a 'coming in v2' marker", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleEffector, allNodes: [sampleHub, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const marker = el.querySelector(".tp-coming-v2");
  assert.ok(marker, "v2-coming marker text is present");
  assert.match(marker.textContent.toLowerCase(), /v2/,
    "marker mentions v2");
});


// ─── Task 8.5 — Grow panel ──────────────────────────────────────────────


test("grow panel: renders Plant / Live sensors / Linked effectors sections", () => {
  const dom = _newDom();
  const eff = {
    ...sampleEffector, id: "effector:7", parent: "grow:1",
    label: "Heat pad", effector_type: "heat_pad",
  };
  const el = renderSidePanel({
    node: sampleGrow, allNodes: [sampleHub, sampleGrow, eff],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const headings = Array.from(el.querySelectorAll(".tp-sect-h"))
    .map((h) => h.textContent.trim().toLowerCase());
  assert.ok(headings.includes("plant"), "Plant section heading");
  assert.ok(headings.includes("live sensors"), "Live sensors section heading");
  assert.ok(headings.includes("linked effectors"), "Linked effectors section heading");
});


test("grow panel: Plant block surfaces plant_type / phase / medium", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleGrow, allNodes: [sampleHub, sampleGrow],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const txt = el.textContent;
  assert.match(txt, /tomato/i, "shows plant_type");
  assert.match(txt, /vegetative/i, "shows phase");
  assert.match(txt, /soil/i, "shows medium");
});


test("grow panel: Live sensors block shows the 4 telemetry tiles", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleGrow, allNodes: [sampleHub, sampleGrow],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const txt = el.textContent;
  // The Live sensors kv-grid lists every field from sensors:
  //   soil_moisture, soil_temp_c, air_temp_c, air_humidity_pct
  assert.match(txt, /60/, "shows soil_moisture value");
  assert.match(txt, /21/, "shows soil_temp_c value");
  assert.match(txt, /22/, "shows air_temp_c value");
  assert.match(txt, /55/, "shows air_humidity_pct value");
});


test("grow panel: Linked effectors lists effectors with parent === grow:<id>", () => {
  const dom = _newDom();
  const eff1 = {
    ...sampleEffector, id: "effector:7", parent: "grow:1",
    label: "Heat pad", effector_type: "heat_pad",
  };
  const eff2 = {
    ...sampleEffector, id: "effector:8", parent: "grow:1",
    label: "Light A", effector_type: "light_supplementary",
  };
  const eff3 = {
    // Different parent — should NOT appear.
    ...sampleEffector, id: "effector:9", parent: "hub",
    label: "Room fan", effector_type: "fan",
  };
  const el = renderSidePanel({
    node: sampleGrow, allNodes: [sampleHub, sampleGrow, eff1, eff2, eff3],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const linked = el.querySelector(".tp-linked-effectors");
  assert.ok(linked, "linked effectors block present");
  const txt = linked.textContent;
  assert.match(txt, /Heat pad/);
  assert.match(txt, /Light A/);
  assert.ok(!/Room fan/.test(txt),
    "effectors parented to hub should NOT appear in this grow's list");
});


test("grow panel: includes 'View full grow page' button linking to /grow/<id>", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleGrow, allNodes: [sampleHub, sampleGrow],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const link = el.querySelector("a[href='/grow/1'], a.tp-view-grow-link");
  assert.ok(link, "panel includes a link to the per-unit grow page");
  assert.equal(link.getAttribute("href"), "/grow/1");
  assert.match(link.textContent.toLowerCase(), /grow|view/,
    "link wording references the grow page");
});


// ─── Task 8.5 — Hub panel ───────────────────────────────────────────────


test("hub panel: renders Room sensors / Coordination / Subsystems sections", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleHub, allNodes: [sampleHub, sampleGrow, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const headings = Array.from(el.querySelectorAll(".tp-sect-h"))
    .map((h) => h.textContent.trim().toLowerCase());
  assert.ok(headings.includes("room sensors"));
  assert.ok(headings.some((h) => h.startsWith("coordination")),
    "Coordination section heading present");
  assert.ok(headings.includes("subsystems"));
});


test("hub panel: Room sensors block shows temp / RH / CO2", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleHub, allNodes: [sampleHub, sampleGrow, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  const txt = el.textContent;
  assert.match(txt, /22\.5/, "shows temp value");
  assert.match(txt, /55/, "shows rh value");
  assert.match(txt, /700/, "shows co2 value");
});


test("hub panel: Coordination block shows node.notes copy", () => {
  const dom = _newDom();
  const el = renderSidePanel({
    node: sampleHub, allNodes: [sampleHub, sampleGrow, sampleEffector],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  assert.match(el.textContent, /Whole-room sensors/,
    "Coordination block surfaces the node.notes string");
});


test("hub panel: Subsystems block counts grows / effectors / active", () => {
  const dom = _newDom();
  const onEff = {
    ...sampleEffector, id: "effector:1", current_state: "on", mode: "on",
  };
  const offEff = {
    ...sampleEffector, id: "effector:2", current_state: "off", mode: "off",
  };
  const grow2 = { ...sampleGrow, id: "grow:2" };
  const el = renderSidePanel({
    node: sampleHub,
    allNodes: [sampleHub, sampleGrow, grow2, onEff, offEff],
    doc: dom.window.document, isAdmin: true, callbacks: {},
  });
  // Subsystems counts: 2 grows, 2 effectors, 1 active. The block
  // renders each as its own .tp-kv-v cell so we inspect the values
  // directly rather than relying on whitespace word boundaries.
  const sub = el.querySelector(".tp-subsystems-grid");
  assert.ok(sub, "Subsystems block present (.tp-subsystems-grid)");
  const values = Array.from(sub.querySelectorAll(".tp-kv-v"))
    .map((v) => v.textContent.trim());
  assert.deepEqual(values, ["2", "2", "1"],
    `expected counts [grows=2, effectors=2, active=1], got ${JSON.stringify(values)}`);
});


// ─── Task 8.6 — Admin cog opens panel for that effector ────────────────


test("boot: clicking the admin cog on an effector card opens the side panel", async () => {
  const dom = _bootDom("admin");
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:42", kind: "effector", parent: "hub",
      label: "Cabinet fan", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  const doc = dom.window.document;
  const cog = doc.querySelector(
    ".tp-node[data-node-id='effector:42'] [data-action='open-config']",
  );
  assert.ok(cog, "admin cog button is present on the effector card");
  cog.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  // Panel host now holds a visible panel for effector:42.
  const aside = doc.querySelector("#tp-sidepanel-host aside.tp-sidepanel");
  assert.ok(aside);
  assert.ok(!aside.classList.contains("hidden"),
    "panel is visible after cog click");
  assert.equal(aside.dataset.nodeId, "effector:42",
    "panel tracks the cog's effector id");
});


test("boot: admin cog click does NOT trigger a node drag", async () => {
  // The card-level handler stopPropagation()'s the cog click; this
  // boot-level test confirms the wrapping setupNodeDrag handler
  // doesn't fire either — i.e. the mousedown propagation guard is
  // honoured all the way up to the .tp-node element.
  const dom = _bootDom("admin");
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "MLSS Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:99", kind: "effector", parent: "hub",
      label: "X", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  const doc = dom.window.document;
  const nodeEl = doc.querySelector(".tp-node[data-node-id='effector:99']");
  let nodeDragFired = false;
  // Spy: wrap the existing mousedown listener invocation by adding a
  // capture-phase listener BEFORE the node-drag handler. If the cog
  // button propagates, our spy fires — but the cog handler should
  // stopPropagation so nothing reaches the spy.
  nodeEl.addEventListener("mousedown", () => { nodeDragFired = true; });
  const cog = nodeEl.querySelector("[data-action='open-config']");
  cog.dispatchEvent(new dom.window.MouseEvent("mousedown", { bubbles: true }));
  assert.equal(nodeDragFired, false,
    "node-level mousedown handler must NOT fire when the cog is pressed");
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

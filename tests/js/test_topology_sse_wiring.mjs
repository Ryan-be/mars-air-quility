/**
 * Integration tests for the boot()-side SSE wiring (Phase 10 Task 10.2
 * + 10.3).
 *
 * boot() now subscribes to /api/stream via static/js/topology/sse.mjs
 * and, on each named event, updates the in-memory store and re-renders
 * only the affected card rather than the whole graph:
 *
 *   - effector_state_changed  → re-render the matching effector card
 *   - sensor_update           → update store.history.hub + re-render hub
 *   - health_update           → update Hub Status cell on the topbar
 *   - fan_status              → legacy alias for effector_state_changed
 *
 * Tests also cover the rolling 30-value history buffer via
 * `pushHistory`, exported from page.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { boot, pushHistory } from "../../static/js/topology/page.mjs";


// ─── Shared fixture helpers ──────────────────────────────────────────


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


function _mockFetch(payload) {
  return async () => ({
    ok: true,
    status: 200,
    async json() { return payload; },
  });
}


function _captureEventSource() {
  // Capture every constructed instance + expose a `dispatch` helper so
  // tests can synthesise an SSE event without involving real network IO.
  const instances = [];
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.onerror = null;
      this.onopen = null;
      this.closed = false;
      instances.push(this);
    }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() { this.closed = true; }
    dispatch(name, payload) {
      const fn = this.listeners[name];
      if (fn) fn({ data: JSON.stringify(payload) });
    }
  }
  global.EventSource = FakeEventSource;
  return instances;
}


// ─── pushHistory pure-function tests ────────────────────────────────


test("pushHistory: appends value at the configured key", () => {
  const hist = {};
  pushHistory(hist, "hub", "temp", 22.5);
  assert.deepEqual(hist.hub.temp, [22.5]);
});


test("pushHistory: caps the buffer at 30 by default", () => {
  const hist = {};
  for (let i = 0; i < 32; i++) pushHistory(hist, "hub", "temp", i);
  assert.equal(hist.hub.temp.length, 30);
  // Oldest two values dropped; latest is 31.
  assert.equal(hist.hub.temp[0], 2);
  assert.equal(hist.hub.temp[hist.hub.temp.length - 1], 31);
});


test("pushHistory: respects custom cap", () => {
  const hist = {};
  for (let i = 0; i < 10; i++) pushHistory(hist, "hub", "temp", i, 5);
  assert.equal(hist.hub.temp.length, 5);
});


test("pushHistory: nullish values are skipped (no NaN sparklines)", () => {
  const hist = {};
  pushHistory(hist, "hub", "temp", null);
  pushHistory(hist, "hub", "temp", undefined);
  pushHistory(hist, "hub", "temp", 22.5);
  assert.deepEqual(hist.hub.temp, [22.5]);
});


// ─── boot() SSE wiring tests ────────────────────────────────────────


test("boot: subscribes to /api/stream on mount", async () => {
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [], effectors: [], layout: {},
  }) });
  // boot opens at least one EventSource against /api/stream.
  const streams = instances.filter((i) => i.url === "/api/stream");
  assert.ok(streams.length >= 1,
    `expected at least one /api/stream subscription, got ${streams.length}`);
  // Silence the JSDOM noise about the test exit.
  dom.window.close();
});


test("boot: effector_state_changed re-renders the affected effector card", async () => {
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:1", kind: "effector", parent: "hub",
      label: "Fan", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  const doc = dom.window.document;
  // Initial state: card shows mode=auto so the AUTO button has `active`.
  const cardBefore = doc.querySelector('[data-node-id="effector:1"]');
  assert.ok(cardBefore);
  const autoBtnBefore = cardBefore.querySelector(".tp-modebtn.active");
  assert.match(autoBtnBefore.textContent, /AUTO/);

  // Server pushes an effector_state_changed event saying the fan was
  // forced ON. Dispatch via the captured stream.
  instances[instances.length - 1].dispatch("effector_state_changed", {
    id: 1, state: "on", auto: false,
  });

  // The targeted re-render should update the effector card so the ON
  // button now carries the `active` class.
  const cardAfter = doc.querySelector('[data-node-id="effector:1"]');
  const activeAfter = cardAfter.querySelector(".tp-modebtn.active");
  assert.match(activeAfter.textContent, /ON/);
  dom.window.close();
});


test("boot: sensor_update updates the hub card tile + pushes history", async () => {
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: {
      id: "hub", kind: "hub", label: "Hub",
      sensors: { temp: 22.5, rh: 55, co2: 700 },
    },
    grows: [], effectors: [], layout: {},
  }) });
  const doc = dom.window.document;
  const hubBefore = doc.querySelector('[data-node-id="hub"]');
  // Temp tile starts at 22.5°C.
  assert.match(hubBefore.textContent, /22\.5/);

  // Push a sensor_update with a new temperature.
  instances[instances.length - 1].dispatch("sensor_update", {
    temperature: 25.7, humidity: 60, eco2: 750,
  });

  const hubAfter = doc.querySelector('[data-node-id="hub"]');
  assert.match(hubAfter.textContent, /25\.7/,
    "hub card should reflect the new SSE temperature");
  dom.window.close();
});


test("boot: 32 sensor_updates leave hub.temp history capped at 30", async () => {
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: {
      id: "hub", kind: "hub", label: "Hub",
      sensors: { temp: 0, rh: 0, co2: 0 },
    },
    grows: [], effectors: [], layout: {},
  }) });
  const stream = instances[instances.length - 1];
  for (let i = 0; i < 32; i++) {
    stream.dispatch("sensor_update", {
      temperature: 20 + i * 0.1, humidity: 50, eco2: 700,
    });
  }
  // The renderHubCard helper only mounts a <svg.tp-spark> when the
  // history has at least 2 points (per its docblock). Confirm the
  // sparkline is present and has exactly 30 polyline points.
  const doc = dom.window.document;
  const hubCard = doc.querySelector('[data-node-id="hub"] .tp-card-hub');
  const spark = hubCard.querySelector("svg.tp-spark");
  assert.ok(spark, "sparkline should mount after multiple sensor updates");
  // The sparkline polyline carries one comma-separated point per value;
  // counting commas gives a robust proxy for the rolling buffer length.
  const polyline = spark.querySelector("polyline");
  const pointAttr = polyline ? polyline.getAttribute("points") : "";
  const pointCount = pointAttr.trim().split(/\s+/).length;
  assert.equal(pointCount, 30,
    `expected 30 sparkline points (cap), got ${pointCount}`);
  dom.window.close();
});


test("boot: health_update overwrites the Hub Status cell", async () => {
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [], effectors: [], layout: {},
  }) });
  const doc = dom.window.document;
  // Initial: Hub Status cell reads "Nominal".
  const hubStatusValue = doc.querySelector(
    ".tp-stat [data-role='hub-status']",
  );
  assert.ok(hubStatusValue, "topbar exposes a hub-status data-role target");
  // After a health_update with status="degraded", the cell flips.
  instances[instances.length - 1].dispatch("health_update", {
    status: "degraded",
  });
  const after = doc.querySelector(".tp-stat [data-role='hub-status']");
  assert.match(after.textContent.toLowerCase(), /degraded/);
  dom.window.close();
});


test("boot: fan_status routes to the same re-render path as effector_state_changed", async () => {
  // The legacy fan_status event is the pre-v2 broadcast for the seeded
  // fan (id=1). Subscribing to it keeps the topology view live during
  // the deprecation window — the page MUST treat it identically to
  // effector_state_changed for id=1.
  const dom = _newDom();
  const instances = _captureEventSource();
  await boot({ fetchFn: _mockFetch({
    hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
    grows: [],
    effectors: [{
      id: "effector:1", kind: "effector", parent: "hub",
      label: "Fan", effector_type: "fan",
      mode: "auto", current_state: "off", is_enabled: 1,
    }],
    layout: {},
  }) });
  // Dispatch the legacy event. The payload shape from api_fan publishes
  // {state: "on"} (no `id`); the wiring infers id=1.
  instances[instances.length - 1].dispatch("fan_status", { state: "on" });
  const doc = dom.window.document;
  const activeAfter = doc.querySelector(
    '[data-node-id="effector:1"] .tp-modebtn.active',
  );
  assert.match(activeAfter.textContent, /ON/,
    "fan_status should drive the same re-render as effector_state_changed");
  dom.window.close();
});

/**
 * Tests for the sensor-event chart used on the Live tab's watering-
 * history panel. Two regression issues we want pinned:
 *
 *   (a) The /history endpoint emits `watering_events` (not `events`).
 *       The chart used to read `events`, which was undefined →
 *       `events.map()` crashed once Plotly was actually loaded
 *       (without Plotly the early-return masked it). We now accept
 *       both keys defensively.
 *
 *   (b) Downsampled moisture rows use `pct_avg`; raw rows use `pct`.
 *       The chart used to read only `pct`, breaking on long ranges
 *       (7d/30d/90d/all) where downsampling kicks in.
 *
 *   (c) Camera-only / brand-new units have neither moisture nor
 *       events — render a placeholder rather than crashing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderSensorEventChart } from
  "../../static/js/grow/components/sensor-event-chart.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


// Stub Plotly so the early-return on missing-Plotly doesn't fire.
// Records calls so we can assert what was passed.
function _installFakePlotly() {
  const calls = [];
  globalThis.Plotly = {
    newPlot(container, traces, layout, opts) {
      calls.push({ container, traces, layout, opts });
    },
  };
  return calls;
}

function _uninstallFakePlotly() {
  delete globalThis.Plotly;
}


test("accepts watering_events key (server contract)", () => {
  const calls = _installFakePlotly();
  try {
    const container = document.createElement("div");
    renderSensorEventChart(container, {
      moisture: [{ ts: "2026-05-08T12:00:00Z", pct: 60 }],
      watering_events: [{
        ts: "2026-05-08T12:01:00Z", duration_s: 5, trigger: "manual",
      }],
    });
    assert.equal(calls.length, 1, "Plotly.newPlot called once");
    const eventsTrace = calls[0].traces[1];
    assert.deepEqual(eventsTrace.x, ["2026-05-08T12:01:00Z"]);
    assert.deepEqual(eventsTrace.y, [5]);
  } finally {
    _uninstallFakePlotly();
  }
});


test("falls back to legacy `events` key", () => {
  const calls = _installFakePlotly();
  try {
    const container = document.createElement("div");
    renderSensorEventChart(container, {
      moisture: [{ ts: "2026-05-08T12:00:00Z", pct: 60 }],
      events: [{ ts: "2026-05-08T12:01:00Z", duration_s: 5, trigger: "pid" }],
    });
    assert.equal(calls.length, 1);
  } finally {
    _uninstallFakePlotly();
  }
});


test("handles downsampled moisture rows (pct_avg)", () => {
  const calls = _installFakePlotly();
  try {
    const container = document.createElement("div");
    renderSensorEventChart(container, {
      moisture: [{ ts: "2026-05-01T00:00:00Z", pct_avg: 58 }],
      watering_events: [],
    });
    assert.equal(calls.length, 1);
    const moistureTrace = calls[0].traces[0];
    assert.deepEqual(moistureTrace.y, [58]);
  } finally {
    _uninstallFakePlotly();
  }
});


test("renders placeholder for empty unit (no crash)", () => {
  const calls = _installFakePlotly();
  try {
    const container = document.createElement("div");
    renderSensorEventChart(container, {
      moisture: [],
      watering_events: [],
    });
    // Plotly NOT called — placeholder text rendered instead
    assert.equal(calls.length, 0);
    assert.match(container.textContent, /no.*data/i);
  } finally {
    _uninstallFakePlotly();
  }
});


test("does not crash when watering_events / events are both undefined", () => {
  const calls = _installFakePlotly();
  try {
    const container = document.createElement("div");
    // Intentionally pass an object missing both event keys — what could
    // happen during the `data: undefined → {}` defensive path on the
    // unit_detail caller. Should not throw.
    assert.doesNotThrow(() => {
      renderSensorEventChart(container, {
        moisture: [{ ts: "2026-05-08T12:00:00Z", pct: 50 }],
      });
    });
  } finally {
    _uninstallFakePlotly();
  }
});


test("plotly-undefined still short-circuits (no crash)", () => {
  // Don't install fake Plotly — verify the early-return still works
  // even if data shape would normally cause a crash.
  const container = document.createElement("div");
  renderSensorEventChart(container, {});
  assert.equal(container.textContent, "Plotly not loaded");
});

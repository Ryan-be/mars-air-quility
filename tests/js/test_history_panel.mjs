/**
 * Tests for the history-panel orchestrator — Task 5 of the History-tab plan.
 *
 * The orchestrator is intentionally thin: it composes the moisture-history
 * chart and the photo-timelapse into a single panel so the tab-switcher
 * has one element to mount. No business logic of its own. Tests focus on
 *   1. both children are present
 *   2. both children get the same `unit` (verified via the URLs they
 *      fetch — each child issues a fetch tagged with `unit.id`)
 *
 * Fetch is mocked the same way as test_moisture_history_chart.mjs and
 * test_photo_timelapse.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderHistoryPanel } from "../../static/js/grow/components/history-panel.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit() {
  return { id: 7, label: "Tom 1", overrides: { watering_target: null } };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
async function _flushMicro() {
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
}


function _ok(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


test("history panel: mounts both moisture-chart and photo-timelapse children", async () => {
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (String(url).includes("/history")) {
      return _ok({ moisture: [], watering_events: [], phase_changes: [] });
    }
    return _ok([]);  // photos list
  });
  try {
    const el = renderHistoryPanel(_unit(), { ownerDocument: document });
    await _flushMicro();
    const chart = el.querySelector("[data-testid='moisture-chart']");
    assert.ok(chart, "moisture-chart child present");
    const tlapse = el.querySelector("[data-testid='photo-timelapse']");
    assert.ok(tlapse, "photo-timelapse child present");
  } finally {
    _setMockFetch(orig);
  }
});


test("history panel: passes the same unit to both children (URLs include unit.id)", async () => {
  const orig = _origFetch();
  const calls = [];
  _setMockFetch(async (url) => {
    calls.push(String(url));
    if (String(url).includes("/history")) {
      return _ok({ moisture: [], watering_events: [], phase_changes: [] });
    }
    return _ok([]);
  });
  try {
    renderHistoryPanel(_unit(), { ownerDocument: document });
    await _flushMicro();
    // The chart fetches /api/grow/units/7/history?range=24h
    // The timelapse fetches /api/grow/units/7/photos?range=24h
    const historyCall = calls.find((u) => u.includes("/history"));
    const photosCall = calls.find((u) => u.includes("/photos"));
    assert.ok(historyCall, "chart fired a /history fetch");
    assert.ok(photosCall, "timelapse fired a /photos fetch");
    assert.match(historyCall, /\/api\/grow\/units\/7\/history/,
      "chart URL targets unit 7");
    assert.match(photosCall, /\/api\/grow\/units\/7\/photos/,
      "timelapse URL targets unit 7");
  } finally {
    _setMockFetch(orig);
  }
});


test("history panel: returns a wrapped container element (not loose nodes)", async () => {
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (String(url).includes("/history")) {
      return _ok({ moisture: [], watering_events: [], phase_changes: [] });
    }
    return _ok([]);
  });
  try {
    const el = renderHistoryPanel(_unit(), { ownerDocument: document });
    assert.ok(el, "returns an element");
    assert.equal(el.nodeType, 1, "is an Element node");
    // Wrap is identifiable so the tab switcher can find it if needed
    assert.equal(el.dataset.testid, "history-panel");
  } finally {
    _setMockFetch(orig);
  }
});

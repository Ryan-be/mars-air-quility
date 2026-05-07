import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderDetailHeader,
  renderSubTabs,
  switchSubtab,
} from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 3, label: "Tomato 3", current_phase: "vegetative",
  medium_type: "soil", sown_at: "2026-04-10T00:00:00Z",
  status: "online", last_seen_at: new Date().toISOString(),
  capabilities: [], last_known_state: {},
  overrides: { watering_target: null },
};


/** Microtask flush — used by tests that exercise the tab switcher,
 *  whose mounted children fire async fetches. Same pattern as
 *  test_moisture_history_chart.mjs / test_photo_timelapse.mjs. */
async function _flushMicro() {
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
  }
}

function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
function _ok(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


/** Build a fresh JSDOM page with the three host elements that the
 *  unit_detail page provides. switchSubtab() reads `du-body` + `.du-tab`
 *  via getElementById/querySelectorAll, so the tests need a real document
 *  with those elements wired up. */
function _mountPage() {
  const page = new JSDOM(`<!doctype html><html><body>
    <div data-unit-id="3">
      <header id="du-header"></header>
      <nav id="du-tabs"></nav>
      <main id="du-body"></main>
    </div>
  </body></html>`);
  const doc = page.window.document;
  doc.getElementById("du-tabs").appendChild(renderSubTabs("live", doc));
  return doc;
}


test("detail header renders title + phase + status pill", () => {
  const el = renderDetailHeader(sampleUnit, document);
  assert.match(el.textContent, /Tomato 3/);
  assert.match(el.textContent, /vegetative/i);
  assert.ok(el.querySelector(".gu-status"));
});

test("detail header includes back link to /grow", () => {
  const el = renderDetailHeader(sampleUnit, document);
  const back = el.querySelector("a.du-back");
  assert.ok(back);
  assert.equal(back.getAttribute("href"), "/grow");
});

test("sub-tabs: Live is the active tab", () => {
  const el = renderSubTabs("live", document);
  const live = el.querySelector("[data-tab='live']");
  assert.match(live.className, /active/);
});


test("sub-tabs: Diagnostics is enabled (Phase 3 Task 4)", () => {
  // Phase 3 Task 4 activated the Diagnostics subtab. Asserts Live is
  // active AND Diagnostics is no longer disabled — replaces the older
  // test that pinned the deferred state.
  const el = renderSubTabs("live", document);
  const diag = el.querySelector("[data-tab='diagnostics']");
  assert.equal(diag.disabled, false);
  assert.ok(!diag.classList.contains("disabled"));
});


test("sub-tabs: Configure is enabled (Task 6 of Configure-tab plan)", () => {
  const el = renderSubTabs("live", document);
  const configure = el.querySelector("[data-tab='configure']");
  assert.equal(configure.disabled, false);
  assert.ok(!configure.classList.contains("disabled"));
});


test("sub-tabs: History is enabled (Task 5 of History-tab plan)", () => {
  const el = renderSubTabs("live", document);
  const history = el.querySelector("[data-tab='history']");
  assert.equal(history.disabled, false);
  assert.ok(!history.classList.contains("disabled"));
});


test("switchSubtab: clicking History renders moisture-chart + photo-timelapse", async () => {
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (String(url).includes("/history")) {
      return _ok({ moisture: [], watering_events: [], phase_changes: [] });
    }
    return _ok([]);  // photos list
  });
  try {
    const doc = _mountPage();
    await switchSubtab("history", sampleUnit, doc);
    await _flushMicro();
    const body = doc.getElementById("du-body");
    assert.ok(body.querySelector("[data-testid='moisture-chart']"),
      "moisture-chart panel mounted in du-body");
    assert.ok(body.querySelector("[data-testid='photo-timelapse']"),
      "photo-timelapse panel mounted in du-body");
    // The History tab should now be flagged active.
    const historyTab = doc.querySelector(".du-tab[data-tab='history']");
    assert.match(historyTab.className, /active/);
  } finally {
    _setMockFetch(orig);
  }
});


test("switchSubtab: clicking Diagnostics renders the diagnostics panel", async () => {
  // Phase 3 Task 4 — Diagnostics is now activatable. The orchestrator
  // does a single fetch to /diagnostics; mock that + assert the panel
  // mounts under du-body with all four child sections present.
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (String(url).includes("/diagnostics")) {
      return _ok({
        firmware_version: "0.3.1", uptime_s: 100, buffer_size: 0,
        connection_log: [], sensor_sanity: [], open_errors: [],
      });
    }
    return _ok({});
  });
  try {
    const doc = _mountPage();
    await switchSubtab("diagnostics", sampleUnit, doc);
    await _flushMicro();
    const body = doc.getElementById("du-body");
    assert.ok(body.querySelector("[data-testid='diagnostics-panel']"),
      "diagnostics panel mounted in du-body");
    assert.ok(body.querySelector("[data-testid='diag-firmware']"),
      "firmware-info child present");
    assert.ok(body.querySelector("[data-testid='diag-danger-zone']"),
      "danger-zone child present");
    const diagTab = doc.querySelector(".du-tab[data-tab='diagnostics']");
    assert.match(diagTab.className, /active/);
  } finally {
    _setMockFetch(orig);
  }
});


test("switchSubtab: switching back to Live re-renders the live panels", async () => {
  const orig = _origFetch();
  _setMockFetch(async (url) => {
    if (String(url).includes("/history")) {
      return _ok({ moisture: [], watering_events: [], phase_changes: [] });
    }
    if (String(url).includes("/photos")) {
      return _ok([]);
    }
    // /history?range=24h is also what renderWateringHistoryPanel fetches
    return _ok({ moisture: [], watering_events: [], phase_changes: [] });
  });
  try {
    const doc = _mountPage();
    // Mount History first, then flip back to Live.
    await switchSubtab("history", sampleUnit, doc);
    await _flushMicro();
    const body = doc.getElementById("du-body");
    assert.ok(body.querySelector("[data-testid='moisture-chart']"),
      "history mounted before flip back");

    await switchSubtab("live", sampleUnit, doc);
    await _flushMicro();
    // History components should be gone…
    assert.equal(body.querySelector("[data-testid='moisture-chart']"), null,
      "moisture-chart cleared when switching back to Live");
    assert.equal(body.querySelector("[data-testid='photo-timelapse']"), null,
      "photo-timelapse cleared when switching back to Live");
    // …and a live panel (one of the .du-panel cards) should be present.
    const livePanels = body.querySelectorAll(".du-panel");
    assert.ok(livePanels.length > 0, "at least one live .du-panel re-rendered");
    // The Live tab should now be flagged active.
    const liveTab = doc.querySelector(".du-tab[data-tab='live']");
    assert.match(liveTab.className, /active/);
  } finally {
    _setMockFetch(orig);
  }
});

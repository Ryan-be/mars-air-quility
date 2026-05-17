/**
 * Tests for the moisture-history-chart component — first panel of the
 * History tab delivered in Task 3 of the History-tab plan.
 *
 * The chart's wire shape is the interesting bit. The backend's
 * /api/grow/units/<id>/history endpoint returns either:
 *   - raw rows  {ts, pct, raw}                                 (≤600 rows)
 *   - downsampled rows {ts, pct_min, pct_avg, pct_max, raw_avg} (>600 rows)
 * The component sniffs the shape (presence of pct_avg) and renders a
 * single line vs a band+line. We test both branches plus the overlays
 * (watering events, target band) and the empty-data fallback.
 *
 * Fetch is mocked the same way as test_safety_override.mjs — set
 * globalThis.fetch in setup, restore in finally. JSDOM gives us
 * createElementNS for SVG creation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderMoistureHistoryChart } from "../../static/js/grow/components/moisture-history-chart.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit(overrides) {
  return {
    id: 7,
    label: "Tom 1",
    overrides: {
      watering_target: null,
      ...overrides,
    },
  };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
/** Microtask flush — several await Promise.resolve cycles to settle the
 *  fetch promise + the await r.json() chain inside loadAndRender. */
async function _flushMicro() {
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
}


/** Build a JSON Response wrapper for the mock fetch. */
function _ok(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


test("moisture chart: renders range selector with 5 options", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({ moisture: [], watering_events: [], phase_changes: [] }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const selector = el.querySelector("[data-testid='range-selector']");
    assert.ok(selector, "range selector container present");
    for (const r of ["24h", "7d", "30d", "90d", "all"]) {
      const btn = el.querySelector(`[data-testid='range-${r}']`);
      assert.ok(btn, `range button for ${r} present`);
    }
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: default range is 24h and triggers initial fetch with ?range=24h", async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url) => {
    captured = url;
    return _ok({ moisture: [], watering_events: [], phase_changes: [] });
  });
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    assert.ok(captured, "fetch was called on initial render");
    assert.match(String(captured), /\?range=24h/);
    const active = el.querySelector("[data-testid='range-24h']");
    assert.match(active.className, /active/, "the 24h button has the active class");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: renders single line when raw data shape", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-06T10:05:00Z", pct: 55, raw: 620 },
      { ts: "2026-05-06T10:10:00Z", pct: 52, raw: 610 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const svg = el.querySelector("[data-testid='chart-svg']");
    assert.ok(svg, "svg rendered");
    const line = svg.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "moisture line path present");
    const band = svg.querySelector("[data-testid='moisture-band']");
    assert.equal(band, null, "no band path when shape is raw");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: renders band + avg line when downsampled data shape", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    moisture: [
      { ts: "2026-04-06T10:00:00Z", pct_min: 30, pct_avg: 45, pct_max: 60, raw_avg: 600 },
      { ts: "2026-04-06T11:00:00Z", pct_min: 35, pct_avg: 50, pct_max: 65, raw_avg: 620 },
      { ts: "2026-04-06T12:00:00Z", pct_min: 40, pct_avg: 55, pct_max: 70, raw_avg: 640 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const svg = el.querySelector("[data-testid='chart-svg']");
    assert.ok(svg, "svg rendered");
    const band = svg.querySelector("[data-testid='moisture-band']");
    assert.ok(band, "band path present for downsampled shape");
    const line = svg.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "avg line path also present");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: clicking range button refetches with new range", async () => {
  const orig = _origFetch();
  const calls = [];
  _setMockFetch(async (url) => {
    calls.push(String(url));
    return _ok({ moisture: [], watering_events: [], phase_changes: [] });
  });
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    assert.equal(calls.length, 1, "one fetch on initial render");
    assert.match(calls[0], /\?range=24h/);
    const sevenDay = el.querySelector("[data-testid='range-7d']");
    sevenDay.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    assert.equal(calls.length, 2, "second fetch fires on click");
    assert.match(calls[1], /\?range=7d/, "second fetch uses range=7d");
    assert.match(sevenDay.className, /active/);
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: overlays watering events as vertical marks", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-06T12:00:00Z", pct: 55, raw: 620 },
    ],
    watering_events: [
      { ts: "2026-05-06T10:30:00Z", trigger: "auto",   duration_s: 5, soil_pct_before: 48 },
      { ts: "2026-05-06T11:00:00Z", trigger: "manual", duration_s: 8, soil_pct_before: 49 },
      { ts: "2026-05-06T11:30:00Z", trigger: "auto",   duration_s: 5, soil_pct_before: 50 },
    ],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const svg = el.querySelector("[data-testid='chart-svg']");
    const marks = svg.querySelectorAll(".watering-mark");
    assert.equal(marks.length, 3, "one vertical mark per watering event");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: shows target band horizontal line when overrides.watering_target is set", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-06T11:00:00Z", pct: 55, raw: 620 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    // Target set
    const elWith = renderMoistureHistoryChart(_unit({ watering_target: 55 }), { ownerDocument: document });
    await _flushMicro();
    const targetLine = elWith.querySelector("[data-testid='target-line']");
    assert.ok(targetLine, "target line drawn when watering_target is set");

    // Target null → no target line
    const elWithout = renderMoistureHistoryChart(_unit({ watering_target: null }), { ownerDocument: document });
    await _flushMicro();
    const noTarget = elWithout.querySelector("[data-testid='target-line']");
    assert.equal(noTarget, null, "no target line when watering_target is null");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: handles empty moisture array without crashing", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({ moisture: [], watering_events: [], phase_changes: [] }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const empty = el.querySelector("[data-testid='empty-state']");
    assert.ok(empty, "empty-state element rendered");
    assert.match(empty.textContent, /no data/i, "empty-state copy mentions no data");
    // No moisture line / band should be drawn for an empty array
    assert.equal(el.querySelector("[data-testid='moisture-line']"), null);
    assert.equal(el.querySelector("[data-testid='moisture-band']"), null);
  } finally {
    _setMockFetch(orig);
  }
});


// ────────────────────────────────────────────────────────────────────
// Uncalibrated-sensor rendering. The backend now flags responses with
// a top-level `calibrated` boolean; when false, the chart switches to
// a raw-only 0–1023 Y-axis and shows a banner explaining the fallback.
// The `calibrated === false` check is intentionally strict — an absent
// flag means a legacy backend and must render exactly as before.
// ────────────────────────────────────────────────────────────────────

test("moisture chart: renders raw line + banner when calibrated:false (downsampled)", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: false,
    moisture: [
      { ts: "2026-04-06T10:00:00Z", raw_avg: 318 },
      { ts: "2026-04-06T11:00:00Z", raw_avg: 322 },
      { ts: "2026-04-06T12:00:00Z", raw_avg: 320 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const line = el.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "raw line path present in uncalibrated mode");
    // No band in uncalibrated mode — we only have averages, not min/max.
    const band = el.querySelector("[data-testid='moisture-band']");
    assert.equal(band, null, "no band when calibrated:false (no min/max data)");
    const banner = el.querySelector("[data-testid='uncalibrated-banner']");
    assert.ok(banner, "uncalibrated banner element present");
    assert.match(banner.textContent, /uncalibrated/i, "banner mentions uncalibrated state");
    assert.match(banner.textContent, /0.*1023/, "banner shows the raw 0-1023 range");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: renders raw line + banner when calibrated:false (non-bucketed)", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: false,
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: null, raw: 315 },
      { ts: "2026-05-06T10:05:00Z", pct: null, raw: 318 },
      { ts: "2026-05-06T10:10:00Z", pct: null, raw: 320 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const line = el.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "raw line drawn from {raw} on each row");
    const banner = el.querySelector("[data-testid='uncalibrated-banner']");
    assert.ok(banner, "uncalibrated banner shown for short-range uncalibrated data too");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: omits target line when calibrated:false", async () => {
  // The target is expressed in pct (0-100); in uncalibrated mode the
  // Y-axis is raw 0-1023, so plotting the target would be misleading.
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: false,
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: null, raw: 315 },
      { ts: "2026-05-06T11:00:00Z", pct: null, raw: 320 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit({ watering_target: 55 }), { ownerDocument: document });
    await _flushMicro();
    const targetLine = el.querySelector("[data-testid='target-line']");
    assert.equal(targetLine, null, "target line suppressed in uncalibrated mode");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: legacy response (calibrated absent) still renders 0-100% chart", async () => {
  // Backward-compat — a deployed older mlss-monitor returns no `calibrated`
  // key. We must default to calibrated rendering (no banner, target line
  // shown). The detection check is `=== false`, not falsy.
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    // No `calibrated` key at all — simulates pre-fix backend.
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-06T11:00:00Z", pct: 55, raw: 620 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit({ watering_target: 55 }), { ownerDocument: document });
    await _flushMicro();
    const banner = el.querySelector("[data-testid='uncalibrated-banner']");
    assert.equal(banner, null, "no uncalibrated banner when calibrated key is absent");
    const targetLine = el.querySelector("[data-testid='target-line']");
    assert.ok(targetLine, "target line present (calibrated mode preserved)");
    const line = el.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "moisture line still rendered");
  } finally {
    _setMockFetch(orig);
  }
});

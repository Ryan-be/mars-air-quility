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


// ────────────────────────────────────────────────────────────────────
// Mixed buckets — the case that bit the user the moment they calibrated
// a previously-uncalibrated sensor. The window contains pre-calibration
// rows (pct=NULL) AND post-calibration rows (pct populated). The
// backend's downsampler emits raw_avg for every bucket but pct_* only
// for buckets that have at least one non-NULL pct. The chart used to
// iterate all rows assuming pct_avg was present everywhere — pctToY
// of undefined → NaN → SVG path renders as a blank "M PADDING NaN L
// ... NaN" string. The fix filters the calibrated render path to
// pct-bearing buckets only.
// ────────────────────────────────────────────────────────────────────

test("moisture chart: filters out pre-calibration buckets in calibrated mode", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      // Pre-calibration — only raw_avg
      { ts: "2026-05-17T08:00:00Z", raw_avg: 318 },
      { ts: "2026-05-17T09:00:00Z", raw_avg: 322 },
      // Post-calibration — full pct_* + raw_avg
      { ts: "2026-05-17T10:00:00Z", raw_avg: 800, pct_min: 60, pct_avg: 65, pct_max: 70 },
      { ts: "2026-05-17T11:00:00Z", raw_avg: 820, pct_min: 62, pct_avg: 68, pct_max: 75 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const line = el.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "moisture line still drawn from the pct-bearing buckets");
    // The bug: pctToY(undefined) silently produces NaN. Catch that by
    // asserting the `d` attribute has no NaN substring.
    assert.doesNotMatch(line.getAttribute("d"), /NaN/,
      "path d-attribute must not contain NaN — pre-calibration buckets " +
      "should be filtered, not iterated as if they had pct_avg");
    const band = el.querySelector("[data-testid='moisture-band']");
    assert.ok(band, "band still drawn from the pct_min/max-bearing buckets");
    assert.doesNotMatch(band.getAttribute("d"), /NaN/,
      "band d-attribute must not contain NaN");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: filters null-pct rows in calibrated short-range mode", async () => {
  // Non-bucketed path (≤600 rows). Backend emits `pct` always but it
  // can be null for pre-calibration rows. Chart must skip those so
  // pctToY(null) → 0 doesn't drag the line down to the bottom edge.
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-05-17T10:00:00Z", pct: null, raw: 318 },
      { ts: "2026-05-17T10:30:00Z", pct: null, raw: 320 },
      { ts: "2026-05-17T11:00:00Z", pct: 65, raw: 800 },
      { ts: "2026-05-17T11:30:00Z", pct: 68, raw: 820 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const line = el.querySelector("[data-testid='moisture-line']");
    assert.ok(line, "line drawn from the two post-calibration rows");
    assert.doesNotMatch(line.getAttribute("d"), /NaN/,
      "no NaN in path even though pct is null on early rows");
  } finally {
    _setMockFetch(orig);
  }
});


test("moisture chart: shows empty state when calibrated but no pct buckets in window", async () => {
  // Edge case: backend flagged calibrated=true (rows exist somewhere
  // with pct) but the current range window covers only pre-calibration
  // buckets. After filtering we have zero usable rows. Don't silently
  // draw nothing — show the helpful "no data" text so the user knows
  // why the chart is blank.
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-05-17T08:00:00Z", raw_avg: 318 },
      { ts: "2026-05-17T09:00:00Z", raw_avg: 322 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const empty = el.querySelector("[data-testid='empty-state']");
    assert.ok(empty, "empty-state shown when filtered list is empty");
    assert.equal(el.querySelector("[data-testid='moisture-line']"), null,
      "no moisture line when nothing to render");
  } finally {
    _setMockFetch(orig);
  }
});


// ────────────────────────────────────────────────────────────────────
// Chart anatomy — Y-axis spine, tick marks, axis titles, X-axis with
// time labels, and the legend below the SVG. The previous build drew
// only the data line/band; operators reported "no axis or legend or
// title" the moment values started rendering. These tests pin down
// the new chrome elements so future refactors don't strip them again.
// ────────────────────────────────────────────────────────────────────

test("chart anatomy: y-axis spine and tick labels render in calibrated mode", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-06T11:00:00Z", pct: 55, raw: 620 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    // Tick labels at 0/25/50/75/100 — the calibrated 5-tick scheme.
    for (const v of [0, 25, 50, 75, 100]) {
      const lbl = el.querySelector(`[data-testid='y-axis-label-${v}']`);
      assert.ok(lbl, `y-axis-label-${v} present`);
      assert.equal(lbl.textContent, String(v), `y-axis-label-${v} text = "${v}"`);
    }
    const title = el.querySelector("[data-testid='y-axis-title']");
    assert.ok(title, "y-axis-title element present");
    assert.match(title.textContent, /Moisture/i, "y-axis-title mentions Moisture");
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: y-axis labels switch to raw scale in uncalibrated mode", async () => {
  // RAW_AXIS_MAX is 1023, but tick density is the same 5-tick scheme.
  // Spec: 0/500/1000/1500/2000 if cleanly divisible; for 1023 we pick
  // the highest tick to land on RAW_AXIS_MAX (1023) and divide 0..1023
  // into 4 even steps — see the implementation.
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
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    const lbl0 = el.querySelector("[data-testid='y-axis-label-0']");
    assert.ok(lbl0, "y-axis-label-0 present in uncalibrated mode");
    assert.equal(lbl0.textContent, "0", "lowest tick is 0");
    // The highest tick corresponds to RAW_AXIS_MAX (1023). The data-testid
    // suffix encodes the tick value so we don't have to import the const.
    const lblMax = el.querySelector("[data-testid='y-axis-label-1023']");
    assert.ok(lblMax, "y-axis-label-1023 present (top tick matches RAW_AXIS_MAX)");
    assert.equal(lblMax.textContent, "1023");
    const title = el.querySelector("[data-testid='y-axis-title']");
    assert.ok(title, "y-axis-title present in uncalibrated mode");
    assert.match(title.textContent, /Raw/i, "uncalibrated y-axis title mentions Raw");
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: x-axis has 5 evenly-spaced time labels (HH:MM for 24h span)", async () => {
  const orig = _origFetch();
  // 24h-ish span: data at 10:00 and the next day at 10:00.
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-05-06T10:00:00Z", pct: 50, raw: 600 },
      { ts: "2026-05-07T10:00:00Z", pct: 55, raw: 620 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    for (let i = 0; i < 5; i++) {
      const lbl = el.querySelector(`[data-testid='x-axis-label-${i}']`);
      assert.ok(lbl, `x-axis-label-${i} present`);
      // 24h-or-less spans format as HH:MM (zero-padded).
      assert.match(lbl.textContent, /^\d{2}:\d{2}$/,
        `x-axis-label-${i} ("${lbl.textContent}") should match HH:MM`);
    }
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: x-axis label format adapts to longer 30d spans (DD MMM)", async () => {
  const orig = _origFetch();
  // ~30d span; labels should adopt the "DD MMM" format (e.g. "06 May").
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-04-06T10:00:00Z", pct_min: 30, pct_avg: 45, pct_max: 60, raw_avg: 600 },
      { ts: "2026-05-06T10:00:00Z", pct_min: 35, pct_avg: 50, pct_max: 65, raw_avg: 620 },
    ],
    watering_events: [],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    // At least one label must be in "DD MMM" form. We don't pin specific
    // labels — the exact dates depend on local time-zone bucket placement.
    let matched = false;
    for (let i = 0; i < 5; i++) {
      const lbl = el.querySelector(`[data-testid='x-axis-label-${i}']`);
      assert.ok(lbl, `x-axis-label-${i} present`);
      if (/^\d{2}\s\w{3}$/.test(lbl.textContent)) matched = true;
    }
    assert.ok(matched, "at least one x-axis label matches DD MMM for a 30d span");
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: legend in calibrated downsampled mode lists line + band + target + watering", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({
    calibrated: true,
    moisture: [
      { ts: "2026-04-06T10:00:00Z", pct_min: 30, pct_avg: 45, pct_max: 60, raw_avg: 600 },
      { ts: "2026-04-06T11:00:00Z", pct_min: 35, pct_avg: 50, pct_max: 65, raw_avg: 620 },
    ],
    watering_events: [
      { ts: "2026-04-06T10:30:00Z", trigger: "auto", duration_s: 5, soil_pct_before: 48 },
    ],
    phase_changes: [],
  }));
  try {
    const el = renderMoistureHistoryChart(_unit({ watering_target: 55 }), { ownerDocument: document });
    await _flushMicro();
    const legend = el.querySelector("[data-testid='chart-legend']");
    assert.ok(legend, "chart-legend wrapper present");
    for (const key of ["legend-line", "legend-band", "legend-target", "legend-watering"]) {
      assert.ok(legend.querySelector(`[data-testid='${key}']`), `${key} entry present`);
    }
    // Raw-only entry must NOT be in calibrated mode.
    assert.equal(legend.querySelector("[data-testid='legend-raw']"), null,
      "no legend-raw in calibrated mode");
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: legend in uncalibrated mode shows only Raw reading entry", async () => {
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
    // Target set, but should still be suppressed in uncalibrated mode.
    const el = renderMoistureHistoryChart(_unit({ watering_target: 55 }), { ownerDocument: document });
    await _flushMicro();
    const legend = el.querySelector("[data-testid='chart-legend']");
    assert.ok(legend, "chart-legend wrapper present in uncalibrated mode too");
    assert.ok(legend.querySelector("[data-testid='legend-raw']"),
      "legend-raw entry present");
    assert.equal(legend.querySelector("[data-testid='legend-target']"), null,
      "legend-target absent (target is suppressed in uncalibrated mode)");
    assert.equal(legend.querySelector("[data-testid='legend-band']"), null,
      "legend-band absent (no min/max data in uncalibrated mode)");
    assert.equal(legend.querySelector("[data-testid='legend-line']"), null,
      "legend-line absent — the raw line uses legend-raw");
  } finally {
    _setMockFetch(orig);
  }
});


test("chart anatomy: empty data still shows axes (just no line)", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok({ moisture: [], watering_events: [], phase_changes: [] }));
  try {
    const el = renderMoistureHistoryChart(_unit(), { ownerDocument: document });
    await _flushMicro();
    // Axes / title must coexist with the empty-state text. Operators
    // shouldn't see a totally blank chart area while data is being
    // collected — the axes give scale context immediately.
    assert.ok(el.querySelector("[data-testid='y-axis-title']"),
      "y-axis-title still rendered for empty data");
    assert.ok(el.querySelector("[data-testid='y-axis-label-0']"),
      "y-axis-label-0 still rendered for empty data");
    assert.ok(el.querySelector("[data-testid='empty-state']"),
      "empty-state text coexists with the axes");
    assert.equal(el.querySelector("[data-testid='moisture-line']"), null,
      "no data line for empty data");
  } finally {
    _setMockFetch(orig);
  }
});

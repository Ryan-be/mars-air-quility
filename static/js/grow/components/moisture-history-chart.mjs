/**
 * Long-range moisture history chart for the History tab (Task 3 of the
 * History-tab plan).
 *
 * Range selector: 24h / 7d / 30d / 90d / all. Default 24h. The button
 * the user clicks gets an `active` class; clicking the already-active
 * range is a no-op (no redundant fetch).
 *
 * The backend's GET /api/grow/units/<id>/history returns either:
 *   - raw rows  {ts, pct, raw}                                 (≤600 rows)
 *   - downsampled rows {ts, pct_min, pct_avg, pct_max, raw_avg} (>600 rows)
 * We sniff the shape via presence of `pct_avg` on the first row and
 * render accordingly:
 *   - raw → single SVG <path> line at pct
 *   - downsampled → SVG <path> band fill between pct_min and pct_max
 *                   plus an SVG <path> avg line on top
 *
 * Calibration modes — the response also carries a top-level `calibrated`
 * boolean:
 *   - calibrated === true (or absent, for backward compat with older
 *     mlss-monitor builds): render as above, Y-axis 0-100 %.
 *   - calibrated === false: a freshly-plugged-in Seesaw with no dry/wet
 *     calibration captured. pct is NULL for every row, so we fall back
 *     to raw values on a 0-1023 Y-axis (Seesaw raw range). In this mode
 *     the downsampled response carries only raw_avg (no min/max), so
 *     we draw a single line — no band fill. The target overlay is
 *     suppressed because the target is expressed in pct and would be
 *     meaningless on the raw axis. An "Uncalibrated" banner is rendered
 *     above the SVG so the user knows why the axis differs.
 *   The `=== false` check is intentionally strict — `undefined` falls
 *   through to the calibrated branch so legacy backend responses don't
 *   regress.
 *
 * Overlays:
 *   - Watering events: vertical green <line> marks at each event timestamp
 *   - Target band: dashed horizontal <line> at unit.overrides.watering_target
 *     (only when target is set AND calibrated; otherwise no overlay)
 *
 * Empty data: shows a centred "No data in this range" <text> element
 * rather than rendering an empty SVG with NaN-derived geometry. A
 * freshly-enrolled unit at range=all otherwise crashes on the
 * Math.min(...[]) → -Infinity scale arithmetic.
 *
 * Vanilla SVG via createElementNS — no Chart.js or D3 dependency, and
 * no innerHTML so the JSDOM tests get real Element instances and there's
 * no XSS surface for downstream telemetry data.
 *
 * `data-testid` attributes on every queryable element so tests aren't
 * coupled to className changes.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const RANGES = ["24h", "7d", "30d", "90d", "all"];

// SVG geometry — the viewBox is fixed; CSS scales it to the container width.
const W = 800;
const H = 240;
// Right/top/bottom padding stays small — the X-axis labels sit BELOW the
// plot area inside the bottom padding band, so 32px is enough for one line
// of 10px text. The LEFT padding is widened because the Y-axis tick labels
// (up to "1023" in uncalibrated mode) plus the rotated Y-axis title both
// need to live to the left of the plot area without colliding with the
// data line at x=0. 48px gives ~4 chars of tick text plus the rotated title.
const PADDING = 32;
const LEFT_PADDING = 48;

// Seesaw raw moisture reading is a 10-bit ADC value (0..1023). We pin the
// uncalibrated Y-axis to that full range rather than auto-scaling so users
// can eyeball the absolute number — handy when capturing dry/wet
// calibration points later.
const RAW_AXIS_MAX = 1023;

// Chart anatomy palette — hard-coded hex values rather than CSS variables
// because SVG attribute values can't reference CSS custom properties on
// every browser/JSDOM combo we test against. Colours mirror the
// --grow-* CSS variables defined in static/css/grow.css so the chart
// chrome reads as part of the existing UI.
const AXIS_STROKE = "#2a3d50";      // --grow-border-emph
const AXIS_TEXT_MUTED = "#7d92a8";  // --grow-text-muted (tick labels)
const AXIS_TEXT_BODY = "#c2d2e3";   // --grow-text-body  (axis title)
const MOISTURE_LINE_COLOR = "#4dacff";      // Brand blue; matches the data line
const MOISTURE_BAND_COLOR = "rgba(77, 172, 255, 0.18)";  // Same blue, faded
const TARGET_LINE_COLOR = "#56f000";        // Bright green dashed
const WATERING_MARK_COLOR = "#56f000";      // Same green vertical marks

// Day-of-week abbreviations for the medium-range x-axis format. Sunday=0
// to match JS's Date.getDay() return value.
const DOW_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
// Month abbreviations for the long-range x-axis format. January=0 to
// match Date.getMonth().
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Time-span thresholds (ms) that flip the x-axis label format. The cut-offs
// straddle the typical range-selector buttons (24h / 7d / 30d / 90d / all)
// so a user clicking "30d" sees DD-MMM labels and clicking "24h" sees
// HH:MM labels, without the chart having to know about the selector.
const X_LABEL_SPAN_SHORT_MS = 36 * 60 * 60 * 1000;  // < 36h → HH:MM
const X_LABEL_SPAN_MEDIUM_MS = 14 * 24 * 60 * 60 * 1000;  // < 14d → "Day HH:00"


/**
 * Build the moisture history chart panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `id`
 *                       and `overrides`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderMoistureHistoryChart(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.className = "du-panel hist-chart";
  wrap.dataset.testid = "moisture-chart";

  // ── header
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📈 Soil moisture history</span>";
  wrap.appendChild(head);

  // ── range selector
  let currentRange = "24h";
  const selector = doc.createElement("div");
  selector.className = "hist-range-selector";
  selector.dataset.testid = "range-selector";
  for (const r of RANGES) {
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.dataset.testid = `range-${r}`;
    btn.dataset.range = r;
    btn.textContent = r;
    btn.className = r === currentRange ? "active" : "";
    selector.appendChild(btn);
  }
  wrap.appendChild(selector);

  // ── chart container — re-rendered on every range change
  const chartHost = doc.createElement("div");
  chartHost.className = "hist-chart-host";
  chartHost.dataset.testid = "chart-host";
  wrap.appendChild(chartHost);

  // ── fetch + render helper
  async function loadAndRender(range) {
    chartHost.innerHTML = "";  // clear previous SVG (and any error text)
    let r;
    try {
      r = await fetch(`/api/grow/units/${unit.id}/history?range=${range}`);
    } catch (exc) {
      chartHost.textContent = "Network error";
      return;
    }
    if (!r.ok) {
      chartHost.textContent = "Failed to load";
      return;
    }
    const data = await r.json();
    // Banner sits above the SVG so it reads as a chart subtitle. Only
    // shown for explicit calibrated=false — undefined (legacy backend)
    // falls through to calibrated rendering with no banner.
    if (data.calibrated === false) {
      const banner = doc.createElement("div");
      banner.className = "hist-uncalibrated-banner";
      banner.dataset.testid = "uncalibrated-banner";
      banner.textContent = "Uncalibrated — raw readings (0–1023)";
      chartHost.appendChild(banner);
    }
    const { svg, legend } = _renderChartSvg(data, unit, doc);
    chartHost.appendChild(svg);
    // The legend sits OUTSIDE the SVG so the swatches can be styled with
    // ordinary HTML/CSS (and read by screen readers like normal text).
    // It's appended directly under the SVG inside chartHost.
    if (legend) chartHost.appendChild(legend);
  }

  // ── range button click handler — event-delegated on the selector
  selector.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-range]");
    if (!btn || btn.dataset.range === currentRange) return;
    currentRange = btn.dataset.range;
    selector.querySelectorAll("button").forEach((b) => {
      b.className = b.dataset.range === currentRange ? "active" : "";
    });
    loadAndRender(currentRange);
  });

  // ── kick off initial load. Fire-and-forget — the panel is usable
  //    immediately; the SVG appears when the fetch resolves.
  loadAndRender(currentRange);

  return wrap;
}


/**
 * Build the SVG + legend for a single rendered range. Stateless helper —
 * given the /history response and the unit (for the target overlay),
 * returns `{svg, legend}` where:
 *   - svg    : a fresh <svg> Element with all chart elements
 *   - legend : an HTMLElement (<div>) listing the visible series, or
 *              `null` if no legend should be shown (e.g. true empty
 *              response — there's nothing to label).
 *
 * The legend is split out of the SVG so its swatch text is real HTML
 * (screen-readable, copy-pastable, no foreignObject quirks).
 */
function _renderChartSvg(data, unit, doc) {
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "hist-chart-svg");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.dataset.testid = "chart-svg";

  const allMoisture = data.moisture || [];

  // Uncalibrated mode: strict === false. Undefined falls through to
  // calibrated rendering so legacy backend responses (no `calibrated`
  // key) keep working unchanged. We need this BEFORE the empty-state
  // branch because the axes (drawn in both branches) pick scale + title
  // based on calibration state.
  const isUncalibrated = data.calibrated === false;

  // ── axes are drawn FIRST in every branch (even empty data) so the
  //    user always has a scale to read against. The empty-state text
  //    sits centred on top of (but visually above) the axes.
  _drawYAxis(svg, doc, isUncalibrated);
  _drawXAxisSpine(svg, doc);

  // Empty-state short circuit. Avoid NaN scale arithmetic from Math.min on
  // an empty array, and give the user something to read. We've already
  // drawn the axes above so the chart isn't a totally blank rectangle.
  if (allMoisture.length === 0) {
    _appendEmptyState(svg, doc);
    // No legend for a truly empty response — there's nothing to label.
    return { svg, legend: null };
  }

  // Detect downsampled vs raw shape. The contract is the presence of
  // `pct_avg` on the first row (calibrated bucketed) OR `raw_avg`
  // (uncalibrated bucketed — pct_* keys are dropped entirely). Either
  // signals the bucketed shape; the non-bucketed shape uses {pct, raw}.
  // We compute this from the WHOLE response (pre-filter) because the
  // shape is uniform across all rows of a given response.
  const isDownsampled =
    allMoisture[0].pct_avg !== undefined || allMoisture[0].raw_avg !== undefined;

  // In CALIBRATED mode, filter to pct-bearing rows/buckets. The window
  // can include pre-calibration data (pct=null in non-bucketed shape,
  // pct_* keys absent in bucketed shape) — iterating that as if pct_avg
  // were defined gives `pctToY(undefined)` → NaN coordinates in the SVG
  // path, which silently renders as a blank chart. UX-wise the right
  // answer is "show only the calibrated timeline" — the pre-calibration
  // raw values can't be sensibly placed on the 0-100% axis anyway.
  // Uncalibrated mode keeps the full series (all rows have raw / raw_avg).
  const moisture = isUncalibrated
    ? allMoisture
    : allMoisture.filter((m) =>
        isDownsampled
          ? m.pct_avg !== undefined
          : (m.pct !== null && m.pct !== undefined)
      );

  // The filter can leave nothing — happens when calibrated=true (some
  // row somewhere has pct) but the current range window covers only
  // pre-calibration buckets. Render the empty state with the same copy
  // as a truly empty response so the user gets a clear signal rather
  // than a mysteriously blank SVG.
  if (moisture.length === 0) {
    _appendEmptyState(svg, doc);
    return { svg, legend: null };
  }

  // ── X scale: linear interpolation across the time domain. The data
  //    starts at LEFT_PADDING (wider than top/right/bottom PADDING) to
  //    leave room for the Y-axis tick labels and the rotated axis title.
  const tsValues = moisture.map((m) => new Date(m.ts).getTime());
  const tMin = Math.min(...tsValues);
  const tMax = Math.max(...tsValues);
  // Guard against single-point series → divide-by-zero. Pin denominator to 1
  // so the lone point lands at the left edge rather than NaN.
  const tSpan = (tMax - tMin) || 1;
  const tToX = (t) => LEFT_PADDING + ((t - tMin) / tSpan) * (W - LEFT_PADDING - PADDING);

  // ── Y scale: 0–100 % pct in calibrated mode, 0–1023 raw in uncalibrated.
  //    Both are inverted (high value → low Y).
  const pctToY = (p) => H - PADDING - (p / 100) * (H - 2 * PADDING);
  const rawToY = (r) => H - PADDING - (r / RAW_AXIS_MAX) * (H - 2 * PADDING);

  // ── X-axis time labels. Drawn now (rather than alongside the spine in
  //    _drawXAxisSpine) because they depend on tMin/tMax/tSpan, which
  //    are computed from the moisture series.
  _drawXAxisLabels(svg, doc, tMin, tMax);

  // ── target band (dashed horizontal). Drawn first so the moisture line
  //    sits on top. Suppressed entirely in uncalibrated mode — the
  //    target is in pct and would lie at a meaningless position on the
  //    raw axis.
  const target = unit.overrides && typeof unit.overrides.watering_target === "number"
    ? unit.overrides.watering_target
    : null;
  const targetDrawn = target !== null && !isUncalibrated;
  if (targetDrawn) {
    const line = doc.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(LEFT_PADDING));
    line.setAttribute("x2", String(W - PADDING));
    line.setAttribute("y1", String(pctToY(target)));
    line.setAttribute("y2", String(pctToY(target)));
    line.setAttribute("stroke", TARGET_LINE_COLOR);
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "4 4");
    line.setAttribute("opacity", "0.6");
    line.dataset.testid = "target-line";
    svg.appendChild(line);
  }

  // Track which renderable pieces actually got drawn so the legend
  // reflects the same. The booleans are toggled below as each branch
  // emits its elements.
  let lineDrawn = false;
  let bandDrawn = false;

  // ── moisture line / band
  if (isUncalibrated) {
    // Raw-only single line. Source field depends on shape:
    //   - bucketed:    m.raw_avg (no min/max available, so no band)
    //   - non-bucketed: m.raw
    const rawAt = (m) => (m.raw_avg !== undefined ? m.raw_avg : m.raw);
    let d = `M ${tToX(tsValues[0])} ${rawToY(rawAt(moisture[0]))}`;
    for (let i = 1; i < moisture.length; i++) {
      d += ` L ${tToX(tsValues[i])} ${rawToY(rawAt(moisture[i]))}`;
    }
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", MOISTURE_LINE_COLOR);
    path.setAttribute("stroke-width", "2");
    path.dataset.testid = "moisture-line";
    svg.appendChild(path);
    // Uncalibrated line is labelled "Raw reading" in the legend; we
    // track it via a dedicated flag rather than `lineDrawn` so the
    // legend can pick the right testid (`legend-raw` vs `legend-line`).
  } else if (isDownsampled) {
    // Band fill — closed polygon traced along pct_max forward then pct_min
    // backward. This is the standard "envelope" technique for SVG paths.
    let dBand = `M ${tToX(tsValues[0])} ${pctToY(moisture[0].pct_max)}`;
    for (let i = 1; i < moisture.length; i++) {
      dBand += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct_max)}`;
    }
    for (let i = moisture.length - 1; i >= 0; i--) {
      dBand += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct_min)}`;
    }
    dBand += " Z";
    const band = doc.createElementNS(SVG_NS, "path");
    band.setAttribute("d", dBand);
    band.setAttribute("fill", MOISTURE_BAND_COLOR);
    band.setAttribute("stroke", "none");
    band.dataset.testid = "moisture-band";
    svg.appendChild(band);
    bandDrawn = true;

    // Avg line on top of the band
    let dLine = `M ${tToX(tsValues[0])} ${pctToY(moisture[0].pct_avg)}`;
    for (let i = 1; i < moisture.length; i++) {
      dLine += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct_avg)}`;
    }
    const line = doc.createElementNS(SVG_NS, "path");
    line.setAttribute("d", dLine);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", MOISTURE_LINE_COLOR);
    line.setAttribute("stroke-width", "2");
    line.dataset.testid = "moisture-line";
    svg.appendChild(line);
    lineDrawn = true;
  } else {
    // Raw shape — single line at pct.
    let d = `M ${tToX(tsValues[0])} ${pctToY(moisture[0].pct)}`;
    for (let i = 1; i < moisture.length; i++) {
      d += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct)}`;
    }
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", MOISTURE_LINE_COLOR);
    path.setAttribute("stroke-width", "2");
    path.dataset.testid = "moisture-line";
    svg.appendChild(path);
    lineDrawn = true;
  }

  // ── watering events as vertical marks. Drawn last so they sit on top
  //    of the moisture line for visibility.
  const wateringEvents = data.watering_events || [];
  for (const ev of wateringEvents) {
    const x = tToX(new Date(ev.ts).getTime());
    const mark = doc.createElementNS(SVG_NS, "line");
    mark.setAttribute("x1", String(x));
    mark.setAttribute("x2", String(x));
    mark.setAttribute("y1", String(PADDING));
    mark.setAttribute("y2", String(H - PADDING));
    mark.setAttribute("stroke", WATERING_MARK_COLOR);
    mark.setAttribute("stroke-width", "1");
    mark.setAttribute("opacity", "0.6");
    mark.classList.add("watering-mark");
    svg.appendChild(mark);
  }

  // ── build the legend now that we know which series are visible.
  const legend = _buildLegend(doc, {
    isUncalibrated,
    lineDrawn,
    bandDrawn,
    targetDrawn,
    wateringDrawn: wateringEvents.length > 0,
  });

  return { svg, legend };
}


// ────────────────────────────────────────────────────────────────────
// Chart anatomy helpers — split out so the main render fn doesn't grow
// past 200 lines. All helpers append directly to the SVG that's passed
// in (mutating side-effect) and return void.
// ────────────────────────────────────────────────────────────────────

/**
 * Centred "No data in this range" text. Extracted because both the
 * truly-empty and the post-filter-empty branches use the same copy.
 */
function _appendEmptyState(svg, doc) {
  const txt = doc.createElementNS(SVG_NS, "text");
  txt.setAttribute("x", String(W / 2));
  txt.setAttribute("y", String(H / 2));
  txt.setAttribute("text-anchor", "middle");
  txt.setAttribute("dominant-baseline", "middle");
  txt.setAttribute("fill", AXIS_TEXT_MUTED);
  txt.setAttribute("font-size", "14");
  txt.dataset.testid = "empty-state";
  txt.textContent = "No data in this range";
  svg.appendChild(txt);
}


/**
 * Y-axis spine + 5 tick marks + tick labels + rotated axis title.
 * Calibrated mode → 0/25/50/75/100 (pct). Uncalibrated → 0/256/512/768/1023
 * (4 evenly-spaced steps across the 0..RAW_AXIS_MAX range, with the
 * highest tick pinned exactly to RAW_AXIS_MAX so the top label reads
 * "1023" — the canonical Seesaw raw maximum).
 */
function _drawYAxis(svg, doc, isUncalibrated) {
  const topY = PADDING;
  const botY = H - PADDING;

  // Spine: vertical line at x=LEFT_PADDING (the left edge of the plot area)
  const spine = doc.createElementNS(SVG_NS, "line");
  spine.setAttribute("x1", String(LEFT_PADDING));
  spine.setAttribute("x2", String(LEFT_PADDING));
  spine.setAttribute("y1", String(topY));
  spine.setAttribute("y2", String(botY));
  spine.setAttribute("stroke", AXIS_STROKE);
  spine.setAttribute("stroke-width", "1");
  spine.dataset.testid = "y-axis-spine";
  svg.appendChild(spine);

  // Tick values + the Y-coord mapping function for placing them. The
  // mapping mirrors pctToY / rawToY but is local so the empty-state
  // branch (which doesn't compute the data scales) can still use it.
  let tickValues;
  let valueToY;
  if (isUncalibrated) {
    // Four evenly-spaced steps; floor to integers so labels are clean.
    // Highest tick pinned to RAW_AXIS_MAX exactly so the top label
    // reads "1023" — matches the banner copy.
    tickValues = [0,
                  Math.round(RAW_AXIS_MAX * 0.25),
                  Math.round(RAW_AXIS_MAX * 0.5),
                  Math.round(RAW_AXIS_MAX * 0.75),
                  RAW_AXIS_MAX];
    valueToY = (v) => H - PADDING - (v / RAW_AXIS_MAX) * (H - 2 * PADDING);
  } else {
    tickValues = [0, 25, 50, 75, 100];
    valueToY = (v) => H - PADDING - (v / 100) * (H - 2 * PADDING);
  }

  for (const v of tickValues) {
    const y = valueToY(v);
    // Tick mark: short horizontal line jutting LEFT of the spine, so it
    // sits in the padding band rather than over the data area.
    const tick = doc.createElementNS(SVG_NS, "line");
    tick.setAttribute("x1", String(LEFT_PADDING - 4));
    tick.setAttribute("x2", String(LEFT_PADDING));
    tick.setAttribute("y1", String(y));
    tick.setAttribute("y2", String(y));
    tick.setAttribute("stroke", AXIS_STROKE);
    tick.setAttribute("stroke-width", "1");
    svg.appendChild(tick);

    // Tick label: right-aligned to the left of the tick mark. The
    // data-testid suffix is the tick VALUE (not its index) so tests
    // can assert exact text without depending on tick ordering.
    const lbl = doc.createElementNS(SVG_NS, "text");
    lbl.setAttribute("x", String(LEFT_PADDING - 6));
    lbl.setAttribute("y", String(y));
    lbl.setAttribute("text-anchor", "end");
    lbl.setAttribute("dominant-baseline", "middle");
    lbl.setAttribute("fill", AXIS_TEXT_MUTED);
    lbl.setAttribute("font-size", "10");
    lbl.dataset.testid = `y-axis-label-${v}`;
    lbl.textContent = String(v);
    svg.appendChild(lbl);
  }

  // Rotated axis title — vertical, reading bottom-to-top, centred on
  // the axis midpoint. SVG rotate-about-point convention is
  // `rotate(deg, cx, cy)`.
  const titleY = (topY + botY) / 2;
  const titleX = 12;  // sits just inside the left edge of the viewBox
  const title = doc.createElementNS(SVG_NS, "text");
  title.setAttribute("x", String(titleX));
  title.setAttribute("y", String(titleY));
  title.setAttribute("text-anchor", "middle");
  title.setAttribute("dominant-baseline", "middle");
  title.setAttribute("fill", AXIS_TEXT_BODY);
  title.setAttribute("font-size", "11");
  title.setAttribute("transform", `rotate(-90, ${titleX}, ${titleY})`);
  title.dataset.testid = "y-axis-title";
  title.textContent = isUncalibrated ? "Raw reading" : "Moisture (%)";
  svg.appendChild(title);
}


/**
 * X-axis spine. The time labels are drawn separately (after the data is
 * processed) because they depend on the time domain, which the empty-
 * state branch doesn't compute.
 */
function _drawXAxisSpine(svg, doc) {
  const y = H - PADDING;
  const spine = doc.createElementNS(SVG_NS, "line");
  spine.setAttribute("x1", String(LEFT_PADDING));
  spine.setAttribute("x2", String(W - PADDING));
  spine.setAttribute("y1", String(y));
  spine.setAttribute("y2", String(y));
  spine.setAttribute("stroke", AXIS_STROKE);
  spine.setAttribute("stroke-width", "1");
  spine.dataset.testid = "x-axis-spine";
  svg.appendChild(spine);
}


/**
 * 5 evenly-spaced x-axis tick marks + time labels. Label format adapts
 * to the time span:
 *   span < 36h   → "HH:MM"        (e.g. "14:30")
 *   span < 14d   → "Day HH:00"    (e.g. "Fri 14:00")
 *   span ≥ 14d   → "DD MMM"       (e.g. "06 May")
 *
 * data-testid is positional ("x-axis-label-0".."x-axis-label-4") because
 * the label values are time strings that vary per response — assertion
 * by exact text doesn't make sense.
 */
function _drawXAxisLabels(svg, doc, tMin, tMax) {
  const spineY = H - PADDING;
  const span = tMax - tMin;
  const formatTick = _xAxisFormatter(span);
  const N = 5;
  // Single-point series: the span is 0 (or 1 after the guard above) and
  // all 5 ticks would collapse onto the left edge. Skip the inner
  // ticks in that case — just label the start position.
  const denom = (N - 1) || 1;
  for (let i = 0; i < N; i++) {
    const t = tMin + (span * i) / denom;
    const x = LEFT_PADDING + ((W - LEFT_PADDING - PADDING) * i) / denom;

    // Tick mark: short vertical line jutting DOWN, sitting in the
    // bottom padding band.
    const tick = doc.createElementNS(SVG_NS, "line");
    tick.setAttribute("x1", String(x));
    tick.setAttribute("x2", String(x));
    tick.setAttribute("y1", String(spineY));
    tick.setAttribute("y2", String(spineY + 4));
    tick.setAttribute("stroke", AXIS_STROKE);
    tick.setAttribute("stroke-width", "1");
    svg.appendChild(tick);

    // Label: centred under the tick. dominant-baseline=hanging puts
    // the TOP of the text at the y coordinate, so the label sits
    // below the tick mark.
    const lbl = doc.createElementNS(SVG_NS, "text");
    lbl.setAttribute("x", String(x));
    lbl.setAttribute("y", String(spineY + 8));
    lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("dominant-baseline", "hanging");
    lbl.setAttribute("fill", AXIS_TEXT_MUTED);
    lbl.setAttribute("font-size", "10");
    lbl.dataset.testid = `x-axis-label-${i}`;
    lbl.textContent = formatTick(new Date(t));
    svg.appendChild(lbl);
  }
}


/**
 * Pick the right Date→string formatter for the x-axis labels based on
 * the visible span. Returns a function `(date) => string`.
 *
 * All formatting via vanilla Date methods — no moment.js / date-fns
 * dependency. Hours and minutes are zero-padded; day-of-month is
 * zero-padded too so the labels stay column-aligned in monospace.
 */
function _xAxisFormatter(spanMs) {
  const pad = (n) => String(n).padStart(2, "0");
  if (spanMs < X_LABEL_SPAN_SHORT_MS) {
    // < 36h: "HH:MM" — most users on a 24h view are reading individual
    // measurement timings down to the minute.
    return (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  if (spanMs < X_LABEL_SPAN_MEDIUM_MS) {
    // < 14d: "Day HH:00" — 7d view has 1-hour granularity at best, so
    // we surface the day-of-week (a 7d chart's most useful axis) plus
    // the hour. Minutes are always :00 because the buckets sit on the
    // hour by the time we reach this span.
    return (d) => `${DOW_NAMES[d.getDay()]} ${pad(d.getHours())}:00`;
  }
  // ≥ 14d: "DD MMM" — 30/90/all-time views show calendar dates; the
  // time-of-day is noise at this granularity.
  return (d) => `${pad(d.getDate())} ${MONTH_NAMES[d.getMonth()]}`;
}


/**
 * Inline legend below the SVG. Each entry is a horizontal row with a
 * small SVG swatch (matching the line/band/dash colour exactly) + a
 * text label. Built with createElementNS for the swatch markup so the
 * test suite gets real Element instances (no innerHTML).
 *
 * Returns the wrapper <div>, or `null` if there are no visible series
 * to label (e.g. an empty response — no point showing an empty legend).
 *
 * @param {object} flags  { isUncalibrated, lineDrawn, bandDrawn,
 *                          targetDrawn, wateringDrawn }
 */
function _buildLegend(doc, flags) {
  const { isUncalibrated, lineDrawn, bandDrawn, targetDrawn, wateringDrawn } = flags;

  // Decide which entries to emit. In uncalibrated mode we replace the
  // standard line/band/target trio with a single "Raw reading" entry
  // — the band isn't drawn (no min/max), the target is suppressed, so
  // the only thing on screen is the raw line. The raw branch always
  // emits the line (and only reaches _buildLegend if moisture is
  // non-empty), so we don't gate on lineDrawn here.
  const entries = [];
  if (isUncalibrated) {
    entries.push({ testid: "legend-raw", label: "Raw reading",
                   swatch: "line", color: MOISTURE_LINE_COLOR });
  } else {
    if (lineDrawn) {
      entries.push({ testid: "legend-line", label: "Moisture average",
                     swatch: "line", color: MOISTURE_LINE_COLOR });
    }
    if (bandDrawn) {
      entries.push({ testid: "legend-band", label: "Moisture range",
                     swatch: "band", color: MOISTURE_BAND_COLOR });
    }
    if (targetDrawn) {
      entries.push({ testid: "legend-target", label: "Watering target",
                     swatch: "dash", color: TARGET_LINE_COLOR });
    }
    if (wateringDrawn) {
      entries.push({ testid: "legend-watering", label: "Watering event",
                     swatch: "vline", color: WATERING_MARK_COLOR });
    }
  }

  if (entries.length === 0) return null;

  const wrap = doc.createElement("div");
  wrap.className = "hist-chart-legend";
  wrap.dataset.testid = "chart-legend";

  for (const e of entries) {
    const row = doc.createElement("span");
    row.className = "hist-chart-legend-entry";
    row.dataset.testid = e.testid;

    // Swatch: a 14x10 inline SVG containing the appropriate shape.
    // Inline SVG (rather than a CSS-styled <span>) keeps the swatch
    // visually identical to the data on the chart even if the page CSS
    // hasn't loaded yet.
    const swatch = doc.createElementNS(SVG_NS, "svg");
    swatch.setAttribute("width", "14");
    swatch.setAttribute("height", "10");
    swatch.setAttribute("viewBox", "0 0 14 10");
    swatch.style.verticalAlign = "middle";
    swatch.style.marginRight = "4px";
    _appendSwatchShape(swatch, doc, e.swatch, e.color);
    row.appendChild(swatch);

    // Label text node directly — no inner <span> needed.
    row.appendChild(doc.createTextNode(e.label));
    wrap.appendChild(row);
  }

  return wrap;
}


/**
 * Draw the visual swatch shape inside the inline SVG element. Variants:
 *   "line"  → 12×2 solid horizontal line (avg / raw lines)
 *   "band"  → 12×8 translucent filled rectangle (moisture range band)
 *   "dash"  → 12×2 dashed horizontal line (target line)
 *   "vline" → 2×8 vertical bar (watering event mark)
 */
function _appendSwatchShape(swatch, doc, kind, color) {
  if (kind === "band") {
    const rect = doc.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", "1");
    rect.setAttribute("y", "1");
    rect.setAttribute("width", "12");
    rect.setAttribute("height", "8");
    rect.setAttribute("fill", color);
    swatch.appendChild(rect);
  } else if (kind === "vline") {
    const line = doc.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", "7");
    line.setAttribute("x2", "7");
    line.setAttribute("y1", "1");
    line.setAttribute("y2", "9");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "2");
    line.setAttribute("opacity", "0.6");
    swatch.appendChild(line);
  } else {
    // "line" or "dash" — both horizontal lines down the middle.
    const line = doc.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", "1");
    line.setAttribute("x2", "13");
    line.setAttribute("y1", "5");
    line.setAttribute("y2", "5");
    line.setAttribute("stroke", color);
    line.setAttribute("stroke-width", "2");
    if (kind === "dash") {
      line.setAttribute("stroke-dasharray", "3 2");
      line.setAttribute("opacity", "0.6");
    }
    swatch.appendChild(line);
  }
}

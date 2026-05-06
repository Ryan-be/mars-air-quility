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
 * Overlays:
 *   - Watering events: vertical green <line> marks at each event timestamp
 *   - Target band: dashed horizontal <line> at unit.overrides.watering_target
 *     (only when target is set; otherwise no overlay)
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
const PADDING = 32;


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
    chartHost.appendChild(_renderChartSvg(data, unit, doc));
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
 * Build the SVG for a single rendered range. Stateless helper — given the
 * /history response and the unit (for the target overlay), returns a fresh
 * <svg> Element.
 */
function _renderChartSvg(data, unit, doc) {
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "hist-chart-svg");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.dataset.testid = "chart-svg";

  const moisture = data.moisture || [];

  // Empty-state short circuit. Avoid NaN scale arithmetic from Math.min on
  // an empty array, and give the user something to read.
  if (moisture.length === 0) {
    const txt = doc.createElementNS(SVG_NS, "text");
    txt.setAttribute("x", String(W / 2));
    txt.setAttribute("y", String(H / 2));
    txt.setAttribute("text-anchor", "middle");
    txt.setAttribute("dominant-baseline", "middle");
    txt.setAttribute("fill", "#7d92a8");
    txt.setAttribute("font-size", "14");
    txt.dataset.testid = "empty-state";
    txt.textContent = "No data in this range";
    svg.appendChild(txt);
    return svg;
  }

  // Detect downsampled vs raw shape. The contract is the presence of
  // `pct_avg` (which only the bucket-aggregator emits).
  const isDownsampled = moisture[0].pct_avg !== undefined;

  // ── X scale: linear interpolation across the time domain.
  const tsValues = moisture.map((m) => new Date(m.ts).getTime());
  const tMin = Math.min(...tsValues);
  const tMax = Math.max(...tsValues);
  // Guard against single-point series → divide-by-zero. Pin denominator to 1
  // so the lone point lands at the left edge rather than NaN.
  const tSpan = (tMax - tMin) || 1;
  const tToX = (t) => PADDING + ((t - tMin) / tSpan) * (W - 2 * PADDING);

  // ── Y scale: 0–100 % pct, inverted (high pct → low Y).
  const pctToY = (p) => H - PADDING - (p / 100) * (H - 2 * PADDING);

  // ── target band (dashed horizontal). Drawn first so the moisture line
  //    sits on top.
  const target = unit.overrides && typeof unit.overrides.watering_target === "number"
    ? unit.overrides.watering_target
    : null;
  if (target !== null) {
    const line = doc.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(PADDING));
    line.setAttribute("x2", String(W - PADDING));
    line.setAttribute("y1", String(pctToY(target)));
    line.setAttribute("y2", String(pctToY(target)));
    line.setAttribute("stroke", "#56f000");
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "4 4");
    line.setAttribute("opacity", "0.6");
    line.dataset.testid = "target-line";
    svg.appendChild(line);
  }

  // ── moisture line / band
  if (isDownsampled) {
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
    band.setAttribute("fill", "rgba(77, 172, 255, 0.18)");
    band.setAttribute("stroke", "none");
    band.dataset.testid = "moisture-band";
    svg.appendChild(band);

    // Avg line on top of the band
    let dLine = `M ${tToX(tsValues[0])} ${pctToY(moisture[0].pct_avg)}`;
    for (let i = 1; i < moisture.length; i++) {
      dLine += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct_avg)}`;
    }
    const line = doc.createElementNS(SVG_NS, "path");
    line.setAttribute("d", dLine);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#4dacff");
    line.setAttribute("stroke-width", "2");
    line.dataset.testid = "moisture-line";
    svg.appendChild(line);
  } else {
    // Raw shape — single line at pct.
    let d = `M ${tToX(tsValues[0])} ${pctToY(moisture[0].pct)}`;
    for (let i = 1; i < moisture.length; i++) {
      d += ` L ${tToX(tsValues[i])} ${pctToY(moisture[i].pct)}`;
    }
    const path = doc.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#4dacff");
    path.setAttribute("stroke-width", "2");
    path.dataset.testid = "moisture-line";
    svg.appendChild(path);
  }

  // ── watering events as vertical marks. Drawn last so they sit on top
  //    of the moisture line for visibility.
  for (const ev of (data.watering_events || [])) {
    const x = tToX(new Date(ev.ts).getTime());
    const mark = doc.createElementNS(SVG_NS, "line");
    mark.setAttribute("x1", String(x));
    mark.setAttribute("x2", String(x));
    mark.setAttribute("y1", String(PADDING));
    mark.setAttribute("y2", String(H - PADDING));
    mark.setAttribute("stroke", "#56f000");
    mark.setAttribute("stroke-width", "1");
    mark.setAttribute("opacity", "0.6");
    mark.classList.add("watering-mark");
    svg.appendChild(mark);
  }

  return svg;
}

import { isLight, themeLayout } from './theme.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function vpdColour(vpd) {
  if (vpd == null) return "#555";
  if (vpd < 0.4)  return "#3b82f6";
  if (vpd < 0.8)  return "#22c55e";
  if (vpd < 1.2)  return "#16a34a";
  if (vpd < 1.6)  return "#f59e0b";
  return "#ef4444";
}

function linearRegression(x, y) {
  const n = x.length;
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) {
    sumX  += x[i];  sumY  += y[i];
    sumXY += x[i] * y[i];
    sumX2 += x[i] * x[i];
  }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) return { slope: 0, intercept: 0, r2: 0 };
  const slope     = (n * sumXY - sumX * sumY) / denom;
  const intercept = (sumY - slope * sumX) / n;
  const meanY     = sumY / n;
  const ssTot     = y.reduce((s, yi) => s + (yi - meanY) ** 2, 0);
  const ssRes     = y.reduce((s, yi, i) => s + (yi - (slope * x[i] + intercept)) ** 2, 0);
  const r2        = ssTot > 0 ? 1 - ssRes / ssTot : 0;
  return { slope, intercept, r2 };
}

function avg(arr) {
  const valid = arr.filter(v => v != null);
  return valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
}

function trendSlope(values) {
  // simple linear slope over array indices
  const v = values.filter(x => x != null);
  if (v.length < 2) return 0;
  const n = v.length;
  const xMean = (n - 1) / 2;
  const yMean = v.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    num += (i - xMean) * (v[i] - yMean);
    den += (i - xMean) ** 2;
  }
  return den > 0 ? num / den : 0;
}

const CORR_CHANNEL_META = {
  tvoc_ppb:    { field: 'tvoc',      label: 'TVOC',             unit: 'ppb',  color: '#8b5cf6' },
  eco2_ppm:    { field: 'eco2',      label: 'eCO₂',             unit: 'ppm',  color: '#06b6d4' },
  temperature_c: { field: 'temperature', label: 'Temperature',      unit: '°C',   color: '#f97316' },
  humidity_pct:  { field: 'humidity',    label: 'Humidity',         unit: '%',    color: '#3b82f6' },
  pm1_ug_m3:   { field: 'pm1_0',     label: 'PM1',              unit: 'µg/m³', color: '#84cc16' },
  pm25_ug_m3:  { field: 'pm2_5',     label: 'PM2.5',            unit: 'µg/m³', color: '#22c55e' },
  pm10_ug_m3:  { field: 'pm10',      label: 'PM10',             unit: 'µg/m³', color: '#a3e635' },
  co_ppb:      { field: 'gas_co',    label: 'CO (resistance)',   unit: 'Ω',    color: '#ef4444' },
  no2_ppb:     { field: 'gas_no2',   label: 'NO₂ (resistance)',  unit: 'Ω',    color: '#f59e0b' },
  nh3_ppb:     { field: 'gas_nh3',   label: 'NH₃ (resistance)',  unit: 'Ω',    color: '#ec4899' },
};

function _getActiveCorrChannels() {
  return new Set(Array.from(document.querySelectorAll('.channel-chip[data-group].active')).map(c => c.dataset.channel));
}

function _normalizeChannel(values) {
  const numeric = values.filter(v => v != null);
  if (!numeric.length) return values.map(() => null);
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  if (min === max) return values.map(v => v != null ? 0.5 : null);
  return values.map(v => v != null ? (v - min) / (max - min) : null);
}

// ── State ────────────────────────────────────────────────────────────────────

let _fullData = [];
let _isSubset = false;
let _corrSelectedRange = { start: null, end: null };

export function getSelectedAnalysisRange() {
  return _corrSelectedRange;
}

// ── Public entry point ───────────────────────────────────────────────────────

export async function renderCorrelationCharts(data) {
  if (!data || data.length === 0) return;
  _fullData = data;
  _isSubset = false;

  // Fetch anomaly overlay data
  try {
    const now = new Date();
    const start = data.length ? new Date(data[0].timestamp).toISOString() : new Date(now.getTime() - 86400000).toISOString();
    const end   = now.toISOString();
    const ctxResp = await fetch(`/api/history/ml-context?start=${start}&end=${end}`);
    if (ctxResp.ok) {
      const ctxData = await ctxResp.json();
      const { shapes, hoverTrace } = _buildAnomalyOverlay(ctxData.inferences || []);
      _corrOverlayShapes = shapes;
      _renderBrushChart(data, shapes, hoverTrace);
    } else {
      _renderBrushChart(data);
    }
  } catch (e) {
    _renderBrushChart(data);
  }
  _renderScatterCharts(data);
  _renderInferencePanel(data, data);


  // Reset button — show immediate feedback then defer the heavy Plotly work
  const resetBtn = document.getElementById("corrResetBtn");
  if (resetBtn) {
    resetBtn.onclick = () => {
      if (resetBtn.disabled) return;
      resetBtn.disabled = true;
      resetBtn.textContent = "Resetting…";
      document.getElementById("corrRangeLabel").textContent = "Resetting to full range…";
      _corrSelectedRange = { start: null, end: null };
      
      setTimeout(() => {
        _renderBrushChart(_fullData, _corrOverlayShapes.length ? _corrOverlayShapes : undefined);
        _renderScatterCharts(_fullData);
        _renderInferencePanel(_fullData, _fullData);
        
        // Hide analysis panel and range tagging when resetting to full range
        const analysisPanel = document.getElementById('corrAnalysisPanel');
        if (analysisPanel) {
          analysisPanel.style.display = 'none';
        }
        const tagSection = document.getElementById('corrRangeTagSection');
        if (tagSection) {
          tagSection.style.display = 'none';
        }
        
        document.getElementById("corrRangeLabel").textContent = "Showing: full range";
        resetBtn.textContent = "Reset to full range";
        resetBtn.disabled = false;
      });
    };
  }
}

// ── Live data update (preserves user zoom) ───────────────────────────────────
//
// Called on each polling cycle instead of renderCorrelationCharts so that the
// brush chart zoom set by the user is not destroyed by a full re-render.
// Uses Plotly.restyle to swap trace x/y data without touching the layout
// (which holds the current xaxis.range / zoom state).
//
export function updateCorrelationData(data) {
  if (!data || data.length === 0) return;
  _fullData = data;

  const brushEl = document.getElementById("corrBrushPlot");
  if (brushEl && brushEl.data && brushEl.data.length > 0) {
    const ts = data.map(d => new Date(d.timestamp));
    const update = { x: [], y: [], customdata: [] };
    const traceIndices = [];

    brushEl.data.forEach(function (trace, idx) {
      if (!trace.meta || trace.name === 'detections') return;
      const channel = trace.meta;
      const meta = CORR_CHANNEL_META[channel];
      if (!meta) return;
      const values = data.map(d => d[meta.field]);
      const normalized = _normalizeChannel(values);
      update.x.push(ts);
      update.y.push(normalized);
      update.customdata.push(values);
      traceIndices.push(idx);
    });

    if (traceIndices.length) {
      Plotly.restyle(brushEl, update, traceIndices);
    }
  }

  // When the user hasn't zoomed in, keep the scatter plots and inference panel
  // current.  When they have zoomed in, leave those panels showing their
  // selection — they will update naturally when they reset or re-select.
  if (!_isSubset) {
    _renderScatterCharts(data);
    _renderInferencePanel(data, data);
  }
}

// ── Brush chart (compact selected channels over time) ───────────────────────

function _renderBrushChart(data, overlayShapes, hoverTrace) {
  const ts = data.map(d => new Date(d.timestamp));
  const activeChannels = _getActiveCorrChannels();
  const titleFont = { color: isLight ? "#111" : "#b0bec5" };

  const traces = Object.entries(CORR_CHANNEL_META).map(([channel, meta]) => {
    const values = data.map(d => d[meta.field]);
    if (!values.some(v => v != null)) return null;
    const normalized = _normalizeChannel(values);
    return {
      x: ts,
      y: normalized,
      mode: "lines",
      name: `${meta.label} (${meta.unit})`,
      line: { color: meta.color, width: 1.5 },
      hovertemplate: `${meta.label}: %{customdata} ${meta.unit}<br>%{x|%Y-%m-%d %H:%M}<extra></extra>`,
      customdata: values,
      visible: activeChannels.has(channel) ? true : 'legendonly',
      meta: channel,
    };
  }).filter(Boolean);

  const layout = themeLayout({
    height: 240,
    margin: { t: 64, b: 55, l: 55, r: 55 },
    title: { text: "Selected channels over time — drag to select a window", font: { ...titleFont, size: 12 }, x: 0.5, xanchor: "center" },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.18, font: { size: 10 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: {
      type: "date",
      domain: [0, 0.94],
      // Pin to the actual data range so overlay shapes at other timestamps
      // cannot cause Plotly's autorange to expand the axis beyond the data.
      range: ts.length >= 2 ? [ts[0], ts[ts.length - 1]] : undefined,
      autorange: ts.length < 2,
    },
    yaxis: { title: "Relative movement (scaled 0–1)", side: "left", showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } },
    dragmode: "zoom",
  });

  if (overlayShapes && overlayShapes.length) {
    layout.shapes = overlayShapes;
  }
  const allTraces = hoverTrace ? [...traces, hoverTrace] : traces;
  if (hoverTrace) {
    _corrHoverTraceIdx = allTraces.length - 1;
  }

  Plotly.newPlot("corrBrushPlot", allTraces, layout, { responsive: true, displayModeBar: false });

  const brushEl = document.getElementById("corrBrushPlot");
  brushEl.on("plotly_relayout", (evt) => {
    const x0 = evt["xaxis.range[0]"];
    const x1 = evt["xaxis.range[1]"];
    if (!x0 || !x1) return; // autorange reset, ignore

    const t0 = new Date(x0).getTime();
    const t1 = new Date(x1).getTime();
    const subset = _fullData.filter(d => {
      const t = new Date(d.timestamp).getTime();
      return t >= t0 && t <= t1;
    });

    if (subset.length < 2) return;

    _isSubset = true;
    _renderScatterCharts(subset);
    _renderInferencePanel(subset, _fullData);

    const fmt = (d) => new Date(d).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const dateStr = new Date(x0).toLocaleDateString([], { day: "numeric", month: "short" });
    document.getElementById("corrRangeLabel").textContent =
      `Showing: ${dateStr} ${fmt(x0)} – ${fmt(x1)} (${subset.length} readings)`;

    const zStart = new Date(x0).toISOString();
    const zEnd   = new Date(x1).toISOString();
    _corrSelectedRange = { start: zStart, end: zEnd };
    _loadAnalysisPanel(zStart, zEnd);
  });
}

// ── Scatter plots (reused from before, but take filtered data) ───────────────

function _renderScatterCharts(data) {
  const eco2 = data.map(d => d.eco2);
  const tvoc = data.map(d => d.tvoc);
  const temp = data.map(d => d.temperature);
  const hum  = data.map(d => d.humidity);
  const vpd  = data.map(d => d.vpd_kpa);
  const timeColour = data.map((_, i) => i / (data.length - 1 || 1));
  const titleFont  = { color: isLight ? "#111" : "#b0bec5" };

  // Regression
  const pairs = data.filter(d => d.eco2 != null && d.tvoc != null);
  const regX = pairs.map(d => d.eco2);
  const regY = pairs.map(d => d.tvoc);
  const reg  = pairs.length >= 2 ? linearRegression(regX, regY) : null;

  const traces = [{
    x: eco2, y: tvoc, mode: "markers", name: "Readings",
    marker: {
      color: timeColour, colorscale: "Viridis", size: 6,
      colorbar: { title: "Time →", thickness: 12, len: 0.6 },
    },
    hovertemplate: "eCO₂: %{x} ppm<br>TVOC: %{y} ppb<extra></extra>",
  }];

  if (reg) {
    const xMin = Math.min(...regX), xMax = Math.max(...regX);
    traces.push({
      x: [xMin, xMax],
      y: [reg.slope * xMin + reg.intercept, reg.slope * xMax + reg.intercept],
      mode: "lines", name: "Best fit",
      line: { color: "#f59e0b", width: 2, dash: "dash" },
      hoverinfo: "skip",
    });
  }

  const r2Label = reg ? `R² = ${reg.r2.toFixed(3)}` : "";
  const sourceHint = reg
    ? (reg.r2 > 0.7 ? "Strong correlation — likely common source"
      : reg.r2 > 0.4 ? "Moderate correlation — partially shared sources"
      : "Weak correlation — likely multiple independent sources")
    : "";

  Plotly.newPlot("tvocEco2ScatterPlot", traces, themeLayout({
    title: { text: "TVOC vs eCO₂ (colour = time)", font: titleFont },
    xaxis: { title: "eCO₂ (ppm)" },
    yaxis: { title: "TVOC (ppb)" },
    annotations: reg ? [{
      xref: "paper", yref: "paper", x: 0.02, y: 0.98,
      text: `<b>${r2Label}</b><br>${sourceHint}`,
      showarrow: false, align: "left",
      font: { size: 12, color: isLight ? "#333" : "#ddd" },
      bgcolor: isLight ? "rgba(255,255,255,0.8)" : "rgba(30,30,30,0.8)",
      borderpad: 6,
    }] : [],
  }), { responsive: true });

  // Temp vs Humidity (VPD)
  Plotly.newPlot("tempHumScatterPlot", [{
    x: hum, y: temp, mode: "markers",
    marker: { color: vpd.map(vpdColour), size: 6 },
    hovertemplate: "Humidity: %{x}%<br>Temp: %{y} °C<extra></extra>",
  }], themeLayout({
    title: { text: "Temperature vs Humidity (colour = VPD zone)", font: titleFont },
    xaxis: { title: "Humidity (%)" },
    yaxis: { title: "Temperature (°C)" },
  }), { responsive: true });

  // PM2.5 vs TVOC
  const pm25El = document.getElementById("pm25TvocScatterPlot");
  if (pm25El) {
    const pmPairs = data.filter(d => d.pm2_5 != null && d.tvoc != null);
    if (pmPairs.length >= 2) {
      const pmX = pmPairs.map(d => d.pm2_5);
      const pmY = pmPairs.map(d => d.tvoc);
      const pmTime = pmPairs.map((_, i) => i / (pmPairs.length - 1 || 1));
      const pmReg = linearRegression(pmX, pmY);
      const pmTraces = [{
        x: pmX, y: pmY, mode: "markers", name: "Readings",
        marker: {
          color: pmTime, colorscale: "Plasma", size: 6,
          colorbar: { title: "Time →", thickness: 12, len: 0.6 },
        },
        hovertemplate: "PM2.5: %{x} µg/m³<br>TVOC: %{y} ppb<extra></extra>",
      }];
      if (pmReg) {
        const xMin = Math.min(...pmX), xMax = Math.max(...pmX);
        pmTraces.push({
          x: [xMin, xMax],
          y: [pmReg.slope * xMin + pmReg.intercept, pmReg.slope * xMax + pmReg.intercept],
          mode: "lines", name: "Best fit",
          line: { color: "#f59e0b", width: 2, dash: "dash" },
          hoverinfo: "skip",
        });
      }
      const pmR2 = pmReg ? pmReg.r2.toFixed(3) : "";
      const pmHint = pmReg
        ? (pmReg.r2 > 0.6
            ? "Strong link — particles and VOCs likely share a source (cooking, combustion)"
            : pmReg.r2 > 0.3
              ? "Moderate link — partial shared source (e.g. cooking raises both)"
              : "Weak link — particles and VOCs have independent sources")
        : "";
      Plotly.newPlot("pm25TvocScatterPlot", pmTraces, themeLayout({
        title: { text: "PM2.5 vs TVOC (colour = time)", font: titleFont },
        xaxis: { title: "PM2.5 (µg/m³)" },
        yaxis: { title: "TVOC (ppb)" },
        annotations: pmReg ? [{
          xref: "paper", yref: "paper", x: 0.02, y: 0.98,
          text: `<b>R² = ${pmR2}</b><br>${pmHint}`,
          showarrow: false, align: "left",
          font: { size: 12, color: isLight ? "#333" : "#ddd" },
          bgcolor: isLight ? "rgba(255,255,255,0.8)" : "rgba(30,30,30,0.8)",
          borderpad: 6,
        }] : [],
      }), { responsive: true });
    } else {
      pm25El.innerHTML = '<p style="color:#888;padding:1em;text-align:center">No PM2.5 data available for this range.</p>';
    }
  }

  // PM2.5 vs eCO₂
  const pm25Eco2El = document.getElementById("pm25Eco2ScatterPlot");
  if (pm25Eco2El) {
    const eco2Pairs = data.filter(d => d.pm2_5 != null && d.eco2 != null);
    if (eco2Pairs.length >= 2) {
      const pX = eco2Pairs.map(d => d.pm2_5);
      const pY = eco2Pairs.map(d => d.eco2);
      const pTime = eco2Pairs.map((_, i) => i / (eco2Pairs.length - 1 || 1));
      const pReg = linearRegression(pX, pY);
      const pTraces = [{
        x: pX, y: pY, mode: "markers", name: "Readings",
        marker: {
          color: pTime, colorscale: "Cividis", size: 6,
          colorbar: { title: "Time →", thickness: 12, len: 0.6 },
        },
        hovertemplate: "PM2.5: %{x} µg/m³<br>eCO₂: %{y} ppm<extra></extra>",
      }];
      if (pReg) {
        const xMin = Math.min(...pX), xMax = Math.max(...pX);
        pTraces.push({
          x: [xMin, xMax],
          y: [pReg.slope * xMin + pReg.intercept, pReg.slope * xMax + pReg.intercept],
          mode: "lines", name: "Best fit",
          line: { color: "#f59e0b", width: 2, dash: "dash" },
          hoverinfo: "skip",
        });
      }
      const pHint = pReg
        ? (pReg.r2 > 0.6
            ? "Strong link — particles and CO₂ rise together: combustion (cooking, candles) or dense occupancy with particles"
            : pReg.r2 > 0.3
              ? "Moderate link — some shared source; cooking or gas appliances raise both"
              : "Weak link — particles and CO₂ have independent origins (e.g. outdoor PM + indoor occupants)")
        : "";
      Plotly.newPlot("pm25Eco2ScatterPlot", pTraces, themeLayout({
        title: { text: "PM2.5 vs eCO₂ (colour = time)", font: titleFont },
        xaxis: { title: "PM2.5 (µg/m³)" },
        yaxis: { title: "eCO₂ (ppm)" },
        annotations: pReg ? [{
          xref: "paper", yref: "paper", x: 0.02, y: 0.98,
          text: `<b>R² = ${pReg.r2.toFixed(3)}</b><br>${pHint}`,
          showarrow: false, align: "left",
          font: { size: 12, color: isLight ? "#333" : "#ddd" },
          bgcolor: isLight ? "rgba(255,255,255,0.8)" : "rgba(30,30,30,0.8)",
          borderpad: 6,
        }] : [],
      }), { responsive: true });
    } else {
      pm25Eco2El.innerHTML = '<p style="color:#888;padding:1em;text-align:center">No PM2.5 data available for this range.</p>';
    }
  }
}

// ── Inference engine ─────────────────────────────────────────────────────────

function _renderInferencePanel(subset, fullData) {
  const panel = document.getElementById("corrInferenceGrid");
  if (!panel) return;

  if (subset.length < 2) {
    panel.innerHTML = '<p class="corr-inference-placeholder">Select a time range above to see analysis.</p>';
    return;
  }

  const selTvoc  = subset.map(d => d.tvoc).filter(v => v != null);
  const selEco2  = subset.map(d => d.eco2).filter(v => v != null);
  const selTemp  = subset.map(d => d.temperature).filter(v => v != null);
  const selHum   = subset.map(d => d.humidity).filter(v => v != null);
  const selPm25  = subset.map(d => d.pm2_5).filter(v => v != null);
  const selPm10  = subset.map(d => d.pm10).filter(v => v != null);

  const fullTvoc = fullData.map(d => d.tvoc).filter(v => v != null);
  const fullEco2 = fullData.map(d => d.eco2).filter(v => v != null);
  const fullTemp = fullData.map(d => d.temperature).filter(v => v != null);
  const fullHum  = fullData.map(d => d.humidity).filter(v => v != null);

  // Averages
  const avgTvocSel  = avg(selTvoc),  avgTvocFull  = avg(fullTvoc);
  const avgEco2Sel  = avg(selEco2),  avgEco2Full  = avg(fullEco2);
  const avgTempSel  = avg(selTemp),  avgTempFull  = avg(fullTemp);
  const avgHumSel   = avg(selHum),   avgHumFull   = avg(fullHum);
  const avgPm25Sel  = avg(selPm25);
  const avgPm10Sel  = avg(selPm10);

  // Peaks in selection
  const peakTvoc = selTvoc.length ? Math.max(...selTvoc) : null;
  const peakEco2 = selEco2.length ? Math.max(...selEco2) : null;
  const peakPm25 = selPm25.length ? Math.max(...selPm25) : null;

  // Trends in selection
  const tvocTrend = trendSlope(selTvoc);
  const eco2Trend = trendSlope(selEco2);
  const pm25Trend = trendSlope(selPm25);

  // Correlations
  const tvocEco2Pairs = subset.filter(d => d.eco2 != null && d.tvoc != null);
  const reg = tvocEco2Pairs.length >= 2
    ? linearRegression(tvocEco2Pairs.map(d => d.eco2), tvocEco2Pairs.map(d => d.tvoc))
    : null;

  const pmTvocPairs = subset.filter(d => d.pm2_5 != null && d.tvoc != null);
  const regPmTvoc = pmTvocPairs.length >= 2
    ? linearRegression(pmTvocPairs.map(d => d.pm2_5), pmTvocPairs.map(d => d.tvoc))
    : null;

  const pmEco2Pairs = subset.filter(d => d.pm2_5 != null && d.eco2 != null);
  const regPmEco2 = pmEco2Pairs.length >= 2
    ? linearRegression(pmEco2Pairs.map(d => d.pm2_5), pmEco2Pairs.map(d => d.eco2))
    : null;

  // PM2.5/PM10 ratio: >0.7 = fine combustion particles, <0.5 = coarse dust
  const pmRatio = avgPm25Sel != null && avgPm10Sel != null && avgPm10Sel > 0
    ? avgPm25Sel / avgPm10Sel : null;

  const hasPm = selPm25.length >= 2;

  // ── Build inference cards ──────────────────────────────────────────────
  const cards = [];

  // 1. Pollutant fingerprint — what pattern are we looking at?
  {
    const pmHigh   = avgPm25Sel != null && avgPm25Sel > 12;
    const tvocHigh = avgTvocSel != null && avgTvocSel > 250;
    const eco2High = avgEco2Sel != null && avgEco2Sel > 800;
    const levels = [];
    if (hasPm)           levels.push(`PM2.5: <strong>${avgPm25Sel != null ? Math.round(avgPm25Sel) : "–"} µg/m³</strong> ${pmHigh ? "⚠️" : "✅"}`);
    if (selTvoc.length)  levels.push(`TVOC: <strong>${avgTvocSel != null ? Math.round(avgTvocSel) : "–"} ppb</strong> ${tvocHigh ? "⚠️" : "✅"}`);
    if (selEco2.length)  levels.push(`eCO₂: <strong>${avgEco2Sel != null ? Math.round(avgEco2Sel) : "–"} ppm</strong> ${eco2High ? "⚠️" : "✅"}`);
    const corrParts = [];
    if (reg)        corrParts.push(`TVOC↔CO₂ R² = ${reg.r2.toFixed(2)}`);
    if (regPmTvoc)  corrParts.push(`PM2.5↔TVOC R² = ${regPmTvoc.r2.toFixed(2)}`);
    if (regPmEco2)  corrParts.push(`PM2.5↔CO₂ R² = ${regPmEco2.r2.toFixed(2)}`);
    const anyHigh = pmHigh || tvocHigh || eco2High;
    cards.push({
      icon: anyHigh ? "📊" : "📊",
      cls: anyHigh ? "insight-moderate" : "insight-good",
      html: `<strong>Pollutant levels in this window</strong><br>${levels.join(" · ")}` +
        (corrParts.length ? `<br><span style="font-size:0.85em;color:#888">${corrParts.join(" · ")}</span>` : "") +
        (pmRatio != null ? `<br><span style="font-size:0.85em;color:#888">PM2.5/PM10 ratio: ${pmRatio.toFixed(2)} ${pmRatio > 0.7 ? "(fine particles — combustion signature)" : pmRatio < 0.5 ? "(coarse particles — dust/pollen)" : "(mixed)"}</span>` : ""),
    });
  }

  // 2. Source attribution (uses all three pollutants)
  {
    const pmHigh       = avgPm25Sel != null && avgPm25Sel > 12;
    const tvocHigh     = avgTvocSel != null && avgTvocSel > 250;
    const eco2High     = avgEco2Sel != null && avgEco2Sel > 800;
    const pmTvocR2     = regPmTvoc ? regPmTvoc.r2 : 0;
    const pmEco2R2     = regPmEco2 ? regPmEco2.r2 : 0;
    const tvocEco2R2   = reg       ? reg.r2        : 0;

    let srcIcon, srcCls, srcTitle, srcDesc, srcTip;

    if (hasPm && pmHigh && tvocHigh && pmTvocR2 > 0.4) {
      // PM and TVOC both elevated and correlated = combustion
      srcIcon = "🔥"; srcCls = "insight-warn";
      srcTitle = "Combustion event";
      srcDesc = `PM2.5 (${avgPm25Sel != null ? Math.round(avgPm25Sel) : "–"} µg/m³) and TVOC (${avgTvocSel != null ? Math.round(avgTvocSel) : "–"} ppb) are both elevated and correlated (PM↔TVOC R² = ${pmTvocR2.toFixed(2)}). This is the hallmark of a combustion source.`;
      srcTip = pmRatio != null && pmRatio > 0.7
        ? "High PM2.5/PM10 ratio confirms fine combustion particles. Likely: cooking (frying/grilling), candles, incense, or gas appliances."
        : "Common causes: cooking, candles, incense, or gas appliances. The fan should activate automatically if PM25Rule is enabled.";
    } else if (hasPm && pmHigh && !tvocHigh && !eco2High) {
      // PM high but no gas/VOC source = outdoor or dust
      if (pmRatio != null && pmRatio < 0.5) {
        srcIcon = "💨"; srcCls = "insight-moderate";
        srcTitle = "Dust or coarse particles";
        srcDesc = `PM2.5 is elevated but the PM2.5/PM10 ratio is low (${pmRatio.toFixed(2)}), meaning most of the mass is in larger coarse particles. This is not combustion — it's physical disturbance of dust, soil, or pollen.`;
        srcTip = "Common causes: vacuuming, bedding disturbance, construction nearby, or opening windows on a pollen/dust day.";
      } else {
        srcIcon = "🌫️"; srcCls = "insight-warn";
        srcTitle = "Outdoor fine particle infiltration";
        srcDesc = `PM2.5 is elevated (${avgPm25Sel != null ? Math.round(avgPm25Sel) : "–"} µg/m³) but TVOC and eCO₂ are both normal. This pattern means the particles are coming from outside, not from an indoor activity.`;
        srcTip = "Check your local AQI. If outdoor air quality is poor, keep windows closed and avoid running ventilation fans that draw from outside.";
      }
    } else if (!hasPm || (!pmHigh)) {
      if (tvocHigh && !eco2High) {
        srcIcon = "🧴"; srcCls = "insight-warn";
        srcTitle = "Chemical off-gassing (no combustion)";
        srcDesc = `TVOC is elevated (${avgTvocSel != null ? Math.round(avgTvocSel) : "–"} ppb) but PM2.5 and eCO₂ are normal. This is typical of volatile organic sources that don't produce particles or eCO₂: cleaning products, air fresheners, paint, adhesives, new furniture, or cosmetics.`;
        srcTip = "These sources can persist for hours or days. TVOC off-gassing from new furniture peaks in the first few weeks. Ventilation helps but the source needs to be removed or contained.";
      } else if (tvocHigh && eco2High && tvocEco2R2 > 0.4) {
        srcIcon = "👥"; srcCls = "insight-neutral";
        srcTitle = "Occupancy / breathing";
        srcDesc = `eCO₂ and TVOC are both elevated and correlated (R² = ${tvocEco2R2.toFixed(2)}), but PM2.5 is normal. This is the signature of human presence without combustion — eCO₂ and body VOCs from breathing, metabolism, and skin.`;
        srcTip = "Open a window or increase the ventilation rate. eCO₂ and TVOC from occupancy drop quickly with fresh air flow.";
      } else if (eco2High && !tvocHigh) {
        srcIcon = "🫁"; srcCls = "insight-neutral";
        srcTitle = "eCO₂ build-up (minimal VOCs)";
        srcDesc = `eCO₂ is elevated (${avgEco2Sel != null ? Math.round(avgEco2Sel) : "–"} ppm) but TVOC and PM2.5 are normal. Pure eCO₂ build-up with no matching VOCs or particles typically means occupants in a sealed room with minimal activity.`;
        srcTip = "Ventilate the room. If this repeats at the same time daily, the room needs a higher base ventilation rate.";
      } else {
        srcIcon = "✅"; srcCls = "insight-good";
        srcTitle = "All clear";
        srcDesc = `All measured pollutants — PM2.5, TVOC, and eCO₂ — are within normal ranges in this window. No significant source is active.`;
        srcTip = "";
      }
    } else {
      srcIcon = "🔀"; srcCls = "insight-moderate";
      srcTitle = "Mixed or unresolved sources";
      const parts = [];
      if (pmHigh)   parts.push(`PM2.5 ${avgPm25Sel != null ? Math.round(avgPm25Sel) : "–"} µg/m³`);
      if (tvocHigh) parts.push(`TVOC ${avgTvocSel != null ? Math.round(avgTvocSel) : "–"} ppb`);
      if (eco2High) parts.push(`eCO₂ ${avgEco2Sel != null ? Math.round(avgEco2Sel) : "–"} ppm`);
      srcDesc = `Multiple pollutants are elevated (${parts.join(", ")}) but the correlation pattern doesn't point clearly to a single source. This could be a compound event — e.g. cooking while a window is open letting outdoor PM in.`;
      srcTip = "Add annotations when you notice activities, then revisit this window to build a pattern library.";
    }

    let srcHtml = `<strong>Source attribution: ${srcTitle}</strong><br>${srcDesc}`;
    if (srcTip) srcHtml += `<br><em style="color:#aaa;font-size:0.9em">${srcTip}</em>`;
    cards.push({ icon: srcIcon, cls: srcCls, html: srcHtml });
  }

  // 3. Trend direction (all three pollutants)
  {
    const trendLabel = (slope) => slope > 0.5 ? "rising" : slope < -0.5 ? "falling" : "stable";
    const tvocDir = trendLabel(tvocTrend);
    const eco2Dir = trendLabel(eco2Trend);
    const pm25Dir = hasPm ? trendLabel(pm25Trend) : null;

    let trendIcon, trendClass, trendText;
    const allFalling = tvocDir === "falling" && eco2Dir === "falling" && (!hasPm || pm25Dir === "falling");
    const allRising  = tvocDir === "rising"  && eco2Dir === "rising";

    if (allFalling) {
      trendIcon = "📉"; trendClass = "insight-good";
      trendText = `<strong>All pollutants falling</strong><br>Air quality is improving — ventilation is working or the source has been removed.${hasPm && pm25Dir === "falling" ? " PM2.5 is also clearing." : ""}`;
    } else if (allRising && hasPm && pm25Dir === "rising") {
      trendIcon = "📈"; trendClass = "insight-warn";
      trendText = `<strong>All three pollutants rising</strong><br>PM2.5, TVOC, and eCO₂ are all increasing together. This is a strong combustion or occupancy+cooking signal. Ventilate now.`;
    } else if (allRising) {
      trendIcon = "📈"; trendClass = "insight-warn";
      trendText = `<strong>TVOC and eCO₂ rising</strong><br>Air quality is getting worse. ${hasPm && pm25Dir === "rising" ? "PM2.5 is also rising. " : ""}Common during cooking, gatherings, or when windows are closed.`;
    } else if (tvocDir === "rising" && eco2Dir !== "rising") {
      trendIcon = "🧪"; trendClass = "insight-warn";
      trendText = `<strong>TVOC rising, eCO₂ ${eco2Dir}</strong><br>A VOC source is active without extra eCO₂. Likely: cleaning products, paint, adhesives, cosmetics, or new materials.${hasPm && pm25Dir === "rising" ? " PM2.5 also rising — possibly a combustion source." : ""}`;
    } else if (eco2Dir === "rising" && tvocDir !== "rising") {
      trendIcon = "🫁"; trendClass = "insight-warn";
      trendText = `<strong>eCO₂ rising, TVOC ${tvocDir}</strong><br>eCO₂ building up from occupants. The air feels stuffy before TVOC catches up. ${hasPm && pm25Dir === "rising" ? "PM2.5 also rising — check for a concurrent particle source." : ""}`;
    } else {
      trendIcon = "➡️"; trendClass = "insight-neutral";
      trendText = `<strong>Pollutant levels are stable</strong><br>No significant change in this window. The environment is in a steady state.`;
    }
    cards.push({ icon: trendIcon, cls: trendClass, html: trendText });
  }

  // 4. Comparison to full range (only if subset)
  if (_isSubset && avgTvocFull != null && avgTvocSel != null) {
    const tvocPct = ((avgTvocSel - avgTvocFull) / avgTvocFull * 100).toFixed(0);
    const eco2Pct = avgEco2Full ? ((avgEco2Sel - avgEco2Full) / avgEco2Full * 100).toFixed(0) : null;
    const tvocAbove = avgTvocSel > avgTvocFull;
    const compIcon = tvocAbove ? "⚠️" : "✅";
    const compClass = tvocAbove ? "insight-warn" : "insight-good";

    let compText = `<strong>Compared to the full time range</strong><br>`;
    compText += `TVOC: <strong>${Math.abs(tvocPct)}% ${tvocAbove ? "higher" : "lower"}</strong> than average (${Math.round(avgTvocSel)} vs ${Math.round(avgTvocFull)} ppb)`;
    if (eco2Pct != null) {
      const eco2Above = avgEco2Sel > avgEco2Full;
      compText += `<br>eCO₂: <strong>${Math.abs(eco2Pct)}% ${eco2Above ? "higher" : "lower"}</strong> than average (${Math.round(avgEco2Sel)} vs ${Math.round(avgEco2Full)} ppm)`;
    }
    if (hasPm && avgPm25Sel != null) {
      const fullPm25 = avg(fullData.map(d => d.pm2_5).filter(v => v != null));
      if (fullPm25 != null) {
        const pmPct = ((avgPm25Sel - fullPm25) / fullPm25 * 100).toFixed(0);
        const pmAbove = avgPm25Sel > fullPm25;
        compText += `<br>PM2.5: <strong>${Math.abs(pmPct)}% ${pmAbove ? "higher" : "lower"}</strong> than average (${Math.round(avgPm25Sel)} vs ${Math.round(fullPm25)} µg/m³)`;
      }
    }
    if (peakTvoc != null && peakTvoc > 500)  compText += `<br><span class="insight-peak">Peak TVOC: <strong>${Math.round(peakTvoc)} ppb</strong> — above WHO moderate (250 ppb)</span>`;
    if (peakEco2 != null && peakEco2 > 1000) compText += `<br><span class="insight-peak">Peak eCO₂: <strong>${Math.round(peakEco2)} ppm</strong> — cognitive impairment level</span>`;
    if (peakPm25 != null && peakPm25 > 35)   compText += `<br><span class="insight-peak">Peak PM2.5: <strong>${Math.round(peakPm25)} µg/m³</strong> — WHO unhealthy threshold</span>`;

    cards.push({ icon: compIcon, cls: compClass, html: compText });
  }

  // 5. Environmental context
  if (avgTempSel != null && avgHumSel != null) {
    let envIcon, envClass, envText;
    const svp = 0.6108 * Math.exp(17.27 * avgTempSel / (avgTempSel + 237.3));
    const vpd = svp * (1 - avgHumSel / 100);
    envText = `<strong>Environment in this window</strong><br>Temperature: ${avgTempSel.toFixed(1)} °C · Humidity: ${avgHumSel.toFixed(0)}% · VPD: ${vpd.toFixed(2)} kPa<br>`;
    if (avgHumSel > 70) {
      envIcon = "💧"; envClass = "insight-warn";
      envText += `High humidity can trap volatile compounds, amplify odours, and accelerate mould growth. Consider dehumidification.`;
    } else if (avgTempSel > 28) {
      envIcon = "🌡️"; envClass = "insight-warn";
      envText += `High temperature accelerates off-gassing from materials and increases VOC emissions. Ventilate if possible.`;
    } else if (vpd > 1.6) {
      envIcon = "🏜️"; envClass = "insight-warn";
      envText += `Very dry air (high VPD). This can irritate airways and amplify the effects of airborne particles. Consider a humidifier.`;
    } else {
      envIcon = "🌿"; envClass = "insight-good";
      envText += `Conditions are within normal ranges. Temperature and humidity are not amplifying pollution effects.`;
    }
    cards.push({ icon: envIcon, cls: envClass, html: envText });
  }

  // 6. Actionable summary
  {
    const pmProblem  = peakPm25 != null && peakPm25 > 35;
    const pmModerate = peakPm25 != null && peakPm25 > 12;
    let actionIcon, actionClass, actionText;
    if ((peakTvoc != null && peakTvoc > 500) || (peakEco2 != null && peakEco2 > 1500) || pmProblem) {
      actionIcon = "💡"; actionClass = "insight-action";
      actionText = `<strong>What to do</strong><br>Pollution in this window is elevated across multiple indicators. Ventilate now${pmProblem ? ", especially if cooking — run the extractor fan" : ""}. If this pattern repeats, check the <em>Patterns</em> tab to confirm and schedule your fan to pre-emptively run.`;
    } else if ((peakTvoc != null && peakTvoc > 250) || (peakEco2 != null && peakEco2 > 1000) || pmModerate) {
      actionIcon = "💡"; actionClass = "insight-action";
      actionText = `<strong>What to do</strong><br>Moderate pollution detected. Ventilation would help but it's not urgent. If this coincides with a recurring activity, running the fan during it is a good preventive habit.`;
    } else {
      actionIcon = "💡"; actionClass = "insight-action-ok";
      actionText = `<strong>Looking good</strong><br>All pollutant levels in this window are within safe ranges. No action needed.`;
    }
    cards.push({ icon: actionIcon, cls: actionClass, html: actionText });
  }

  // Render
  panel.innerHTML = cards.map(c => `
    <div class="corr-insight-card ${c.cls}">
      <div class="corr-insight-icon">${c.icon}</div>
      <div class="corr-insight-body">${c.html}</div>
    </div>
  `).join("");
}

// ── ML-aware analysis panel ──────────────────────────────────────────────────

let _corrBaselines = {};

async function _loadAnalysisPanel(start, end) {
  const panel   = document.getElementById('corrAnalysisPanel');
  const loading = document.getElementById('corrAnalysisLoading');
  const content = document.getElementById('corrAnalysisContent');
  if (!panel) return;
  panel.style.display = 'block';
  loading.style.display = 'block';
  content.style.display = 'none';
  try {
    const [ctxResp, blResp, sensorResp, rangeResp] = await Promise.all([
      fetch(`/api/history/ml-context?start=${start}&end=${end}`),
      fetch('/api/history/baselines'),
      fetch(`/api/history/sensor?start=${start}&end=${end}`),
      fetch(`/api/history/range-analysis?start=${start}&end=${end}`),
    ]);
    const ctx = await ctxResp.json();
    _corrBaselines = await blResp.json();
    const sensorData = await sensorResp.json();
    const rangeAnalysis = await rangeResp.json();
    loading.style.display = 'none';
    content.style.display = 'block';

    const evList = document.getElementById('corrEventsList');
    if (ctx.inferences && ctx.inferences.length > 0) {
      evList.innerHTML = ctx.inferences.map(function (inf) {
        const chipFn = typeof renderDetectionChip === 'function';
        const chip = chipFn ? renderDetectionChip(inf.detection_method || 'rule') : `<span>[${inf.detection_method||'rule'}]</span>`;
        return `<div class="ev-row">${chip} ${inf.title}</div>`;
      }).join('');
    } else {
      evList.innerHTML = '<span class="muted">No detections in this window.</span>';
    }

    document.getElementById('corrComovement').textContent = ctx.comovement_summary || 'No strong correlations detected.';

    const activeChannels = Array.from(document.querySelectorAll('.channel-chip[data-group].active')).map(c => c.dataset.channel);
    const peakRows = activeChannels.map(function (ch) {
      const vals = (sensorData.channels && sensorData.channels[ch] || []).filter(v => v != null);
      if (!vals.length) return null;
      const peak = Math.max(...vals);
      const baseline = _corrBaselines[ch];
      const label = CORR_LABELS[ch] || ch;
      const ratioStr = baseline ? `${(peak/baseline).toFixed(1)}\u00d7 baseline (${baseline.toFixed(1)})` : 'Baseline not yet available.';
      return `<div class="peak-row"><strong>${label}:</strong> peak ${peak.toFixed(1)} \u2014 ${ratioStr}</div>`;
    }).filter(Boolean);
    document.getElementById('corrPeakBaseline').innerHTML = peakRows.length ? peakRows.join('') : '<span class="muted">No data.</span>';

    const attrSection = document.getElementById('corrAttributionSummarySection');
    const attrEl = document.getElementById('corrAttributionSummary');
    if (ctx.inferences && ctx.inferences.length >= 2 && ctx.dominant_source) {
      attrSection.style.display = 'block';
      const dominated = ctx.inferences.filter(i => i.attribution_source === ctx.dominant_source).length;
      attrEl.textContent = `${dominated} of ${ctx.inferences.length} events attributed to ${ctx.dominant_source}.`;
    } else {
      attrSection.style.display = 'none';
    }

    // Show range inference suggestion
    const suggestionSection = document.getElementById('corrRangeInferenceSuggestionSection');
    const suggestionEl = document.getElementById('corrRangeInferenceSuggestion');
    if (rangeAnalysis.best_candidate) {
      const bc = rangeAnalysis.best_candidate;
      suggestionEl.innerHTML = `
        <div class="inference-card ${bc.severity === 'critical' ? 'critical' : bc.severity === 'warning' ? 'warning' : 'info'}">
          <div class="inference-icon">${bc.severity === 'critical' ? '🚨' : bc.severity === 'warning' ? '⚠️' : 'ℹ️'}</div>
          <div class="inference-body">
            <strong>${bc.title}</strong><br>
            ${bc.description}<br>
            <em>Confidence: ${(bc.confidence * 100).toFixed(0)}%</em>
          </div>
        </div>
      `;
      suggestionSection.style.display = 'block';
    } else {
      suggestionSection.style.display = 'none';
    }

    // Always show range tagging section when a range is selected
    const tagSection = document.getElementById('corrRangeTagSection');
    if (tagSection) {
      tagSection.style.display = 'block';
    }
  } catch (e) {
    loading.textContent = 'Could not load analysis.';
  }
}

// ── Anomaly event overlay ────────────────────────────────────────────────────

let _corrOverlayShapes  = [];
let _corrOverlayVisible = true;
let _corrHoverTraceIdx  = null;

function _buildAnomalyOverlay(inferences) {
  const shapes = [], hoverX = [], hoverY = [], hoverText = [];
  inferences.forEach(function (inf) {
    const ts = inf.created_at;
    let colour = '#6b7280';
    if (inf.severity === 'critical') colour = '#ef4444';
    else if (inf.severity === 'warning') colour = '#f59e0b';
    else if (inf.detection_method === 'ml') colour = '#3b82f6';
    shapes.push({ type:'line', x0:ts,x1:ts,y0:0,y1:1, xref:'x',yref:'paper', line:{color:colour,width:1,dash:'dash'} });
    hoverX.push(ts); hoverY.push(0.5);
    const src = inf.attribution_source ? ` | ${inf.attribution_source} (${Math.round((inf.attribution_confidence||0)*100)}%)` : '';
    hoverText.push(`${inf.title}<br>${inf.detection_method || 'rule'}${src}`);
  });
  const hoverTrace = { x:hoverX, y:hoverY, mode:'markers', marker:{opacity:0,size:12}, hoverinfo:'text', hovertext:hoverText, showlegend:false, name:'detections' };
  return { shapes, hoverTrace };
}

function corrToggleOverlay(visible) {
  _corrOverlayVisible = visible;
  const chartDiv = document.getElementById('corrBrushPlot');
  if (!chartDiv) return;
  Plotly.relayout(chartDiv, { shapes: visible ? _corrOverlayShapes : [] });
  if (_corrHoverTraceIdx !== null) {
    Plotly.restyle(chartDiv, { visible: [visible] }, [_corrHoverTraceIdx]);
  }
}

// ── Channel toggle chips for Correlations tab ────────────────────────────────

const CORR_CHANNELS = ['tvoc_ppb','eco2_ppm','temperature_c','humidity_pct','pm1_ug_m3','pm25_ug_m3','pm10_ug_m3','co_ppb','no2_ppb','nh3_ppb'];
const CORR_COLOURS  = Object.fromEntries(Object.entries(CORR_CHANNEL_META).map(([k, v]) => [k, v.color]));
const CORR_LABELS   = Object.fromEntries(Object.entries(CORR_CHANNEL_META).map(([k, v]) => [k, v.label]));

function corrToggleChip(btn) {
  btn.classList.toggle('active');
  _updateCorrVisibility();
}
function corrToggleGroup(group) {
  const chips = document.querySelectorAll(`.channel-chip[data-group="${group}"]`);
  const allActive = Array.from(chips).every(c => c.classList.contains('active'));
  chips.forEach(c => allActive ? c.classList.remove('active') : c.classList.add('active'));
  _updateCorrVisibility();
}
function corrToggleAll(state) {
  document.querySelectorAll('.channel-chip[data-group]').forEach(c => state ? c.classList.add('active') : c.classList.remove('active'));
  _updateCorrVisibility();
}
function _updateCorrVisibility() {
  const active = _getActiveCorrChannels();
  const emptyMsg = document.getElementById('corrEmptyMsg');
  if (active.size === 0) {
    if (emptyMsg) emptyMsg.style.display = 'block';
  } else if (emptyMsg) {
    emptyMsg.style.display = 'none';
  }

  const chartDiv = document.getElementById('corrBrushPlot');
  if (chartDiv && chartDiv.data && chartDiv.data.length > 0) {
    chartDiv.data.forEach(function (trace, idx) {
      if (!trace.meta || trace.name === 'detections') return;
      const channel = trace.meta;
      const shouldShow = active.has(channel);
      Plotly.restyle(chartDiv, { visible: [shouldShow ? true : 'legendonly'] }, [idx]);
    });
  }
}

// Expose toggle functions to window so inline onclick handlers in the HTML can call them.
// (This file is loaded as an ES module and module-scope functions are not globally accessible.)
window.corrToggleChip  = corrToggleChip;
window.corrToggleGroup = corrToggleGroup;
window.corrToggleAll   = corrToggleAll;
window.corrToggleOverlay = corrToggleOverlay;

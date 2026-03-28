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

// ── State ────────────────────────────────────────────────────────────────────

let _fullData = [];
let _isSubset = false;

// ── Public entry point ───────────────────────────────────────────────────────

export function renderCorrelationCharts(data) {
  if (!data || data.length === 0) return;
  _fullData = data;
  _isSubset = false;

  _renderBrushChart(data);
  _renderScatterCharts(data);
  _renderInferencePanel(data, data);

  // Reset button
  const resetBtn = document.getElementById("corrResetBtn");
  if (resetBtn) {
    resetBtn.onclick = () => {
      _isSubset = false;
      // Re-render brush chart to clear zoom
      _renderBrushChart(_fullData);
      _renderScatterCharts(_fullData);
      _renderInferencePanel(_fullData, _fullData);
      document.getElementById("corrRangeLabel").textContent = "Showing: full range";
    };
  }
}

// ── Brush chart (compact TVOC + eCO₂ over time) ─────────────────────────────

function _renderBrushChart(data) {
  const ts   = data.map(d => new Date(d.timestamp));
  const tvoc = data.map(d => d.tvoc);
  const eco2 = data.map(d => d.eco2);
  const pm25 = data.map(d => d.pm2_5);
  const hasPm = pm25.some(v => v != null);
  const titleFont = { color: isLight ? "#111" : "#ccc" };

  const traces = [
    {
      x: ts, y: tvoc, mode: "lines", name: "TVOC (ppb)",
      line: { color: "#22c55e", width: 1.5 },
      yaxis: "y",
    },
    {
      x: ts, y: eco2, mode: "lines", name: "eCO₂ (ppm)",
      line: { color: "#818cf8", width: 1.5 },
      yaxis: "y2",
    },
  ];
  if (hasPm) {
    traces.push({
      x: ts, y: pm25, mode: "lines", name: "PM2.5 (µg/m³)",
      line: { color: "#a78bfa", width: 1.5, dash: "dot" },
      yaxis: "y3",
    });
  }

  const layout = themeLayout({
    height: 170,
    margin: { t: 30, b: 30, l: 50, r: hasPm ? 90 : 50 },
    title: { text: "🧪 TVOC · eCO₂ · PM2.5 over time — drag to select a window", font: { ...titleFont, size: 12 } },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.35, font: { size: 10 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: { type: "date" },
    yaxis:  { title: "TVOC",  side: "left",  showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } },
    yaxis2: { title: "eCO₂",  side: "right", overlaying: "y", showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } },
    yaxis3: hasPm ? { title: "PM2.5", side: "right", overlaying: "y", anchor: "free", position: 1.0, showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } } : undefined,
    dragmode: "zoom",
  });

  Plotly.newPlot("corrBrushPlot", traces, layout, { responsive: true, displayModeBar: false });

  // Listen for zoom (relayout) events
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

    // Update range label
    const fmt = (d) => new Date(d).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const dateStr = new Date(x0).toLocaleDateString([], { day: "numeric", month: "short" });
    document.getElementById("corrRangeLabel").textContent =
      `Showing: ${dateStr} ${fmt(x0)} – ${fmt(x1)} (${subset.length} readings)`;
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
  const titleFont  = { color: isLight ? "#111" : "#ccc" };

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
    title: { text: "🔍 TVOC vs eCO₂ (colour = time)", font: titleFont },
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
    title: { text: "🔍 Temperature vs Humidity (colour = VPD zone)", font: titleFont },
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
        title: { text: "🔍 PM2.5 vs TVOC (colour = time)", font: titleFont },
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
        title: { text: "🔍 PM2.5 vs eCO₂ (colour = time)", font: titleFont },
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
    if (reg)        corrParts.push(`TVOC↔eCO₂ R² = ${reg.r2.toFixed(2)}`);
    if (regPmTvoc)  corrParts.push(`PM2.5↔TVOC R² = ${regPmTvoc.r2.toFixed(2)}`);
    if (regPmEco2)  corrParts.push(`PM2.5↔eCO₂ R² = ${regPmEco2.r2.toFixed(2)}`);
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
        srcDesc = `TVOC is elevated (${avgTvocSel != null ? Math.round(avgTvocSel) : "–"} ppb) but PM2.5 and eCO₂ are normal. This is typical of volatile organic sources that don't produce particles or CO₂: cleaning products, air fresheners, paint, adhesives, new furniture, or cosmetics.`;
        srcTip = "These sources can persist for hours or days. TVOC off-gassing from new furniture peaks in the first few weeks. Ventilation helps but the source needs to be removed or contained.";
      } else if (tvocHigh && eco2High && tvocEco2R2 > 0.4) {
        srcIcon = "👥"; srcCls = "insight-neutral";
        srcTitle = "Occupancy / breathing";
        srcDesc = `eCO₂ and TVOC are both elevated and correlated (R² = ${tvocEco2R2.toFixed(2)}), but PM2.5 is normal. This is the signature of human presence without combustion — CO₂ and body VOCs from breathing, metabolism, and skin.`;
        srcTip = "Open a window or increase the ventilation rate. CO₂ and TVOC from occupancy drop quickly with fresh air flow.";
      } else if (eco2High && !tvocHigh) {
        srcIcon = "🫁"; srcCls = "insight-neutral";
        srcTitle = "CO₂ build-up (minimal VOCs)";
        srcDesc = `eCO₂ is elevated (${avgEco2Sel != null ? Math.round(avgEco2Sel) : "–"} ppm) but TVOC and PM2.5 are normal. Pure CO₂ build-up with no matching VOCs or particles typically means occupants in a sealed room with minimal activity.`;
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
      trendText = `<strong>TVOC rising, eCO₂ ${eco2Dir}</strong><br>A VOC source is active without extra CO₂. Likely: cleaning products, paint, adhesives, cosmetics, or new materials.${hasPm && pm25Dir === "rising" ? " PM2.5 also rising — possibly a combustion source." : ""}`;
    } else if (eco2Dir === "rising" && tvocDir !== "rising") {
      trendIcon = "🫁"; trendClass = "insight-warn";
      trendText = `<strong>eCO₂ rising, TVOC ${tvocDir}</strong><br>CO₂ building up from occupants. The air feels stuffy before TVOC catches up. ${hasPm && pm25Dir === "rising" ? "PM2.5 also rising — check for a concurrent particle source." : ""}`;
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

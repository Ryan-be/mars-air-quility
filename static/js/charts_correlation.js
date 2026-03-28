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

  const layout = themeLayout({
    height: 160,
    margin: { t: 30, b: 30, l: 50, r: 50 },
    title: { text: "🧪 TVOC & eCO₂ over time — drag to select a window", font: { ...titleFont, size: 12 } },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.35, font: { size: 10 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: { type: "date" },
    yaxis: { title: "TVOC", side: "left", showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } },
    yaxis2: { title: "eCO₂", side: "right", overlaying: "y", showgrid: false, titlefont: { size: 10 }, tickfont: { size: 9 } },
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

  const fullTvoc = fullData.map(d => d.tvoc).filter(v => v != null);
  const fullEco2 = fullData.map(d => d.eco2).filter(v => v != null);
  const fullTemp = fullData.map(d => d.temperature).filter(v => v != null);
  const fullHum  = fullData.map(d => d.humidity).filter(v => v != null);

  // Averages
  const avgTvocSel  = avg(selTvoc),  avgTvocFull  = avg(fullTvoc);
  const avgEco2Sel  = avg(selEco2),  avgEco2Full  = avg(fullEco2);
  const avgTempSel  = avg(selTemp),  avgTempFull  = avg(fullTemp);
  const avgHumSel   = avg(selHum),   avgHumFull   = avg(fullHum);

  // Peaks in selection
  const peakTvoc = selTvoc.length ? Math.max(...selTvoc) : null;
  const peakEco2 = selEco2.length ? Math.max(...selEco2) : null;

  // Trends in selection
  const tvocTrend = trendSlope(selTvoc);
  const eco2Trend = trendSlope(selEco2);

  // Correlation in selection
  const pairs = subset.filter(d => d.eco2 != null && d.tvoc != null);
  const reg = pairs.length >= 2
    ? linearRegression(pairs.map(d => d.eco2), pairs.map(d => d.tvoc))
    : null;

  // ── Build inference cards ──────────────────────────────────────────────
  const cards = [];

  // 1. Correlation summary
  if (reg) {
    const r2 = reg.r2;
    let corrIcon, corrClass, corrText;
    if (r2 > 0.7) {
      corrIcon = "🔗"; corrClass = "insight-strong";
      corrText = `<strong>Strong link (R² = ${r2.toFixed(2)})</strong><br>TVOC and eCO₂ are moving together. This usually means one source is producing both — common causes include people breathing in the room, cooking, or a combustion source.`;
    } else if (r2 > 0.4) {
      corrIcon = "🔀"; corrClass = "insight-moderate";
      corrText = `<strong>Partial link (R² = ${r2.toFixed(2)})</strong><br>Some of the pollution is from a shared source, but not all of it. You may have a background source (e.g. furniture off-gassing TVOC) on top of a human source (CO₂ from breathing).`;
    } else {
      corrIcon = "📊"; corrClass = "insight-weak";
      corrText = `<strong>Weak link (R² = ${r2.toFixed(2)})</strong><br>TVOC and eCO₂ are behaving independently. This points to separate sources — e.g. eCO₂ from occupants and TVOC from cleaning products, paint, or materials.`;
    }
    cards.push({ icon: corrIcon, cls: corrClass, html: corrText });
  }

  // 2. Trend direction
  const trendLabel = (slope) => slope > 0.5 ? "rising" : slope < -0.5 ? "falling" : "stable";
  const tvocDir = trendLabel(tvocTrend);
  const eco2Dir = trendLabel(eco2Trend);

  let trendIcon, trendClass, trendText;
  if (tvocDir === "rising" && eco2Dir === "rising") {
    trendIcon = "📈"; trendClass = "insight-warn";
    trendText = `<strong>Both pollutants rising</strong><br>Air quality is getting worse in this window. If you have a fan or ventilation, this is a good time for it to activate. Common during cooking, gatherings, or when windows are closed.`;
  } else if (tvocDir === "falling" && eco2Dir === "falling") {
    trendIcon = "📉"; trendClass = "insight-good";
    trendText = `<strong>Both pollutants falling</strong><br>Air quality is improving — ventilation is likely working, or the source has been removed. Good sign.`;
  } else if (tvocDir === "rising" && eco2Dir !== "rising") {
    trendIcon = "🧪"; trendClass = "insight-warn";
    trendText = `<strong>TVOC rising, eCO₂ ${eco2Dir}</strong><br>A volatile organic compound source is active but it's not producing CO₂. Likely causes: cleaning products, air fresheners, new furniture or paint, adhesives, or cosmetics.`;
  } else if (eco2Dir === "rising" && tvocDir !== "rising") {
    trendIcon = "🫁"; trendClass = "insight-warn";
    trendText = `<strong>eCO₂ rising, TVOC ${tvocDir}</strong><br>CO₂ is climbing without matching TVOC. This typically means more people entered the room, or reduced ventilation. The air feels "stuffy" before TVOC catches up.`;
  } else {
    trendIcon = "➡️"; trendClass = "insight-neutral";
    trendText = `<strong>Pollutant levels are stable</strong><br>No significant change in this time window. The environment is in a steady state — either well-ventilated or with constant low-level sources.`;
  }
  cards.push({ icon: trendIcon, cls: trendClass, html: trendText });

  // 3. Comparison to full range (only if subset)
  if (_isSubset && avgTvocFull != null && avgTvocSel != null) {
    const tvocPct = ((avgTvocSel - avgTvocFull) / avgTvocFull * 100).toFixed(0);
    const eco2Pct = avgEco2Full ? ((avgEco2Sel - avgEco2Full) / avgEco2Full * 100).toFixed(0) : null;
    const tvocAbove = avgTvocSel > avgTvocFull;
    const compIcon = tvocAbove ? "⚠️" : "✅";
    const compClass = tvocAbove ? "insight-warn" : "insight-good";

    let compText = `<strong>Compared to the full time range</strong><br>`;
    compText += `TVOC: <strong>${Math.abs(tvocPct)}% ${tvocAbove ? "higher" : "lower"}</strong> than average`;
    compText += ` (${Math.round(avgTvocSel)} vs ${Math.round(avgTvocFull)} ppb)`;
    if (eco2Pct != null) {
      const eco2Above = avgEco2Sel > avgEco2Full;
      compText += `<br>eCO₂: <strong>${Math.abs(eco2Pct)}% ${eco2Above ? "higher" : "lower"}</strong> than average`;
      compText += ` (${Math.round(avgEco2Sel)} vs ${Math.round(avgEco2Full)} ppm)`;
    }

    if (peakTvoc != null && peakTvoc > 500) {
      compText += `<br><span class="insight-peak">Peak TVOC in this window: <strong>${Math.round(peakTvoc)} ppb</strong> — above WHO moderate threshold (250 ppb)</span>`;
    }
    if (peakEco2 != null && peakEco2 > 1000) {
      compText += `<br><span class="insight-peak">Peak eCO₂: <strong>${Math.round(peakEco2)} ppm</strong> — cognitive impairment level</span>`;
    }

    cards.push({ icon: compIcon, cls: compClass, html: compText });
  }

  // 4. Environmental context
  if (avgTempSel != null && avgHumSel != null) {
    let envIcon, envClass, envText;
    const svp = 0.6108 * Math.exp(17.27 * avgTempSel / (avgTempSel + 237.3));
    const vpd = svp * (1 - avgHumSel / 100);

    envText = `<strong>Environment in this window</strong><br>`;
    envText += `Temperature: ${avgTempSel.toFixed(1)} °C · Humidity: ${avgHumSel.toFixed(0)}% · VPD: ${vpd.toFixed(2)} kPa<br>`;

    if (avgHumSel > 70) {
      envIcon = "💧"; envClass = "insight-warn";
      envText += `High humidity can trap volatile compounds and encourage mould. Consider dehumidification.`;
    } else if (avgTempSel > 28) {
      envIcon = "🌡️"; envClass = "insight-warn";
      envText += `High temperature accelerates off-gassing from materials and increases VOC emissions. Ventilate if possible.`;
    } else if (vpd > 1.6) {
      envIcon = "🏜️"; envClass = "insight-warn";
      envText += `Very dry air (high VPD). This can irritate airways and make pollutant effects feel worse. Consider a humidifier.`;
    } else {
      envIcon = "🌿"; envClass = "insight-good";
      envText += `Conditions are within normal ranges. Temperature and humidity are not amplifying pollution effects.`;
    }
    cards.push({ icon: envIcon, cls: envClass, html: envText });
  }

  // 5. Actionable summary
  let actionIcon, actionClass, actionText;
  if (peakTvoc > 500 || peakEco2 > 1500) {
    actionIcon = "💡"; actionClass = "insight-action";
    actionText = `<strong>What to do</strong><br>Pollution in this window is elevated. Open a window or turn on ventilation. If this pattern repeats at the same time daily, check the <em>Patterns</em> tab to confirm — then schedule your fan to pre-emptively run.`;
  } else if (peakTvoc > 250 || peakEco2 > 1000) {
    actionIcon = "💡"; actionClass = "insight-action";
    actionText = `<strong>What to do</strong><br>Moderate pollution detected. Ventilation would help but it's not urgent. If this coincides with an activity (cooking, cleaning), running the fan during that activity is a good habit.`;
  } else {
    actionIcon = "💡"; actionClass = "insight-action-ok";
    actionText = `<strong>Looking good</strong><br>Pollution levels in this window are within safe ranges. No action needed.`;
  }
  cards.push({ icon: actionIcon, cls: actionClass, html: actionText });

  // Render
  panel.innerHTML = cards.map(c => `
    <div class="corr-insight-card ${c.cls}">
      <div class="corr-insight-icon">${c.icon}</div>
      <div class="corr-insight-body">${c.html}</div>
    </div>
  `).join("");
}

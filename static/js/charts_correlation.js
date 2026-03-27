import { isLight, themeLayout } from './theme.js';

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
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
  for (let i = 0; i < n; i++) {
    sumX  += x[i];  sumY  += y[i];
    sumXY += x[i] * y[i];
    sumX2 += x[i] * x[i];
    sumY2 += y[i] * y[i];
  }
  const slope     = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
  const intercept = (sumY - slope * sumX) / n;
  const ssRes     = y.reduce((s, yi, i) => s + (yi - (slope * x[i] + intercept)) ** 2, 0);
  const meanY     = sumY / n;
  const ssTot     = y.reduce((s, yi) => s + (yi - meanY) ** 2, 0);
  const r2        = ssTot > 0 ? 1 - ssRes / ssTot : 0;
  return { slope, intercept, r2 };
}

export function renderCorrelationCharts(data) {
  if (!data || data.length === 0) return;

  const eco2 = data.map(d => d.eco2);
  const tvoc = data.map(d => d.tvoc);
  const temp = data.map(d => d.temperature);
  const hum  = data.map(d => d.humidity);
  const vpd  = data.map(d => d.vpd_kpa);
  const timeColour = data.map((_, i) => i / (data.length - 1 || 1));
  const titleFont  = { color: isLight ? "#111" : "#ccc" };

  // Filter valid pairs for regression
  const pairs = data.filter(d => d.eco2 != null && d.tvoc != null);
  const regX = pairs.map(d => d.eco2);
  const regY = pairs.map(d => d.tvoc);
  const reg  = pairs.length >= 2 ? linearRegression(regX, regY) : null;

  const traces = [{
    x: eco2, y: tvoc, mode: "markers", name: "Readings",
    marker: {
      color: timeColour,
      colorscale: "Viridis",
      size: 6,
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

  Plotly.newPlot("tempHumScatterPlot", [{
    x: hum, y: temp, mode: "markers",
    marker: { color: vpd.map(vpdColour), size: 6 },
    hovertemplate: "Humidity: %{x}%<br>Temp: %{y} °C<extra></extra>",
  }], themeLayout({
    title: { text: "🔍 Temperature vs Humidity (colour = VPD zone)", font: titleFont },
    xaxis: { title: "Humidity (%)" },
    yaxis: { title: "Temperature (°C)" },
  }), { responsive: true });
}

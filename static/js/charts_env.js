import { isLight, themeLayout } from './theme.js';

function absHumidity(tempC, rh) {
  return 6.112 * Math.exp(17.67 * tempC / (tempC + 243.5)) * rh * 2.1674 / (273.15 + tempC);
}

function dewPoint(tempC, rh) {
  const a = 17.625, b = 243.04;
  const alpha = Math.log(rh / 100) + a * tempC / (b + tempC);
  return (b * alpha) / (a - alpha);
}

export function renderEnvCharts(sensorData, weatherData) {
  if (!sensorData || sensorData.length === 0) return;

  const ts   = sensorData.map(d => new Date(d.timestamp));
  const temp = sensorData.map(d => d.temperature);
  const hum  = sensorData.map(d => d.humidity);
  const fanW = sensorData.map(d => d.fan_power_w);
  const wTs  = weatherData.map(d => new Date(d.timestamp));
  const wTemp = weatherData.map(d => d.temp);
  const wHum  = weatherData.map(d => d.humidity);
  const titleFont = { color: isLight ? "#111" : "#ccc" };

  Plotly.newPlot("tempOverlayPlot", [
    { x: ts, y: temp, mode: "lines+markers", name: "Indoor",
      line: { color: "deeppink" } },
    { x: wTs, y: wTemp, mode: "lines+markers", name: "Outdoor",
      line: { color: "#f59e0b", dash: "dash" } },
  ], themeLayout({
    title: { text: "🌡️ Indoor vs Outdoor Temperature (°C)", font: titleFont },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.3, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 75 },
  }), { responsive: true });

  Plotly.newPlot("humOverlayPlot", [
    { x: ts, y: hum, mode: "lines+markers", name: "Indoor",
      line: { color: "dodgerblue" } },
    { x: wTs, y: wHum, mode: "lines+markers", name: "Outdoor",
      line: { color: "#38bdf8", dash: "dash" } },
  ], themeLayout({
    title: { text: "💧 Indoor vs Outdoor Humidity (%)", font: titleFont },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.3, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 75 },
  }), { responsive: true });

  const ahValues = sensorData.map(d =>
    d.temperature != null && d.humidity != null
      ? absHumidity(d.temperature, d.humidity) : null
  );
  Plotly.newPlot("absHumPlot", [{
    x: ts, y: ahValues, mode: "lines+markers", name: "Abs. Humidity",
    line: { color: "#818cf8" }, connectgaps: false,
  }], themeLayout({
    title: { text: "💧 Absolute Humidity (g/m³)", font: titleFont },
  }), { responsive: true });

  const dpValues = sensorData.map(d =>
    d.temperature != null && d.humidity != null
      ? dewPoint(d.temperature, d.humidity) : null
  );
  Plotly.newPlot("dewPointPlot", [
    { x: ts, y: temp, mode: "lines", name: "Air Temp",
      line: { color: "deeppink", width: 1.5 } },
    { x: ts, y: dpValues, mode: "lines+markers", name: "Dew Point",
      line: { color: "#34d399", dash: "dot" }, connectgaps: false },
  ], themeLayout({
    title: { text: "🌡️ Dew Point vs Air Temperature (°C)", font: titleFont },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.3, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 75 },
  }), { responsive: true });

  const fanState = fanW.map(w => (w == null ? null : w > 0 ? 1 : 0));
  Plotly.newPlot("fanStatePlot", [{
    x: ts, y: fanState, mode: "lines", name: "Fan",
    line: { color: "#a78bfa", shape: "hv" },
    connectgaps: false,
    fill: "tozeroy", fillcolor: "rgba(167,139,250,0.15)",
  }], themeLayout({
    title: { text: "🌀 Fan State", font: titleFont },
    yaxis: { tickvals: [0, 1], ticktext: ["Off", "On"], range: [-0.1, 1.3] },
  }), { responsive: true });

  // ── VPD chart ─────────────────────────────────────────────────────────────
  const vpdValues = sensorData.map(d => d.vpd_kpa);
  const vpdShapes = [
    { y0: 0,   y1: 0.4, color: "rgba(30,120,255,0.10)"  },
    { y0: 0.4, y1: 0.8, color: "rgba(80,200,120,0.12)"  },
    { y0: 0.8, y1: 1.2, color: "rgba(80,200,120,0.20)"  },
    { y0: 1.2, y1: 1.6, color: "rgba(255,180,0,0.12)"   },
    { y0: 1.6, y1: 3.0, color: "rgba(220,60,60,0.12)"   },
  ].map(z => ({
    type: "rect", xref: "paper", yref: "y",
    x0: 0, x1: 1, y0: z.y0, y1: z.y1,
    fillcolor: z.color, line: { width: 0 }, layer: "below",
  }));

  const zoneAnnotations = [
    { y: 0.2,  label: "Too humid"  },
    { y: 0.6,  label: "Seedlings"  },
    { y: 1.0,  label: "Ideal"      },
    { y: 1.4,  label: "High"       },
    { y: 1.75, label: "Stress"     },
  ].map(z => ({
    xref: "paper", yref: "y", x: 1, y: z.y,
    text: z.label, showarrow: false,
    font: { size: 9, color: isLight ? "#888" : "#666" },
    xanchor: "right",
  }));

  const hasVpd = vpdValues && vpdValues.some(v => v != null);
  if (hasVpd) {
    Plotly.newPlot("vpdPlot", [{
      x: ts, y: vpdValues,
      mode: "lines+markers", name: "VPD",
      line: { color: "#38bdf8", width: 2 },
      marker: {
        color: vpdValues.map(v => {
          if (v == null) return "#555";
          if (v < 0.4)  return "#3b82f6";
          if (v < 0.8)  return "#22c55e";
          if (v < 1.2)  return "#16a34a";
          if (v < 1.6)  return "#f59e0b";
          return "#ef4444";
        }),
        size: 5,
      },
      connectgaps: false,
    }], themeLayout({
      title: { text: "🌱 Vapour Pressure Deficit (kPa)", font: titleFont },
      yaxis: { rangemode: "tozero" },
      shapes: vpdShapes,
      annotations: zoneAnnotations,
    }), { responsive: true });
  } else {
    document.getElementById("vpdPlot").innerHTML =
      '<p style="color:#666;padding:1em;font-size:0.85em">🌱 VPD — collecting data…</p>';
  }
}

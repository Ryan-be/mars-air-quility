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
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.22, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 60 },
  }), { responsive: true });

  Plotly.newPlot("humOverlayPlot", [
    { x: ts, y: hum, mode: "lines+markers", name: "Indoor",
      line: { color: "dodgerblue" } },
    { x: wTs, y: wHum, mode: "lines+markers", name: "Outdoor",
      line: { color: "#38bdf8", dash: "dash" } },
  ], themeLayout({
    title: { text: "💧 Indoor vs Outdoor Humidity (%)", font: titleFont },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.22, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 60 },
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
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.22, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 60 },
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
}

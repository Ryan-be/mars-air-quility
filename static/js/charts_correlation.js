import { isLight, themeLayout } from './theme.js';

function vpdColour(vpd) {
  if (vpd == null) return "#555";
  if (vpd < 0.4)  return "#3b82f6";
  if (vpd < 0.8)  return "#22c55e";
  if (vpd < 1.2)  return "#16a34a";
  if (vpd < 1.6)  return "#f59e0b";
  return "#ef4444";
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

  Plotly.newPlot("tvocEco2ScatterPlot", [{
    x: eco2, y: tvoc, mode: "markers",
    marker: {
      color: timeColour,
      colorscale: "Viridis",
      size: 6,
      colorbar: { title: "Time →", thickness: 12, len: 0.6 },
    },
    hovertemplate: "eCO₂: %{x} ppm<br>TVOC: %{y} ppb<extra></extra>",
  }], themeLayout({
    title: { text: "🔍 TVOC vs eCO₂ (colour = time)", font: titleFont },
    xaxis: { title: "eCO₂ (ppm)" },
    yaxis: { title: "TVOC (ppb)" },
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

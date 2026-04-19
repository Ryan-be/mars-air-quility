import { isLight, themeLayout } from './theme.js';
import { attachAnnotationHandler } from './annotations.js';

const titleFont = () => ({ color: isLight ? "#111" : "#b0bec5", size: 12 });

export function renderClimateCharts(data) {
  if (!data || data.length === 0) return;
  const timestamps   = data.map(d => new Date(d.timestamp));
  const temperatures = data.map(d => d.temperature);
  const humidities   = data.map(d => d.humidity);
  const ids          = data.map(d => d.id);

  Plotly.newPlot("tempPlot", [{
    x: timestamps, y: temperatures,
    mode: "lines+markers", name: "Temperature",
    line: { color: "deeppink" }, customdata: ids
  }], themeLayout({
    title: { text: "Temperature (°C)", font: titleFont() },
  }), { responsive: true }).then(() => attachAnnotationHandler("tempPlot"));

  Plotly.newPlot("humPlot", [{
    x: timestamps, y: humidities,
    mode: "lines+markers", name: "Humidity",
    line: { color: "dodgerblue" }, customdata: ids
  }], themeLayout({
    title: { text: "Humidity (%)", font: titleFont() },
  }), { responsive: true }).then(() => attachAnnotationHandler("humPlot"));
}

export function renderGasCharts(data) {
  if (!data || data.length === 0) return;
  const timestamps   = data.map(d => new Date(d.timestamp));
  const eco2         = data.map(d => d.eco2);
  const tvoc         = data.map(d => d.tvoc);
  const annotations  = data.map(d => d.annotation);
  const ids          = data.map(d => d.id);

  Plotly.newPlot("eco2Plot", [{
    x: timestamps, y: eco2,
    mode: "lines+markers", name: "eCO₂ (ppm)",
    line: { color: "yellowgreen" }, customdata: ids
  }], themeLayout({
    title: { text: "eCO₂ (ppm)", font: titleFont() },
  }), { responsive: true }).then(() => attachAnnotationHandler("eco2Plot"));

  const rollingTVOC = tvoc.map((_, i, arr) => {
    const slice = arr.slice(Math.max(i - 5, 0), i + 1);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
  const rateOfChange = tvoc.map((v, i, arr) => i > 0 ? v - arr[i - 1] : 0);
  const eventAnnotations = annotations.map((note, i) => note && note.trim() ? {
    x: timestamps[i], y: tvoc[i], text: note,
    showarrow: true, arrowhead: 2, ax: 0, ay: -40,
    bgcolor: "#1b2d3e", font: { color: "#b0bec5" }
  } : null).filter(Boolean);

  Plotly.newPlot("tvocPlot", [
    {
      x: timestamps, y: tvoc, mode: "markers", name: "TVOC",
      marker: { color: tvoc.map(v => v <= 250 ? "#56f000" : v <= 500 ? "#fce83a" : "#ff3838"), size: 6 },
      customdata: ids, text: annotations, hoverinfo: "x+y+text"
    },
    {
      x: timestamps, y: rollingTVOC, mode: "lines", name: "Rolling avg",
      line: { dash: "dash", color: "#52667a", width: 2 }
    },
    {
      x: timestamps, y: rateOfChange, mode: "lines", name: "Rate of change",
      yaxis: "y2", line: { color: "#4dacff", width: 2 }
    }
  ], themeLayout({
    title: { text: "TVOC (ppb)", font: titleFont() },
    yaxis2: {
      title: "Δppb", overlaying: "y", side: "right",
      showgrid: false, gridcolor: "#1b2d3e", zerolinecolor: "#2b659b",
      color: "#b0bec5", automargin: true, tickfont: { color: "#b0bec5", size: 10 },
    },
    legend: { x: 0.5, y: 1.3, bgcolor: "rgba(0,0,0,0)", orientation: "h", xanchor: "center", font: { color: "#b0bec5" } },
    margin: { t: 75 },
    annotations: eventAnnotations,
  }), { responsive: true }).then(() => attachAnnotationHandler("tvocPlot"));
}

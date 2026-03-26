import { isLight, themeLayout } from './theme.js';
import { attachAnnotationHandler } from './annotations.js';

export function renderCharts(timestamps, temperatures, humidities, eco2, tvoc, annotations, ids, powerValues) {
  Plotly.newPlot("tempPlot", [{
    x: timestamps, y: temperatures,
    mode: "lines+markers", name: "Temperature",
    line: { color: "deeppink" }, customdata: ids
  }], themeLayout({
    title: { text: "🌡️ Temperature", font: { color: isLight ? "#111" : "#ccc" } },
    yaxis: { title: "°C" }
  }), { responsive: true }).then(() => attachAnnotationHandler("tempPlot"));

  Plotly.newPlot("humPlot", [{
    x: timestamps, y: humidities,
    mode: "lines+markers", name: "Humidity",
    line: { color: "dodgerblue" }, customdata: ids
  }], themeLayout({
    title: { text: "💧 Humidity", font: { color: isLight ? "#111" : "#ccc" } },
    yaxis: { title: "%" }
  }), { responsive: true }).then(() => attachAnnotationHandler("humPlot"));

  Plotly.newPlot("eco2Plot", [{
    x: timestamps, y: eco2,
    mode: "lines+markers", name: "eCO₂",
    line: { color: "yellowgreen" }, customdata: ids
  }], themeLayout({
    title: { text: "🫁 eCO₂", font: { color: isLight ? "#111" : "#ccc" } },
    yaxis: { title: "ppm" }
  }), { responsive: true }).then(() => attachAnnotationHandler("eco2Plot"));

  const rollingTVOC = tvoc.map((_, i, arr) => {
    const slice = arr.slice(Math.max(i - 5, 0), i + 1);
    return slice.reduce((a, b) => a + b, 0) / slice.length;
  });
  const rateOfChange = tvoc.map((v, i, arr) => i > 0 ? v - arr[i - 1] : 0);
  const eventAnnotations = annotations.map((note, i) => note && note.trim() ? {
    x: timestamps[i], y: tvoc[i], text: note,
    showarrow: true, arrowhead: 2, ax: 0, ay: -40,
    bgcolor: "#444", font: { color: "#fff" }
  } : null).filter(Boolean);

  Plotly.newPlot("tvocPlot", [
    {
      x: timestamps, y: tvoc, mode: "markers", name: "TVOC",
      marker: { color: tvoc.map(v => v <= 250 ? "#2d8a2d" : v <= 500 ? "#c87800" : "#b03030"), size: 6 },
      customdata: ids, text: annotations, hoverinfo: "x+y+text"
    },
    {
      x: timestamps, y: rollingTVOC, mode: "lines", name: "Rolling avg",
      line: { dash: "dash", color: "#888", width: 2 }
    },
    {
      x: timestamps, y: rateOfChange, mode: "lines", name: "Rate of change",
      yaxis: "y2", line: { color: "cyan", width: 2 }
    }
  ], themeLayout({
    title: { text: "🧪 TVOC", font: { color: isLight ? "#111" : "#ccc" } },
    yaxis: { title: "ppb" },
    yaxis2: {
      title: "Rate of change", overlaying: "y", side: "right",
      showgrid: false, gridcolor: "#2a2a2a", zerolinecolor: "#333", color: "#ccc"
    },
    legend: { x: 0.5, y: 1.15, bgcolor: "rgba(0,0,0,0)", orientation: "h", xanchor: "center" },
    annotations: eventAnnotations,
  }), { responsive: true }).then(() => attachAnnotationHandler("tvocPlot"));

  if (powerValues.some(v => v != null)) {
    Plotly.newPlot("powerPlot", [{
      x: timestamps, y: powerValues,
      mode: "lines", name: "Fan power",
      line: { color: "#a78bfa" },
      connectgaps: false,
    }], themeLayout({
      title: { text: "⚡ Fan Power (W)", font: { color: isLight ? "#111" : "#ccc" } },
      yaxis: { title: "Watts", rangemode: "tozero" }
    }), { responsive: true });
  } else {
    document.getElementById("powerPlot").innerHTML =
      `<p style="color:#666;padding:1em;font-size:0.85em">⚡ Fan power — no energy meter data yet (plug may not support it)</p>`;
  }
}

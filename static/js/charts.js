import { isLight, themeLayout } from './theme.js';
import { attachAnnotationHandler } from './annotations.js';

export function renderCharts(timestamps, temperatures, humidities, eco2, tvoc, annotations, ids, powerValues, vpdValues) {
  Plotly.newPlot("tempPlot", [{
    x: timestamps, y: temperatures,
    mode: "lines+markers", name: "Temperature",
    line: { color: "deeppink" }, customdata: ids
  }], themeLayout({
    title: { text: "🌡️ Temperature (°C)", font: { color: isLight ? "#111" : "#ccc" } },
  }), { responsive: true }).then(() => attachAnnotationHandler("tempPlot"));

  Plotly.newPlot("humPlot", [{
    x: timestamps, y: humidities,
    mode: "lines+markers", name: "Humidity",
    line: { color: "dodgerblue" }, customdata: ids
  }], themeLayout({
    title: { text: "💧 Humidity (%)", font: { color: isLight ? "#111" : "#ccc" } },
  }), { responsive: true }).then(() => attachAnnotationHandler("humPlot"));

  Plotly.newPlot("eco2Plot", [{
    x: timestamps, y: eco2,
    mode: "lines+markers", name: "eCO₂",
    line: { color: "yellowgreen" }, customdata: ids
  }], themeLayout({
    title: { text: "🫁 eCO₂ (ppm)", font: { color: isLight ? "#111" : "#ccc" } },
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
    title: { text: "🧪 TVOC (ppb)", font: { color: isLight ? "#111" : "#ccc" } },
    yaxis2: {
      title: "Δppb", overlaying: "y", side: "right",
      showgrid: false, gridcolor: "#2a2a2a", zerolinecolor: "#333", color: "#ccc",
      automargin: true,
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
      yaxis: { rangemode: "tozero" }
    }), { responsive: true });
  } else {
    document.getElementById("powerPlot").innerHTML =
      `<p style="color:#666;padding:1em;font-size:0.85em">⚡ Fan power — no energy meter data yet (plug may not support it)</p>`;
  }

  // ── VPD chart ─────────────────────────────────────────────────────────────
  // VPD zones (kPa): <0.4 too humid | 0.4–0.8 seedlings | 0.8–1.2 ideal
  //                  1.2–1.6 high | >1.6 plant stress
  const vpdShapes = [
    { y0: 0,   y1: 0.4, color: "rgba(30,120,255,0.10)",  label: "Too humid"    },
    { y0: 0.4, y1: 0.8, color: "rgba(80,200,120,0.12)",  label: "Seedlings"    },
    { y0: 0.8, y1: 1.2, color: "rgba(80,200,120,0.20)",  label: "Ideal veg"    },
    { y0: 1.2, y1: 1.6, color: "rgba(255,180,0,0.12)",   label: "High"         },
    { y0: 1.6, y1: 3.0, color: "rgba(220,60,60,0.12)",   label: "Plant stress" },
  ].map(z => ({
    type: "rect", xref: "paper", yref: "y",
    x0: 0, x1: 1, y0: z.y0, y1: z.y1,
    fillcolor: z.color, line: { width: 0 }, layer: "below",
  }));

  const zoneAnnotations = [
    { y: 0.2,  label: "Too humid"    },
    { y: 0.6,  label: "Seedlings"    },
    { y: 1.0,  label: "Ideal"        },
    { y: 1.4,  label: "High"         },
    { y: 1.75, label: "Stress"       },
  ].map(z => ({
    xref: "paper", yref: "y", x: 1, y: z.y,
    text: z.label, showarrow: false,
    font: { size: 9, color: isLight ? "#888" : "#666" },
    xanchor: "right",
  }));

  const hasVpd = vpdValues && vpdValues.some(v => v != null);
  if (hasVpd) {
    Plotly.newPlot("vpdPlot", [{
      x: timestamps, y: vpdValues,
      mode: "lines+markers", name: "VPD",
      line: { color: "#38bdf8", width: 2 },
      marker: {
        color: vpdValues.map(v => {
          if (v == null) return "#555";
          if (v < 0.4)  return "#3b82f6";   // too humid — blue
          if (v < 0.8)  return "#22c55e";   // seedlings — green
          if (v < 1.2)  return "#16a34a";   // ideal — dark green
          if (v < 1.6)  return "#f59e0b";   // high — amber
          return "#ef4444";                  // stress — red
        }),
        size: 5,
      },
      connectgaps: false,
    }], themeLayout({
      title: { text: "🌱 Vapour Pressure Deficit (kPa)", font: { color: isLight ? "#111" : "#ccc" } },
      yaxis: { rangemode: "tozero" },
      shapes: vpdShapes,
      annotations: zoneAnnotations,
    }), { responsive: true });
  } else {
    document.getElementById("vpdPlot").innerHTML =
      `<p style="color:#666;padding:1em;font-size:0.85em">🌱 VPD — collecting data…</p>`;
  }
}

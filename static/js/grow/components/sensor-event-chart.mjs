/**
 * Sensor-event chart: moisture % line + watering event vertical bars.
 * Uses Plotly (already loaded by base.html) for the actual rendering.
 * Reusable for any (sensor series, discrete events, target band) chart.
 */

export function renderSensorEventChart(container, data) {
  const { moisture, events, targetPct = 55, deadband = 5 } = data;

  if (typeof Plotly === "undefined") {
    container.textContent = "Plotly not loaded";
    return;
  }

  const traces = [
    {
      x: moisture.map(m => m.ts),
      y: moisture.map(m => m.pct),
      mode: "lines",
      line: { color: "#56f000", width: 2 },
      name: "Moisture %",
      fill: "tozeroy",
      fillcolor: "rgba(86, 240, 0, 0.15)",
      yaxis: "y",
    },
    {
      x: events.map(e => e.ts),
      y: events.map(e => e.duration_s),
      type: "bar",
      marker: { color: events.map(e => e.trigger === "manual" ? "#ffb302" : "#4dacff") },
      name: "Pulse (s)",
      yaxis: "y2",
    },
  ];

  const layout = {
    paper_bgcolor: "#0a1219",
    plot_bgcolor: "#0a1219",
    font: { color: "#c2d2e3", family: "Roboto, sans-serif" },
    margin: { l: 40, r: 60, t: 20, b: 30 },
    height: 240,
    xaxis: { showgrid: false },
    yaxis: { range: [0, 100], title: "%", gridcolor: "#1c2733" },
    yaxis2: { overlaying: "y", side: "right", range: [0, 30],
              title: "pulse s", showgrid: false },
    shapes: [
      // Target band
      { type: "rect", xref: "paper", x0: 0, x1: 1, yref: "y",
        y0: targetPct - deadband, y1: targetPct + deadband,
        fillcolor: "#56f000", opacity: 0.08, line: { width: 0 } },
    ],
    showlegend: false,
  };

  Plotly.newPlot(container, traces, layout, { displayModeBar: false });
}

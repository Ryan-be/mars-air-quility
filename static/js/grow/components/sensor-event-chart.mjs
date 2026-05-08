/**
 * Sensor-event chart: moisture % line + watering event vertical bars.
 * Uses Plotly (already loaded by base.html / grow_unit_detail.html) for
 * the actual rendering. Reusable for any (sensor series, discrete
 * events, target band) chart.
 *
 * Defensive about input shape: the /history endpoint returns the
 * events array under the key `watering_events` (not `events`); raw
 * moisture rows use `pct` while downsampled rows use `pct_avg`.
 * Handle both. An empty unit (no data yet) shows a placeholder
 * rather than crashing — important for the camera-only first-
 * deployment posture where moisture readings don't exist yet.
 */

export function renderSensorEventChart(container, data) {
  // Accept both the API contract (`watering_events`) and the legacy
  // shorthand (`events`). Default empty array so a unit with no data
  // renders an empty chart instead of throwing on undefined.map().
  const moisture = data.moisture || [];
  const events = data.watering_events || data.events || [];
  const targetPct = data.targetPct ?? 55;
  const deadband = data.deadband ?? 5;

  if (typeof Plotly === "undefined") {
    container.textContent = "Plotly not loaded";
    return;
  }

  if (moisture.length === 0 && events.length === 0) {
    // Camera-only / brand-new unit: nothing to chart yet. Render a
    // soft placeholder so the panel doesn't look broken.
    container.textContent = "No moisture or watering data yet.";
    container.style.padding = "20px";
    container.style.color = "#9aa6b2";
    container.style.fontSize = "12px";
    return;
  }

  const traces = [
    {
      x: moisture.map(m => m.ts),
      // Raw rows have `pct`; downsampled rows have `pct_avg`. Handle both.
      y: moisture.map(m => m.pct ?? m.pct_avg ?? null),
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

import { isLight, themeLayout } from './theme.js';

export function renderPatternCharts(data) {
  const NO_DATA = '<p style="color:#666;padding:1em;font-size:0.85em">Not enough data yet.</p>';
  if (!data || data.length < 12) {
    ["hourHeatmapPlot", "dailyBandPlot"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = NO_DATA;
    });
    return;
  }

  const DAY_NAMES = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const titleFont = { color: isLight ? "#111" : "#ccc" };

  // Hour-of-day heatmap (TVOC)
  const matrix = {};
  DAY_NAMES.forEach(day => {
    matrix[day] = {};
    for (let h = 0; h < 24; h++) matrix[day][h] = [];
  });
  data.forEach(row => {
    if (row.tvoc == null) return;
    const dt  = new Date(row.timestamp);
    const day = DAY_NAMES[dt.getDay()];
    matrix[day][dt.getHours()].push(row.tvoc);
  });
  const zData = DAY_NAMES.map(day =>
    Array.from({ length: 24 }, (_, h) => {
      const vals = matrix[day][h];
      return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    })
  );
  Plotly.newPlot("hourHeatmapPlot", [{
    type: "heatmap",
    x: Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}:00`),
    y: DAY_NAMES,
    z: zData,
    colorscale: "RdYlGn",
    reversescale: true,
    colorbar: { title: "ppb", thickness: 12 },
    hoverongaps: false,
  }], themeLayout({
    title: { text: "📅 Avg TVOC by Hour & Day (ppb)", font: titleFont },
    margin: { t: 50, r: 80, b: 60, l: 50 },
  }), { responsive: true });

  // Daily temperature band
  const dayMap = {};
  data.forEach(row => {
    if (row.temperature == null) return;
    const d = new Date(row.timestamp).toISOString().slice(0, 10);
    if (!dayMap[d]) dayMap[d] = [];
    dayMap[d].push(row.temperature);
  });
  const days    = Object.keys(dayMap).sort();
  const dayMins = days.map(d => Math.min(...dayMap[d]));
  const dayMaxs = days.map(d => Math.max(...dayMap[d]));
  const dayAvgs = days.map(d => dayMap[d].reduce((a, b) => a + b, 0) / dayMap[d].length);

  Plotly.newPlot("dailyBandPlot", [
    {
      x: [...days, ...days.slice().reverse()],
      y: [...dayMaxs, ...dayMins.slice().reverse()],
      fill: "toself", fillcolor: "rgba(255,75,130,0.15)",
      line: { color: "transparent" }, name: "Min–Max range", hoverinfo: "skip",
    },
    { x: days, y: dayAvgs, mode: "lines+markers", name: "Daily avg",
      line: { color: "deeppink", width: 2 }, marker: { size: 5 } },
    { x: days, y: dayMaxs, mode: "lines", name: "Daily max",
      line: { color: "deeppink", dash: "dot", width: 1 } },
    { x: days, y: dayMins, mode: "lines", name: "Daily min",
      line: { color: "#ff88aa", dash: "dot", width: 1 } },
  ], themeLayout({
    title: { text: "📅 Daily Temperature Range (°C)", font: titleFont },
    legend: { orientation: "h", x: 0.5, xanchor: "center", y: 1.3, bgcolor: "rgba(0,0,0,0)" },
    margin: { t: 75 },
  }), { responsive: true });
}

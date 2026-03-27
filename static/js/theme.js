export let isLight = false;

export const DARK_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "#1a1a1a",
  font: { color: "#ccc" },
  xaxis: { gridcolor: "#2a2a2a", zerolinecolor: "#333", title: "Time" },
  yaxis: { gridcolor: "#2a2a2a", zerolinecolor: "#333" },
  margin: { t: 40, r: 20, b: 50, l: 55 }, autosize: true,
};

export const LIGHT_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "#f9f9f9",
  font: { color: "#111" },
  xaxis: { gridcolor: "#ddd", zerolinecolor: "#ccc", title: "Time" },
  yaxis: { gridcolor: "#ddd", zerolinecolor: "#ccc" },
  margin: { t: 40, r: 20, b: 50, l: 55 }, autosize: true,
};

export function themeLayout(overrides) {
  const base = isLight ? LIGHT_LAYOUT : DARK_LAYOUT;
  return Object.assign({}, base, overrides, {
    xaxis: Object.assign({}, base.xaxis, overrides.xaxis),
    yaxis: Object.assign({}, base.yaxis, overrides.yaxis),
  });
}

export function toggleTheme(onToggle) {
  isLight = !isLight;
  document.body.classList.toggle("light", isLight);
  const cb = document.getElementById("themeToggle");
  if (cb) cb.checked = isLight;
  if (onToggle) onToggle();
}

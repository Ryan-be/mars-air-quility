const THEME_KEY = 'mlss_theme';

// Initialise from localStorage; base.html's inline <script> already applied the
// body class before paint, so we only need to sync the JS state here.
export let isLight = localStorage.getItem(THEME_KEY) === 'light';

export const DARK_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "#101923",
  font: { color: "#b0bec5", size: 11 },
  xaxis: { gridcolor: "#1b2d3e", zerolinecolor: "#2b659b", automargin: true, tickfont: { color: "#b0bec5", size: 10 } },
  yaxis: { gridcolor: "#1b2d3e", zerolinecolor: "#2b659b", automargin: true, tickfont: { color: "#b0bec5", size: 10 } },
  margin: { t: 40, r: 20, b: 40, l: 45 }, autosize: true,
};

export const LIGHT_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "#f9f9f9",
  font: { color: "#111" },
  xaxis: { gridcolor: "#ddd", zerolinecolor: "#ccc", automargin: true },
  yaxis: { gridcolor: "#ddd", zerolinecolor: "#ccc", automargin: true },
  margin: { t: 40, r: 20, b: 40, l: 45 }, autosize: true,
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
  localStorage.setItem(THEME_KEY, isLight ? 'light' : 'dark');
  const cb = document.getElementById("themeToggle");
  if (cb) cb.checked = isLight;
  if (onToggle) onToggle();
}

// Called on DOMContentLoaded by base.html's inline script cannot reach ES modules,
// so page JS modules must call this to sync the checkbox state.
export function applyPersistedTheme() {
  document.body.classList.toggle("light", isLight);
  const cb = document.getElementById("themeToggle");
  if (cb) cb.checked = isLight;
}

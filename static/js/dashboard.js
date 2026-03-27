import { toggleTheme } from './theme.js';
import { updateInsights, updateWeather } from './insights.js';
import { renderCharts } from './charts.js';
import { setFanControl, fetchFanStatus } from './fan.js';
import { fetchHealth } from './health.js';

// ── Expose onclick handlers to HTML ──────────────────────────────────────────
window.toggleTheme   = () => toggleTheme(fetchData);
window.setFanControl = setFanControl;
window.downloadCSV   = () => {
  window.open(`/api/download?range=${document.getElementById("range").value}`, "_blank");
};

// ── Trend indicator ───────────────────────────────────────────────────────────
function trend(current, previous) {
  if (current == null || previous == null) return "";
  const d = current - previous;
  if (d > 0.1)  return "↑ ";
  if (d < -0.1) return "↓ ";
  return "";
}

// ── Main data fetch ───────────────────────────────────────────────────────────
document.getElementById("range").addEventListener("change", fetchData);

async function fetchData() {
  const range = document.getElementById("range").value;
  const res = await fetch(`/api/data?range=${range}`);
  const data = await res.json();
  if (!Array.isArray(data) || data.length === 0) {
    document.getElementById("last-updated").textContent = "No data for selected range.";
    return;
  }

  data.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  const ids          = data.map(d => d.id);
  const timestamps   = data.map(d => new Date(d.timestamp));
  const temperatures = data.map(d => d.temperature);
  const humidities   = data.map(d => d.humidity);
  const eco2         = data.map(d => d.eco2);
  const tvoc         = data.map(d => d.tvoc);
  const annotations  = data.map(d => d.annotation);
  const powerValues  = data.map(d => d.fan_power_w);

  if (timestamps.length < 2) {
    document.getElementById("last-updated").textContent = "Not enough data points.";
    return;
  }

  const avg = arr => (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1);
  const min = arr => Math.min(...arr);
  const max = arr => Math.max(...arr);

  // Sensor stat cards
  const currentTemp = temperatures.at(-1);
  document.getElementById("tempValue").textContent = `${trend(currentTemp, temperatures.at(-2))}${currentTemp?.toFixed(1) ?? "--"} °C`;
  document.getElementById("tempMin").textContent = min(temperatures).toFixed(1);
  document.getElementById("tempAvg").textContent = avg(temperatures);
  document.getElementById("tempMax").textContent = max(temperatures).toFixed(1);

  const currentHum = humidities.at(-1);
  document.getElementById("humValue").textContent = `${trend(currentHum, humidities.at(-2))}${currentHum?.toFixed(1) ?? "--"} %`;
  document.getElementById("humMin").textContent = min(humidities).toFixed(1);
  document.getElementById("humAvg").textContent = avg(humidities);
  document.getElementById("humMax").textContent = max(humidities).toFixed(1);

  const currentEco2 = eco2.at(-1);
  document.getElementById("eco2Value").textContent = `${trend(currentEco2, eco2.at(-2))}${currentEco2 ?? "--"} ppm`;
  document.getElementById("eco2Min").textContent = min(eco2);
  document.getElementById("eco2Avg").textContent = avg(eco2);
  document.getElementById("eco2Max").textContent = max(eco2);

  const currentTvoc = tvoc.at(-1);
  document.getElementById("tvocValue").textContent = `${trend(currentTvoc, tvoc.at(-2))}${currentTvoc ?? "--"} ppb`;
  document.getElementById("tvocMin").textContent = min(tvoc);
  document.getElementById("tvocAvg").textContent = avg(tvoc);
  document.getElementById("tvocMax").textContent = max(tvoc);

  // Cache current indoor values for weather comparison
  _lastIndoorTemp = currentTemp;
  _lastIndoorHum  = currentHum;

  // Insight cards
  updateInsights(currentTemp, currentHum, currentTvoc, currentEco2, eco2);

  // Charts
  renderCharts(timestamps, temperatures, humidities, eco2, tvoc, annotations, ids, powerValues);

  document.getElementById("last-updated").textContent = "Last updated: " + new Date().toLocaleString();
}

// ── Weather fetch ─────────────────────────────────────────────────────────────
let _lastIndoorTemp = null;
let _lastIndoorHum  = null;

async function fetchWeather() {
  try {
    const res = await fetch("/api/weather");
    const w   = await res.json();
    updateWeather(w, _lastIndoorTemp, _lastIndoorHum);
  } catch {
    updateWeather(null, null, null);
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────
fetchData();
fetchHealth();
fetchFanStatus();
fetchWeather();
setInterval(() => { fetchData(); fetchHealth(); fetchFanStatus(); }, 15000);
setInterval(fetchWeather, 5 * 60 * 1000);  // weather every 5 minutes

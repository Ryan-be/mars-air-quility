import { toggleTheme } from './theme.js';
import { updateInsights, updateWeather, updateForecast, updateDailyForecast } from './insights.js';
import { fetchHealth } from './health.js';

window.toggleTheme = () => toggleTheme(fetchData);

function trend(current, previous) {
  if (current == null || previous == null) return "";
  const d = current - previous;
  if (d > 0.1)  return "↑ ";
  if (d < -0.1) return "↓ ";
  return "";
}

let _lastIndoorTemp = null;
let _lastIndoorHum  = null;

async function fetchData() {
  const res = await fetch("/api/data?range=15m");
  const data = await res.json();
  if (!Array.isArray(data) || data.length === 0) {
    document.getElementById("last-updated").textContent = "No recent data.";
    return;
  }
  data.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  const temperatures = data.map(d => d.temperature);
  const humidities   = data.map(d => d.humidity);
  const eco2         = data.map(d => d.eco2);
  const tvoc         = data.map(d => d.tvoc);

  const currentTemp = temperatures.at(-1);
  const currentHum  = humidities.at(-1);
  const currentEco2 = eco2.at(-1);
  const currentTvoc = tvoc.at(-1);

  document.getElementById("tempValue").textContent =
    `${trend(currentTemp, temperatures.at(-2))}${currentTemp?.toFixed(1) ?? "--"} °C`;
  document.getElementById("humValue").textContent =
    `${trend(currentHum, humidities.at(-2))}${currentHum?.toFixed(1) ?? "--"} %`;
  document.getElementById("eco2Value").textContent =
    `${trend(currentEco2, eco2.at(-2))}${currentEco2 ?? "--"} ppm`;
  document.getElementById("tvocValue").textContent =
    `${trend(currentTvoc, tvoc.at(-2))}${currentTvoc ?? "--"} ppb`;

  _lastIndoorTemp = currentTemp;
  _lastIndoorHum  = currentHum;

  updateInsights(currentTemp, currentHum, currentTvoc, currentEco2, eco2);
  document.getElementById("last-updated").textContent =
    "Last updated: " + new Date().toLocaleString();
}

async function fetchWeather() {
  try {
    const res = await fetch("/api/weather");
    updateWeather(await res.json(), _lastIndoorTemp, _lastIndoorHum);
  } catch { updateWeather(null, null, null); }
}

async function fetchForecast() {
  try {
    const res = await fetch("/api/weather/forecast");
    if (!res.ok) return;
    updateForecast((await res.json()).hours);
  } catch { /* location not set */ }
}

async function fetchDailyForecast() {
  try {
    const res = await fetch("/api/weather/forecast/daily");
    if (!res.ok) return;
    updateDailyForecast((await res.json()).days);
  } catch { /* location not set */ }
}

// ── Sensor card detail popups ────────────────────────────────────────────────
const SENSOR_INFO = {
  temp: {
    title: "🌡️ Temperature",
    sensor: "AHT20 (I²C)",
    unit: "°C",
    range: "18 – 26 °C (comfort zone)",
    desc: "Measured by the AHT20 sensor via I²C. Readings outside the comfort zone can affect plant health, sleep quality, and cognitive performance. Sustained temperatures above 28°C may stress plants; below 15°C can slow growth.",
  },
  hum: {
    title: "💧 Humidity",
    sensor: "AHT20 (I²C)",
    unit: "%",
    range: "40 – 60 % RH (ideal)",
    desc: "Relative humidity from the AHT20 sensor. Below 30% can cause dry skin and irritation; above 70% promotes mould growth. For a grow room, pair this with VPD on the insights panel for a more accurate picture.",
  },
  eco2: {
    title: "🫁 eCO₂",
    sensor: "SGP30 (I²C)",
    unit: "ppm",
    range: "400 – 800 ppm (normal indoor)",
    desc: "Equivalent CO₂ estimated by the SGP30 metal-oxide sensor. Above 1000 ppm cognitive function declines. Above 2000 ppm consider immediate ventilation. The sensor needs ~15s warm-up after power-on; first readings may be inaccurate.",
  },
  tvoc: {
    title: "🧪 TVOC",
    sensor: "SGP30 (I²C)",
    unit: "ppb",
    range: "0 – 250 ppb (WHO good)",
    desc: "Total Volatile Organic Compounds from the SGP30 sensor. Sources include paint, cleaning products, cooking, and off-gassing furniture. Levels above 500 ppb are considered high by WHO guidelines and warrant ventilation.",
  },
};

document.querySelectorAll(".stat-card[data-sensor]").forEach(card => {
  card.addEventListener("click", () => {
    const key  = card.dataset.sensor;
    const info = SENSOR_INFO[key];
    if (!info) return;
    const dialog = document.getElementById("sensorDialog");
    document.getElementById("sdTitle").textContent   = info.title;
    document.getElementById("sdSensor").textContent  = info.sensor;
    document.getElementById("sdCurrent").textContent =
      card.querySelector(".current").textContent;
    document.getElementById("sdRange").textContent   = info.range;
    document.getElementById("sdDesc").textContent    = info.desc;
    dialog.showModal();
    dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
  });
});

fetchData();
fetchHealth();
fetchWeather();
fetchForecast();
fetchDailyForecast();
setInterval(fetchData,          15000);
setInterval(fetchHealth,        15000);
setInterval(fetchWeather,   5 * 60 * 1000);
setInterval(fetchForecast,  60 * 60 * 1000);
setInterval(fetchDailyForecast, 6 * 60 * 60 * 1000);

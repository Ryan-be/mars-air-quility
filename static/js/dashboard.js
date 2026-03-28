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

// ── Insight card detail popups ───────────────────────────────────────────────
const INSIGHT_INFO = {
  aq: {
    title: "🌿 Air Quality Index",
    range: "Good / Moderate / Poor",
    desc: "A combined assessment of TVOC and eCO₂ levels. 'Good' means TVOC ≤ 250 ppb and eCO₂ ≤ 800 ppm — safe for prolonged occupancy. 'Moderate' (TVOC ≤ 500, eCO₂ ≤ 1500) suggests opening a window. 'Poor' indicates active pollution — ventilate immediately. This is not an official AQI; it is derived from WHO indoor air guidelines.",
  },
  dew: {
    title: "💦 Dew Point",
    range: "Ideally 5 – 15 °C below air temp",
    desc: "The temperature at which moisture in the air condenses onto surfaces. When the gap between air temperature and dew point is less than 3°C, condensation is likely — leading to wet walls, mould, and equipment corrosion. Calculated using the Magnus formula from your temperature and humidity readings.",
  },
  feels: {
    title: "🌡️ Feels Like (Apparent Temperature)",
    range: "18 – 24 °C (comfortable)",
    desc: "How the temperature actually feels to a person, accounting for humidity. High humidity makes warm air feel hotter because sweat evaporates more slowly. Calculated using the Australian Bureau of Meteorology apparent temperature formula (indoor version, no wind component). Useful for assessing comfort even when the thermometer looks fine.",
  },
  co2: {
    title: "🧠 CO₂ Cognitive Alert",
    range: "Normal ≤ 800 / Elevated ≤ 1000 / Impaired ≤ 2000",
    desc: "Research shows CO₂ above 1000 ppm reduces decision-making performance by up to 15%. Above 2000 ppm causes headaches, drowsiness, and difficulty concentrating. This alert monitors your eCO₂ levels against these cognitive impact thresholds. In a sealed room with one person, CO₂ can rise from 400 to 1000 ppm in under 2 hours.",
  },
  vpd: {
    title: "🌱 Vapour Pressure Deficit (VPD)",
    range: "0.4 – 0.8 kPa (seedlings) / 0.8 – 1.2 kPa (veg) / 1.2 – 1.6 kPa (flower)",
    desc: "VPD measures the 'drying power' of the air — how strongly the atmosphere pulls moisture from leaf surfaces. Low VPD (< 0.4 kPa) means the air is nearly saturated, slowing transpiration and promoting mould. High VPD (> 1.6 kPa) means the air is very dry, causing stomata to close and stressing the plant. It combines temperature and humidity into the single most useful metric for plant health.",
  },
  ttt: {
    title: "⏱️ Time to CO₂ Threshold",
    range: "Stable = not rising",
    desc: "A linear extrapolation from the last 6 readings predicting when eCO₂ will reach 1000 ppm (the cognitive impairment threshold). If CO₂ is falling or steady, it shows 'Stable'. Under 10 minutes means you should ventilate soon. This is an estimate — opening a window or door will reset the trend immediately.",
  },
  outTemp: {
    title: "🌡️ Outdoor Temperature",
    range: "Compared against your indoor reading",
    desc: "Current outdoor temperature from the Open-Meteo weather API for your configured location. The sub-text shows how much warmer or cooler it is indoors, helping you decide whether to open windows for natural cooling or keep them closed to retain heat.",
  },
  outHum: {
    title: "💧 Outdoor Humidity",
    range: "Compared against your indoor reading",
    desc: "Current outdoor relative humidity. If outdoor humidity is significantly lower than indoors, ventilating will help dry out the space. If higher, opening windows may increase indoor moisture and mould risk.",
  },
  uv: {
    title: "☀️ UV Index",
    range: "Low < 3 / Moderate 3–5 / High 6–7 / Very High 8–10 / Extreme 11+",
    desc: "The UV index measures the intensity of ultraviolet radiation from the sun. High UV (6+) can cause sunburn in under 20 minutes. Relevant for deciding when to open blinds or move plants near windows — UV light benefits plant growth but can bleach sensitive foliage.",
  },
  wind: {
    title: "💨 Wind Speed",
    range: "Calm < 5 mph / Breezy 5–15 / Windy 15–25",
    desc: "Current wind speed at your location. Affects how quickly air exchanges when you open a window. Light breeze is ideal for ventilation; strong winds may cause rapid temperature drops indoors and disturb lightweight plants or equipment.",
  },
  vent: {
    title: "🔄 Ventilation Opportunity",
    range: "Good / Partial / Poor",
    desc: "Compares outdoor and indoor conditions to assess whether opening a window would improve your environment. 'Good' means outdoor air is both cooler (by 1°C+) and drier (by 5%+ RH) — ideal for ventilation. 'Partial' means one condition is better but not both. 'Poor' means outdoor conditions would not help or could make things worse.",
  },
};

document.querySelectorAll("[data-insight]").forEach(card => {
  card.addEventListener("click", () => {
    const key  = card.dataset.insight;
    const info = INSIGHT_INFO[key];
    if (!info) return;
    const dialog  = document.getElementById("insightDialog");
    document.getElementById("idTitle").textContent   = info.title;
    document.getElementById("idCurrent").textContent =
      card.querySelector(".value").textContent;
    document.getElementById("idRange").textContent   = info.range;
    document.getElementById("idDesc").textContent    = info.desc;
    dialog.showModal();
    dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
  });
});

// ── Health item detail popups ────────────────────────────────────────────────
const HEALTH_INFO = {
  aht20:   { title: "🌡️ AHT20 Sensor", desc: "Temperature and humidity sensor connected via I²C bus. Measures 0–100% RH and -40 to +85°C. If offline, temperature and humidity readings will show 0." },
  sgp30:   { title: "🧪 SGP30 Sensor", desc: "Metal-oxide gas sensor for eCO₂ and TVOC via I²C. Requires 15s warm-up and 12h baseline calibration for accurate readings. If offline, air quality data will be unavailable." },
  plug:    { title: "🔌 Smart Plug (Kasa)", desc: "TP-Link Kasa smart plug controlling the ventilation fan. Provides on/off switching and real-time power consumption monitoring. If unreachable, automatic fan control will be disabled." },
  cpu:     { title: "🖥️ CPU Usage", desc: "Current processor utilisation of the Raspberry Pi. Sustained usage above 80% may slow sensor polling and web responses. The sensor logging loop and Flask server are the main consumers." },
  mem:     { title: "🧠 Memory Usage", desc: "RAM utilisation. The Pi typically has 1–8 GB. If memory exceeds 85%, the OS may start swapping to SD card, significantly slowing performance. Consider reducing log frequency if this is high." },
  disk:    { title: "💾 Disk Usage", desc: "SD card space used. The SQLite database grows over time with sensor logs. At 10s intervals, expect ~250 MB/year. If disk exceeds 90%, old data should be exported and purged." },
  db:      { title: "🗄️ Database Size", desc: "Size of the SQLite sensor_data.db file. Contains sensor readings, annotations, weather logs, fan settings, and inferences. Regular growth is normal; sudden jumps may indicate a logging issue." },
  uptime:  { title: "⏱️ Pi Uptime", desc: "How long the Raspberry Pi has been running since last reboot. Long uptimes are good — frequent reboots may indicate power issues or SD card corruption." },
  service: { title: "🚀 Service Uptime", desc: "How long the MLSS Flask application has been running. Resets when the service restarts. If this is much shorter than Pi uptime, the service may have crashed and been restarted by systemd." },
};

document.querySelectorAll("[data-health]").forEach(item => {
  item.addEventListener("click", () => {
    const key  = item.dataset.health;
    const info = HEALTH_INFO[key];
    if (!info) return;
    const dialog = document.getElementById("insightDialog");
    document.getElementById("idTitle").textContent   = info.title;
    document.getElementById("idCurrent").textContent =
      item.querySelector(".h-value").textContent;
    document.getElementById("idRange").textContent   = "";
    document.getElementById("idDesc").textContent    = info.desc;
    dialog.showModal();
    dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
  });
});

// ── Environment inference feed ───────────────────────────────────────────────
const SEVERITY_LABEL = { info: "Info", warning: "Warning", critical: "Critical" };
const SEVERITY_CLS   = { info: "inf-info", warning: "inf-warning", critical: "inf-critical" };
let _inferences = [];

async function fetchInferences() {
  try {
    const res = await fetch("/api/inferences?limit=50");
    if (!res.ok) return;
    _inferences = await res.json();
    _renderInferenceFeed();
  } catch { /* not available yet */ }
}

function _renderInferenceFeed() {
  const feed = document.getElementById("inferenceFeed");
  const countEl = document.getElementById("inferenceCount");
  if (!feed) return;

  if (!_inferences.length) {
    feed.innerHTML = '<div class="inference-empty">No inferences yet — data is being analysed.</div>';
    if (countEl) countEl.textContent = "";
    return;
  }

  const active = _inferences.filter(i => !i.dismissed);
  if (countEl) countEl.textContent = active.length ? `(${active.length})` : "";

  feed.innerHTML = _inferences.slice(0, 30).map(inf => {
    const sev = SEVERITY_CLS[inf.severity] || "inf-info";
    const time = new Date(inf.created_at).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
    return `
      <button class="inference-card ${sev}${inf.dismissed ? ' dismissed' : ''}"
              data-inf-id="${inf.id}" title="Tap for details">
        <div class="inf-card-left">
          <span class="inf-card-type">${inf.event_type.replace(/_/g, " ")}</span>
          <span class="inf-card-summary">${inf.title}</span>
        </div>
        <div class="inf-card-right">
          <span class="inf-card-time">${time}</span>
          <span class="inf-card-conf">${Math.round(inf.confidence * 100)}%</span>
        </div>
      </button>`;
  }).join("");

  feed.onclick = (e) => {
    const card = e.target.closest(".inference-card");
    if (!card) return;
    const id = parseInt(card.dataset.infId, 10);
    _openInferenceDialog(id);
  };
}

function _openInferenceDialog(id) {
  const inf = _inferences.find(i => i.id === id);
  if (!inf) return;
  const dialog = document.getElementById("inferenceDialog");

  document.getElementById("infTitle").textContent = inf.title;

  const badge = document.getElementById("infSeverity");
  badge.textContent = SEVERITY_LABEL[inf.severity] || inf.severity;
  badge.className = `inf-badge ${SEVERITY_CLS[inf.severity] || ""}`;

  document.getElementById("infTime").textContent =
    new Date(inf.created_at).toLocaleString();
  document.getElementById("infConfidence").textContent =
    `${Math.round(inf.confidence * 100)}% confidence`;

  document.getElementById("infDescription").textContent = inf.description;
  document.getElementById("infAction").textContent = inf.action || "No specific action needed.";

  // Evidence
  const evEl = document.getElementById("infEvidence");
  if (inf.evidence && typeof inf.evidence === "object") {
    evEl.innerHTML = Object.entries(inf.evidence).map(([k, v]) =>
      `<div class="inf-ev-row"><span class="fd-label">${k.replace(/_/g, " ")}</span><span class="fd-value">${v}</span></div>`
    ).join("");
  } else {
    evEl.textContent = "No detailed evidence available.";
  }

  // Annotation section
  const annoSec = document.getElementById("infAnnotationSection");
  if (inf.annotation) {
    annoSec.style.display = "";
    document.getElementById("infAnnotationText").textContent = inf.annotation;
  } else {
    annoSec.style.display = "none";
  }

  // Notes
  document.getElementById("infNotes").value = inf.user_notes || "";
  document.getElementById("infSaveNote").onclick = async () => {
    const notes = document.getElementById("infNotes").value;
    try {
      await fetch(`/api/inferences/${id}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      inf.user_notes = notes;
    } catch { /* ignore */ }
  };

  dialog.showModal();
  dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
}

fetchData();
fetchHealth();
fetchWeather();
fetchForecast();
fetchDailyForecast();
fetchInferences();
setInterval(fetchData,          15000);
setInterval(fetchHealth,        15000);
setInterval(fetchWeather,   5 * 60 * 1000);
setInterval(fetchForecast,  60 * 60 * 1000);
setInterval(fetchDailyForecast, 6 * 60 * 60 * 1000);
setInterval(fetchInferences,    60 * 1000);

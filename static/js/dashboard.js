import { toggleTheme } from './theme.js';
import { updateInsights, updateWeather, updateForecast, updateDailyForecast } from './insights.js';
import { fetchHealth, applyHealth } from './health.js';

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

// ── Gas sensor trend history (resistance in Ω; lower = higher concentration) ──
let _gasCoHistory  = [];
let _gasNo2History = [];
let _gasNh3History = [];

// Returns % by which gas concentration has risen vs the session average.
// Positive = resistance dropped = gas level rising.
function _gasTrendPct(vals) {
  const v = vals.filter(x => x != null);
  if (v.length < 2) return 0;
  const current = v.at(-1);
  const avg = v.reduce((a, b) => a + b, 0) / v.length;
  if (avg === 0) return 0;
  return (avg - current) / avg * 100;
}

function _gasTrendInfo(pct) {
  if (pct > 15) return { arrow: "↑↑", text: "rising fast", cls: "gas-bad" };
  if (pct > 5)  return { arrow: "↑",  text: "rising",      cls: "gas-warn" };
  if (pct < -5) return { arrow: "↓",  text: "falling",     cls: "gas-good" };
  return { arrow: "→", text: "stable", cls: "" };
}

function _pm25Class(v) {
  if (v == null) return "";
  if (v <= 12)  return "pm-good";
  if (v <= 35)  return "pm-moderate";
  return "pm-unhealthy";
}

function _updatePmCard(pm25, prevPm25, pm1, pm10, pmStale, pmTimestamp) {
  const el = document.getElementById("pm25Value");
  const card = el?.closest(".stat-card");
  if (el) {
    el.textContent = pm25 != null
      ? `${trend(pm25, prevPm25)}${pm25} µg/m³`
      : "--";
  }
  if (card) {
    card.classList.remove("pm-good", "pm-moderate", "pm-unhealthy");
    const cls = _pm25Class(pm25);
    if (cls) card.classList.add(cls);
  }
  const pm1El  = document.getElementById("pm1SubValue");
  const pm10El = document.getElementById("pm10SubValue");
  if (pm1El)  pm1El.textContent  = pm1  != null ? `${pm1} µg/m³`  : "--";
  if (pm10El) pm10El.textContent = pm10 != null ? `${pm10} µg/m³` : "--";

  // Show stale indicator when displaying a cached PM reading
  const staleEl = document.getElementById("pmStaleHint");
  if (staleEl) {
    if (pmStale && pmTimestamp) {
      const ago = _timeAgo(new Date(pmTimestamp));
      staleEl.textContent = `⏱ cached from ${ago}`;
      staleEl.classList.add("visible");
    } else {
      staleEl.textContent = "";
      staleEl.classList.remove("visible");
    }
  }
}

function _updateGasCard() {
  const co  = _gasCoHistory.at(-1)  ?? null;
  const no2 = _gasNo2History.at(-1) ?? null;
  const nh3 = _gasNh3History.at(-1) ?? null;

  const coTrend  = _gasTrendInfo(_gasTrendPct(_gasCoHistory));
  const no2Trend = _gasTrendInfo(_gasTrendPct(_gasNo2History));
  const nh3Trend = _gasTrendInfo(_gasTrendPct(_gasNh3History));

  // Worst trend of the three drives the card colour
  const maxPct   = Math.max(
    _gasTrendPct(_gasCoHistory),
    _gasTrendPct(_gasNo2History),
    _gasTrendPct(_gasNh3History),
  );
  const cardTrend = _gasTrendInfo(maxPct);

  const coEl    = document.getElementById("gasCoValue");
  const trendEl = document.getElementById("gasTrend");
  const no2El   = document.getElementById("gasNo2SubValue");
  const nh3El   = document.getElementById("gasNh3SubValue");
  const card    = coEl?.closest(".stat-card");

  if (coEl)   coEl.textContent   = co  != null ? `${co} Ω` : "--";
  if (trendEl) {
    trendEl.textContent = co != null ? `CO ${coTrend.arrow} ${coTrend.text}` : "--";
    trendEl.className   = `gas-trend${coTrend.cls ? ` ${coTrend.cls}` : ""}`;
  }
  if (no2El) no2El.textContent = no2 != null ? `${no2} Ω ${no2Trend.arrow}` : "--";
  if (nh3El) nh3El.textContent = nh3 != null ? `${nh3} Ω ${nh3Trend.arrow}` : "--";

  if (card) {
    card.classList.remove("gas-good", "gas-warn", "gas-bad");
    if (cardTrend.cls) card.classList.add(cardTrend.cls);
  }
}

function _timeAgo(date) {
  const secs = Math.round((Date.now() - date.getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

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

  const pm25 = data.map(d => d.pm2_5);
  const currentPm25 = pm25.at(-1);
  const currentPm1  = data.at(-1)?.pm1_0 ?? null;
  const currentPm10 = data.at(-1)?.pm10  ?? null;

  document.getElementById("tempValue").textContent =
    `${trend(currentTemp, temperatures.at(-2))}${currentTemp?.toFixed(1) ?? "--"} °C`;
  document.getElementById("humValue").textContent =
    `${trend(currentHum, humidities.at(-2))}${currentHum?.toFixed(1) ?? "--"} %`;
  document.getElementById("eco2Value").textContent =
    `${trend(currentEco2, eco2.at(-2))}${currentEco2 ?? "--"} ppm`;
  document.getElementById("tvocValue").textContent =
    `${trend(currentTvoc, tvoc.at(-2))}${currentTvoc ?? "--"} ppb`;

  _updatePmCard(currentPm25, pm25.at(-2), currentPm1, currentPm10);
  _gasCoHistory  = data.map(d => d.gas_co).filter(v => v != null);
  _gasNo2History = data.map(d => d.gas_no2).filter(v => v != null);
  _gasNh3History = data.map(d => d.gas_nh3).filter(v => v != null);
  _updateGasCard();

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
    desc: "Estimated CO₂ from the SGP30 metal-oxide sensor. Estimated from TVOC via SGP30 algorithm — not a direct CO₂ measurement. Above 1000 ppm cognitive function declines. Above 2000 ppm consider immediate ventilation. The sensor needs ~15s warm-up after power-on; first readings may be inaccurate.",
  },
  tvoc: {
    title: "🧪 TVOC",
    sensor: "SGP30 (I²C)",
    unit: "ppb",
    range: "0 – 250 ppb (WHO good)",
    desc: "Total Volatile Organic Compounds from the SGP30 sensor. Sources include paint, cleaning products, cooking, and off-gassing furniture. Levels above 500 ppb are considered high by WHO guidelines and warrant ventilation.",
  },
  gas: {
    title: "🔥 Gas (MICS6814)",
    sensor: "Pimoroni MICS6814 (I²C)",
    unit: "kΩ (resistance)",
    range: "Relative — higher resistance = lower concentration",
    desc: "Three-in-one gas sensor measuring CO (carbon monoxide, reducing), NO₂ (nitrogen dioxide, oxidising), and NH₃ (ammonia, reducing) via analogue resistance channels. Readings are proportional to gas concentration — compare trends rather than absolute values. Useful for detecting combustion byproducts, vehicle exhaust, and agricultural emissions.",
  },
  pm: {
    title: "🌫️ Particulate Matter",
    sensor: "PMSA003 (UART)",
    unit: "µg/m³",
    range: "PM2.5: 0–12 good · 12–35 moderate · >35 unhealthy",
    desc: "Fine particulate matter from the PMSA003 sensor via UART. PM2.5 (≤2.5 µm) penetrates deep into the lungs and bloodstream. WHO 24-hr guideline: 15 µg/m³. PM10 (≤10 µm) irritates the upper respiratory tract. Sources include cooking, candles, dust, traffic, and wildfires.",
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
    title: "🧠 eCO₂ Cognitive Alert",
    range: "Normal ≤ 800 / Elevated ≤ 1000 / Impaired ≤ 2000",
    desc: "Research shows CO₂ above 1000 ppm reduces decision-making performance by up to 15%. Above 2000 ppm causes headaches, drowsiness, and difficulty concentrating. This alert monitors your eCO₂ levels against these cognitive impact thresholds. In a sealed room with one person, CO₂ can rise from 400 to 1000 ppm in under 2 hours.",
  },
  vpd: {
    title: "🌱 Vapour Pressure Deficit (VPD)",
    range: "0.4 – 0.8 kPa (seedlings) / 0.8 – 1.2 kPa (veg) / 1.2 – 1.6 kPa (flower)",
    desc: "VPD measures the 'drying power' of the air — how strongly the atmosphere pulls moisture from leaf surfaces. Low VPD (< 0.4 kPa) means the air is nearly saturated, slowing transpiration and promoting mould. High VPD (> 1.6 kPa) means the air is very dry, causing stomata to close and stressing the plant. It combines temperature and humidity into the single most useful metric for plant health.",
  },
  ttt: {
    title: "⏱️ Time to eCO₂ Threshold",
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
  mics6814:{ title: "🔥 MICS6814 Gas Sensor", desc: "Pimoroni MICS6814 3-in-1 gas sensor connected via I²C. Measures CO (carbon monoxide), NO₂ (nitrogen dioxide), and NH₃ (ammonia) as analogue resistance values. If offline, gas readings will show '--'." },
  pm:      { title: "🌫️ PM Sensor (PMSA003)", desc: "Particulate matter sensor connected via UART serial (/dev/ttyS0). Measures PM1.0, PM2.5, and PM10 concentrations in µg/m³. Uses laser scattering to count airborne particles. If offline, particulate matter readings will show '--'." },
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

// ── Attribution badge ────────────────────────────────────────────────────────
const _SOURCE_META = {
  biological_offgas:   { label: 'Biological Off-gassing', emoji: '🌿', colour: '#22c55e' },
  chemical_offgassing: { label: 'Chemical Off-gassing',   emoji: '🧪', colour: '#a855f7' },
  cooking:             { label: 'Cooking',                 emoji: '🍳', colour: '#f97316' },
  combustion:          { label: 'Combustion',              emoji: '🔥', colour: '#ef4444' },
  external_pollution:  { label: 'External Pollution',      emoji: '🌍', colour: '#6b7280' },
  personal_care:       { label: 'Personal Care Products',  emoji: '🧴', colour: '#ec4899' },
};

const _ATTRIBUTION_TOOLTIP =
  'The attribution engine scores this event against known source fingerprints \u2014 ' +
  'combinations of sensor patterns associated with specific real-world causes.';

function renderAttributionBadge(inf) {
  const src = (inf.evidence && inf.evidence.attribution_source) || inf.attribution_source;
  const conf = (inf.evidence && inf.evidence.attribution_confidence) || inf.attribution_confidence;
  const runnerSrc  = (inf.evidence && inf.evidence.runner_up_source) || inf.runner_up_source;
  const runnerConf = (inf.evidence && inf.evidence.runner_up_confidence) || inf.runner_up_confidence;
  if (!src) return '';
  const meta = _SOURCE_META[src] || { label: src, emoji: '', colour: '#6b7280' };
  const pct  = conf ? Math.round(conf * 100) : '?';
  const pill = `<span class="source-pill" style="background:${meta.colour};color:#fff;" title="${_ATTRIBUTION_TOOLTIP}">${meta.emoji} ${meta.label} \u2014 ${pct}% <span class="chip-info">ⓘ</span></span>`;
  let runnerHtml = '';
  if (runnerSrc && runnerConf != null && conf != null && runnerConf >= conf - 0.15) {
    const rm = _SOURCE_META[runnerSrc] || { label: runnerSrc };
    runnerHtml = `<div class="runner-up">Also consistent with: ${rm.label} (${Math.round(runnerConf * 100)}%)</div>`;
  }
  return `<div class="attribution-row"><span class="attribution-label">Source:</span> ${pill}${runnerHtml}</div>`;
}

// ── Detection method chip ────────────────────────────────────────────────────
const _CHIP_METHOD_TOOLTIP =
  'Rule = a fixed threshold was crossed. ' +
  'Statistical = an unusual reading compared to this sensor\u2019s learned normal. ' +
  'ML = an unusual pattern across multiple sensors simultaneously.';

function renderDetectionChip(detectionMethod) {
  const cls = { rule: 'chip--rule', statistical: 'chip--statistical', ml: 'chip--ml' }[detectionMethod] || 'chip--rule';
  const label = { rule: 'Rule', statistical: 'Statistical', ml: 'ML' }[detectionMethod] || 'Rule';
  return `<span class="chip ${cls}" title="${_CHIP_METHOD_TOOLTIP}">${label} <span class="chip-info">ⓘ</span></span>`;
}

// ── Environment inference feed ───────────────────────────────────────────────
const SEVERITY_LABEL = { info: "Info", warning: "Warning", critical: "Critical" };
const SEVERITY_CLS   = { info: "inf-info", warning: "inf-warning", critical: "inf-critical" };
let _inferences = [];
let _activeCategory = "all";
let _categoriesLoaded = false;

async function _loadCategories() {
  if (_categoriesLoaded) return;
  try {
    const res = await fetch("/api/inferences/categories");
    if (!res.ok) return;
    const cats = await res.json();
    const bar = document.getElementById("inferenceFilters");
    if (!bar) return;
    for (const [key, label] of Object.entries(cats)) {
      const btn = document.createElement("button");
      btn.className = "inf-filter";
      btn.dataset.category = key;
      btn.textContent = label;
      bar.appendChild(btn);
    }
    const mlBtn = document.createElement("button");
    mlBtn.className = "inf-filter";
    mlBtn.dataset.category = "ml";
    mlBtn.textContent = "🧠 ML";
    bar.appendChild(mlBtn);
    bar.addEventListener("click", (e) => {
      const btn = e.target.closest(".inf-filter");
      if (!btn) return;
      bar.querySelectorAll(".inf-filter").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _activeCategory = btn.dataset.category;
      _renderInferenceFeed();
    });
    _categoriesLoaded = true;
  } catch { /* categories not available */ }
}

async function fetchInferences() {
  try {
    await _loadCategories();
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

  // Apply category filter — "ml" is a special filter for ML-detected events
  let filtered = _inferences;
  if (_activeCategory && _activeCategory !== "all") {
    if (_activeCategory === "ml") {
      filtered = _inferences.filter(i => i.detection_method === "ml");
    } else {
      filtered = _inferences.filter(i => i.category === _activeCategory);
    }
  }

  if (!filtered.length) {
    const msg = _inferences.length
      ? "No inferences in this category."
      : "No inferences yet — data is being analysed.";
    feed.innerHTML = `<div class="inference-empty">${msg}</div>`;
    if (countEl) countEl.textContent = "";
    return;
  }

  const active = filtered.filter(i => !i.dismissed);
  if (countEl) countEl.textContent = active.length ? `(${active.length})` : "";

  feed.innerHTML = filtered.slice(0, 30).map(inf => {
    const sev = SEVERITY_CLS[inf.severity] || "inf-info";
    const time = new Date(inf.created_at).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
    const chip = renderDetectionChip(inf.detection_method || 'rule');
    return `
      <button class="inference-card ${sev} inf-cat-${inf.category ?? 'other'}${inf.dismissed ? ' dismissed' : ''}"
              data-inf-id="${inf.id}" title="Tap for details">
        <div class="inf-card-left">
          <div class="inf-card-badges">${chip} <span class="inf-badge ${SEVERITY_CLS[inf.severity] || 'inf-info'} inf-badge-sm">${SEVERITY_LABEL[inf.severity] || inf.severity}</span></div>
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

function _parseNum(v) {
  const m = String(v).match(/-?[\d.]+/);
  return m ? parseFloat(m[0]) : null;
}

// Returns "ev-good", "ev-warn", "ev-bad", or "" (neutral)
function _evidenceColor(key, val) {
  const n = _parseNum(val);
  if (n === null) return "";

  // Percentage-based "out of range" / "above X" — lower is better
  if (/out_of_range|above_moderate|above_800/.test(key)) {
    if (n <= 0) return "ev-good";
    if (n <= 20) return "ev-warn";
    return "ev-bad";
  }
  // Percentage-based "optimal time" — higher is better
  if (/optimal_time/.test(key)) {
    if (n >= 80) return "ev-good";
    if (n >= 50) return "ev-warn";
    return "ev-bad";
  }
  // Score out of 100
  if (key === "score") {
    if (n >= 80) return "ev-good";
    if (n >= 60) return "ev-warn";
    return "ev-bad";
  }
  // Temperature averages
  if (key === "temp_avg" || key === "mean_temp") {
    if (n >= 18 && n <= 26) return "ev-good";
    if (n >= 15 && n <= 28) return "ev-warn";
    return "ev-bad";
  }
  // Humidity averages
  if (key === "humidity_avg" || key === "mean_humidity") {
    if (n >= 40 && n <= 60) return "ev-good";
    if (n >= 30 && n <= 70) return "ev-warn";
    return "ev-bad";
  }
  // TVOC (avg or peak or current)
  if (/tvoc/.test(key) && /ppb/.test(val)) {
    if (n <= 250) return "ev-good";
    if (n <= 500) return "ev-warn";
    return "ev-bad";
  }
  // CO2 (estimated)
  if (/eco2|co2/.test(key) && /ppm/.test(val)) {
    if (n <= 800) return "ev-good";
    if (n <= 1500) return "ev-warn";
    return "ev-bad";
  }
  // VPD
  if (/vpd/.test(key) && /kPa/.test(val)) {
    if (n >= 0.4 && n <= 1.6) return "ev-good";
    if (n >= 0.3 && n <= 1.8) return "ev-warn";
    return "ev-bad";
  }
  // Stability percentage — higher is better
  if (/stability/.test(key)) {
    if (n >= 80) return "ev-good";
    if (n >= 50) return "ev-warn";
    return "ev-bad";
  }
  return "";
}

// ── Inference sparkline ──────────────────────────────────────────────────────
// loadSparkline() is defined in sparkline.js (loaded as a plain script before
// this module so it is available as a global on both the dashboard and history
// pages).

function _openInferenceDialog(id) {
  const inf = _inferences.find(i => i.id === id);
  if (!inf) return;
  const dialog = document.getElementById("inferenceDialog");

  document.getElementById("infTitle").textContent = inf.title;

  const badge = document.getElementById("infSeverity");
  badge.textContent = SEVERITY_LABEL[inf.severity] || inf.severity;
  badge.className = `inf-badge ${SEVERITY_CLS[inf.severity] || ""}`;

  // Detection method chip
  const metaEl = document.getElementById("infMeta");
  if (metaEl) {
    const chipEl = metaEl.querySelector(".inf-detection-chip");
    if (chipEl) {
      chipEl.innerHTML = renderDetectionChip(inf.detection_method || 'rule');
    }
  }

  document.getElementById("infTime").textContent =
    new Date(inf.created_at).toLocaleString();
  document.getElementById("infConfidence").textContent =
    `${Math.round(inf.confidence * 100)}% confidence`;

  document.getElementById("infDescription").textContent = inf.description;

  // Attribution badge
  const attrEl = document.getElementById("infAttribution");
  if (attrEl) {
    attrEl.innerHTML = renderAttributionBadge(inf);
    attrEl.style.display = attrEl.innerHTML ? "" : "none";
  }

  document.getElementById("infAction").textContent = inf.action || "No specific action needed.";

  function _renderFeatureVectorEvidence(featureVector) {
    const mapping = [
      ['tvoc_current', 'TVOC', 'ppb'],
      ['tvoc_baseline', 'TVOC baseline', 'ppb'],
      ['tvoc_slope_1m', 'TVOC slope 1m', 'ppb/min'],
      ['tvoc_slope_5m', 'TVOC slope 5m', 'ppb/min'],
      ['tvoc_slope_30m', 'TVOC slope 30m', 'ppb/min'],
      ['tvoc_elevated_minutes', 'TVOC elevated minutes', 'min'],
      ['tvoc_peak_ratio', 'TVOC peak ratio', '×'],
      ['tvoc_is_declining', 'TVOC declining', ''],
      ['tvoc_decay_rate', 'TVOC decay rate', 'ppb/min'],
      ['tvoc_pulse_detected', 'TVOC pulse detected', ''],
      ['eco2_current', 'eCO₂', 'ppm'],
      ['eco2_baseline', 'eCO₂ baseline', 'ppm'],
      ['eco2_slope_1m', 'eCO₂ slope 1m', 'ppm/min'],
      ['eco2_slope_5m', 'eCO₂ slope 5m', 'ppm/min'],
      ['eco2_slope_30m', 'eCO₂ slope 30m', 'ppm/min'],
      ['eco2_elevated_minutes', 'eCO₂ elevated minutes', 'min'],
      ['eco2_peak_ratio', 'eCO₂ peak ratio', '×'],
      ['eco2_is_declining', 'eCO₂ declining', ''],
      ['eco2_decay_rate', 'eCO₂ decay rate', 'ppm/min'],
      ['eco2_pulse_detected', 'eCO₂ pulse detected', ''],
      ['temperature_current', 'Temperature', '°C'],
      ['temperature_baseline', 'Temperature baseline', '°C'],
      ['temperature_slope_1m', 'Temperature slope 1m', '°C/min'],
      ['temperature_slope_5m', 'Temperature slope 5m', '°C/min'],
      ['temperature_slope_30m', 'Temperature slope 30m', '°C/min'],
      ['temperature_elevated_minutes', 'Temperature elevated minutes', 'min'],
      ['temperature_peak_ratio', 'Temperature peak ratio', '×'],
      ['temperature_is_declining', 'Temperature declining', ''],
      ['temperature_decay_rate', 'Temperature decay rate', '°C/min'],
      ['temperature_pulse_detected', 'Temperature pulse detected', ''],
      ['humidity_current', 'Humidity', '%'],
      ['humidity_baseline', 'Humidity baseline', '%'],
      ['humidity_slope_1m', 'Humidity slope 1m', '%/min'],
      ['humidity_slope_5m', 'Humidity slope 5m', '%/min'],
      ['humidity_slope_30m', 'Humidity slope 30m', '%/min'],
      ['humidity_elevated_minutes', 'Humidity elevated minutes', 'min'],
      ['humidity_peak_ratio', 'Humidity peak ratio', '×'],
      ['humidity_is_declining', 'Humidity declining', ''],
      ['humidity_decay_rate', 'Humidity decay rate', '%/min'],
      ['humidity_pulse_detected', 'Humidity pulse detected', ''],
      ['pm1_current', 'PM1', 'µg/m³'],
      ['pm1_baseline', 'PM1 baseline', 'µg/m³'],
      ['pm1_slope_1m', 'PM1 slope 1m', 'µg/m³/min'],
      ['pm1_slope_5m', 'PM1 slope 5m', 'µg/m³/min'],
      ['pm1_slope_30m', 'PM1 slope 30m', 'µg/m³/min'],
      ['pm1_elevated_minutes', 'PM1 elevated minutes', 'min'],
      ['pm1_peak_ratio', 'PM1 peak ratio', '×'],
      ['pm1_is_declining', 'PM1 declining', ''],
      ['pm1_decay_rate', 'PM1 decay rate', 'µg/m³/min'],
      ['pm1_pulse_detected', 'PM1 pulse detected', ''],
      ['pm25_current', 'PM2.5', 'µg/m³'],
      ['pm25_baseline', 'PM2.5 baseline', 'µg/m³'],
      ['pm25_slope_1m', 'PM2.5 slope 1m', 'µg/m³/min'],
      ['pm25_slope_5m', 'PM2.5 slope 5m', 'µg/m³/min'],
      ['pm25_slope_30m', 'PM2.5 slope 30m', 'µg/m³/min'],
      ['pm25_elevated_minutes', 'PM2.5 elevated minutes', 'min'],
      ['pm25_peak_ratio', 'PM2.5 peak ratio', '×'],
      ['pm25_is_declining', 'PM2.5 declining', ''],
      ['pm25_decay_rate', 'PM2.5 decay rate', 'µg/m³/min'],
      ['pm25_pulse_detected', 'PM2.5 pulse detected', ''],
      ['pm10_current', 'PM10', 'µg/m³'],
      ['pm10_baseline', 'PM10 baseline', 'µg/m³'],
      ['pm10_slope_1m', 'PM10 slope 1m', 'µg/m³/min'],
      ['pm10_slope_5m', 'PM10 slope 5m', 'µg/m³/min'],
      ['pm10_slope_30m', 'PM10 slope 30m', 'µg/m³/min'],
      ['pm10_elevated_minutes', 'PM10 elevated minutes', 'min'],
      ['pm10_peak_ratio', 'PM10 peak ratio', '×'],
      ['pm10_is_declining', 'PM10 declining', ''],
      ['pm10_decay_rate', 'PM10 decay rate', 'µg/m³/min'],
      ['pm10_pulse_detected', 'PM10 pulse detected', ''],
      ['co_current', 'CO (resistance)', 'Ω'],
      ['co_baseline', 'CO baseline', 'Ω'],
      ['co_slope_1m', 'CO slope 1m', 'Ω/min'],
      ['co_slope_5m', 'CO slope 5m', 'Ω/min'],
      ['co_slope_30m', 'CO slope 30m', 'Ω/min'],
      ['co_elevated_minutes', 'CO elevated minutes', 'min'],
      ['co_peak_ratio', 'CO peak ratio', '×'],
      ['co_is_declining', 'CO declining', ''],
      ['co_decay_rate', 'CO decay rate', 'Ω/min'],
      ['co_pulse_detected', 'CO pulse detected', ''],
      ['no2_current', 'NO₂ (resistance)', 'Ω'],
      ['no2_baseline', 'NO₂ baseline', 'Ω'],
      ['no2_slope_1m', 'NO₂ slope 1m', 'Ω/min'],
      ['no2_slope_5m', 'NO₂ slope 5m', 'Ω/min'],
      ['no2_slope_30m', 'NO₂ slope 30m', 'Ω/min'],
      ['no2_elevated_minutes', 'NO₂ elevated minutes', 'min'],
      ['no2_peak_ratio', 'NO₂ peak ratio', '×'],
      ['no2_is_declining', 'NO₂ declining', ''],
      ['no2_decay_rate', 'NO₂ decay rate', 'Ω/min'],
      ['no2_pulse_detected', 'NO₂ pulse detected', ''],
      ['nh3_current', 'NH₃ (resistance)', 'Ω'],
      ['nh3_baseline', 'NH₃ baseline', 'Ω'],
      ['nh3_slope_1m', 'NH₃ slope 1m', 'Ω/min'],
      ['nh3_slope_5m', 'NH₃ slope 5m', 'Ω/min'],
      ['nh3_slope_30m', 'NH₃ slope 30m', 'Ω/min'],
      ['nh3_elevated_minutes', 'NH₃ elevated minutes', 'min'],
      ['nh3_peak_ratio', 'NH₃ peak ratio', '×'],
      ['nh3_is_declining', 'NH₃ declining', ''],
      ['nh3_decay_rate', 'NH₃ decay rate', 'Ω/min'],
      ['nh3_pulse_detected', 'NH₃ pulse detected', ''],
      ['nh3_lag_behind_tvoc_seconds', 'NH₃ lag behind TVOC', 's'],
      ['pm25_correlated_with_tvoc', 'PM2.5 correlated with TVOC', ''],
      ['co_correlated_with_tvoc', 'CO correlated with TVOC', ''],
      ['vpd_kpa', 'VPD', 'kPa'],
    ];

    const rows = mapping.map(([key, label, unit]) => {
      const value = featureVector[key];
      if (value == null) return null;
      const formatted = typeof value === 'boolean' ? (value ? 'yes' : 'no') : value;
      const suffix = unit ? ` ${unit}` : '';
      return `<div class="inf-ev-row"><span class="fd-label">${label}</span><span class="fd-value">${formatted}${suffix}</span></div>`;
    }).filter(Boolean);

    return rows.join('');
  }

  function _renderRangeReadingsEvidence(evidence) {
    const readings = Array.isArray(evidence.readings) ? evidence.readings : [];
    if (!readings.length) return '';

    const latest = readings[readings.length - 1];
    const mapping = [
      ['tvoc_ppb', 'TVOC', 'ppb', 'tvoc_baseline'],
      ['eco2_ppm', 'eCO₂', 'ppm', 'eco2_baseline'],
      ['temperature_c', 'Temperature', '°C', 'temperature_baseline'],
      ['humidity_pct', 'Humidity', '%', 'humidity_baseline'],
      ['pm1_ug_m3', 'PM1', 'µg/m³', 'pm1_baseline'],
      ['pm25_ug_m3', 'PM2.5', 'µg/m³', 'pm25_baseline'],
      ['pm10_ug_m3', 'PM10', 'µg/m³', 'pm10_baseline'],
      ['co_ppb', 'CO (resistance)', 'Ω', 'co_baseline'],
      ['no2_ppb', 'NO₂ (resistance)', 'Ω', 'no2_baseline'],
      ['nh3_ppb', 'NH₃ (resistance)', 'Ω', 'nh3_baseline'],
    ];

    const summary = `<div class="inf-ev-row"><span class="fd-label">Selected range</span><span class="fd-value">${readings.length} readings from ${new Date(readings[0].timestamp).toLocaleString()} to ${new Date(latest.timestamp).toLocaleString()}</span></div>`;
    const rows = mapping.map(([key, label, unit, baselineKey]) => {
      const value = latest[key];
      if (value == null) return null;
      const baseline = evidence.feature_vector ? evidence.feature_vector[baselineKey] : null;
      const status = baseline != null ? (value > baseline ? 'above baseline' : value < baseline ? 'below baseline' : 'at baseline') : '';
      const statusText = status ? ` (${status})` : '';
      const baselineText = baseline != null ? ` / baseline ${baseline} ${unit}` : '';
      return `<div class="inf-ev-row"><span class="fd-label">${label}</span><span class="fd-value">${value} ${unit}${baselineText}${statusText}</span></div>`;
    }).filter(Boolean);

    return summary + rows.join('');
  }

  // Evidence section
  const evEl = document.getElementById("infEvidence");
  const thSec = document.getElementById("infThresholdsSection");
  const thGrid = document.getElementById("infThresholds");

  if (inf.evidence && typeof inf.evidence === "object") {
    const snapshot = inf.evidence.sensor_snapshot;
    const thresholds = inf.evidence._thresholds;

    if (Array.isArray(snapshot) && snapshot.length > 0) {
      // Structured sensor snapshot — render chips (all data pre-computed by backend)
      const TREND_ARROW = { rising: "↑", falling: "↓", stable: "→" };
      const BAND_CLS    = { high: "ev-bad", elevated: "ev-warn", normal: "ev-good", unknown: "" };

      evEl.innerHTML = snapshot.map(s => {
        const arrow = TREND_ARROW[s.trend] || "→";
        const cls   = BAND_CLS[s.ratio_band] || "";
        const ratioDesc = s.ratio_description || (s.ratio != null ? `${s.ratio.toFixed(1)}× normal` : '');
        const infoIcon = ratioDesc ? `<span class="ev-info" title="${ratioDesc}" style="cursor:help;opacity:0.6;font-size:0.8em;margin-left:3px;">ⓘ</span>` : '';
        const ratio = s.ratio != null ? `<span class="ev-ratio">${s.ratio}× normal</span>${infoIcon}` : "";
        return `<div class="inf-ev-row ${cls}">
          <span class="fd-label">${s.label}</span>
          <span class="fd-value">${s.value} ${s.unit} <span class="ev-trend">${arrow}</span></span>
          ${ratio}
        </div>`;
      }).join("");
    } else if (inf.evidence.readings && Array.isArray(inf.evidence.readings) && inf.evidence.readings.length > 0) {
      evEl.innerHTML = _renderRangeReadingsEvidence(inf.evidence);
      if (inf.evidence.feature_vector && typeof inf.evidence.feature_vector === 'object') {
        evEl.innerHTML += '<div class="inf-ev-subtitle">Feature vector</div>' +
          _renderFeatureVectorEvidence(inf.evidence.feature_vector);
      }
    } else if (inf.evidence.feature_vector && typeof inf.evidence.feature_vector === 'object') {
      const featureHtml = _renderFeatureVectorEvidence(inf.evidence.feature_vector);
      evEl.innerHTML = featureHtml || 'No detailed evidence available.';
    } else {
      // Fallback: generic key-value pairs (existing behaviour for older inferences)
      const entries = Object.entries(inf.evidence).filter(
        ([k]) => k !== "_thresholds" && k !== "sensor_snapshot" && k !== "model_id"
      );
      evEl.innerHTML = entries.map(([k, v]) => {
        const cls = _evidenceColor(k, v);
        return `<div class="inf-ev-row ${cls}"><span class="fd-label">${k.replace(/_/g, " ")}</span><span class="fd-value">${v}</span></div>`;
      }).join("") || "No detailed evidence available.";
    }

    // Thresholds section (unchanged)
    if (thresholds && typeof thresholds === "object" && Object.keys(thresholds).length) {
      thSec.style.display = "";
      thSec.removeAttribute("open");
      thGrid.innerHTML = Object.entries(thresholds).map(([k, th]) => {
        const customTag = th.is_custom
          ? '<span class="inf-th-custom">custom</span>'
          : '<span class="inf-th-default">default</span>';
        return `<div class="inf-th-row">
          <span class="inf-th-label">${th.label || k.replace(/_/g, " ")}</span>
          <span class="inf-th-val">${th.value} ${th.unit || ""} ${customTag}</span>
        </div>`;
      }).join("");
    } else {
      thSec.style.display = "none";
    }
  } else {
    evEl.textContent = "No detailed evidence available.";
    thSec.style.display = "none";
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

  // Sparkline — guard in case sparkline.js failed to load
  const sparkline = document.getElementById('infSparkline');
  if (sparkline) sparkline.style.display = 'none';
  if (typeof loadSparkline === 'function') {
    loadSparkline(inf.id, inf.created_at);
  }

  dialog.showModal();
  // Resize the sparkline chart after the dialog is visible so Plotly measures
  // the correct dimensions (it renders before the dialog is fully painted).
  setTimeout(() => {
    const chartDiv = document.getElementById('infSparklineChart');
    if (chartDiv && window.Plotly) Plotly.Plots.resize(chartDiv);
  }, 50);
  dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
}

// ── Server-Sent Events (real-time push) ─────────────────────────────────────

let _evtSource = null;
let _sseRetryMs = 1000;

function connectSSE() {
  if (_evtSource) _evtSource.close();
  _evtSource = new EventSource("/api/stream");

  _evtSource.addEventListener("sensor_update", (e) => {
    const d = JSON.parse(e.data);
    const t = d.temperature;
    const h = d.humidity;
    document.getElementById("tempValue").textContent =
      `${trend(t, _lastIndoorTemp)}${t?.toFixed(1) ?? "--"} °C`;
    document.getElementById("humValue").textContent =
      `${trend(h, _lastIndoorHum)}${h?.toFixed(1) ?? "--"} %`;
    document.getElementById("eco2Value").textContent =
      `${trend(d.eco2, null)}${d.eco2 ?? "--"} ppm`;
    document.getElementById("tvocValue").textContent =
      `${trend(d.tvoc, null)}${d.tvoc ?? "--"} ppb`;
    _updatePmCard(d.pm2_5, null, d.pm1_0 ?? null, d.pm10 ?? null, d.pm_stale, d.pm_timestamp);
    if (d.gas_co  != null) { _gasCoHistory.push(d.gas_co);   if (_gasCoHistory.length  > 30) _gasCoHistory.shift(); }
    if (d.gas_no2 != null) { _gasNo2History.push(d.gas_no2); if (_gasNo2History.length > 30) _gasNo2History.shift(); }
    if (d.gas_nh3 != null) { _gasNh3History.push(d.gas_nh3); if (_gasNh3History.length > 30) _gasNh3History.shift(); }
    _updateGasCard();
    _lastIndoorTemp = t;
    _lastIndoorHum  = h;
    updateInsights(t, h, d.tvoc, d.eco2, [d.eco2]);
    document.getElementById("last-updated").textContent =
      "Last updated: " + new Date().toLocaleString();
  });

  _evtSource.addEventListener("inference_fired", () => {
    fetchInferences();
  });

  _evtSource.addEventListener("weather_update", (e) => {
    const w = JSON.parse(e.data);
    updateWeather(w, _lastIndoorTemp, _lastIndoorHum);
  });

  _evtSource.addEventListener("forecast_update", (e) => {
    const d = JSON.parse(e.data);
    if (d.hours) updateForecast(d.hours);
  });

  _evtSource.addEventListener("daily_forecast_update", (e) => {
    const d = JSON.parse(e.data);
    if (d.days) updateDailyForecast(d.days);
  });

  _evtSource.addEventListener("health_update", (e) => {
    applyHealth(JSON.parse(e.data));
  });

  // fan_status doesn't carry full health data — the next health_update
  // (arriving within LOG_INTERVAL seconds) will refresh the health panel.

  _evtSource.onopen = () => { _sseRetryMs = 1000; };

  _evtSource.onerror = () => {
    _evtSource.close();
    // Exponential back-off reconnect (max 30 s)
    setTimeout(connectSSE, _sseRetryMs);
    _sseRetryMs = Math.min(_sseRetryMs * 2, 30000);
  };
}

// ── Initial data load then hand off to SSE for all subsequent updates ────────

fetchData();
fetchHealth();
fetchWeather();
fetchForecast();
fetchDailyForecast();
fetchInferences();
connectSSE();

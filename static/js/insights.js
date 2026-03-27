// ── Derived atmospheric calculations ─────────────────────────────────────────

export function dewPoint(tempC, rh) {
  // Magnus formula
  const a = 17.625, b = 243.04;
  const alpha = Math.log(rh / 100) + a * tempC / (b + tempC);
  return (b * alpha) / (a - alpha);
}

export function feelsLike(tempC, rh) {
  // Australian BOM apparent temperature (indoors, no wind)
  const e = (rh / 100) * 6.105 * Math.exp(17.27 * tempC / (237.7 + tempC));
  return tempC + 0.33 * e - 4.00;
}

export function vpdKpa(tempC, rh) {
  // Tetens formula for saturation vapour pressure (kPa)
  const svp = 0.6108 * Math.exp(17.27 * tempC / (tempC + 237.3));
  return svp * (1 - rh / 100);
}

export function timeToThreshold(values, thresholdPpm, intervalSecs) {
  // Linear extrapolation from last 6 readings
  const n = Math.min(values.length, 6);
  if (n < 2) return null;
  const recent = values.slice(-n);
  const rate = (recent[recent.length - 1] - recent[0]) / (n - 1); // ppm per interval
  if (rate <= 0) return null; // not rising
  const current = recent[recent.length - 1];
  if (current >= thresholdPpm) return 0;
  const intervalsLeft = (thresholdPpm - current) / rate;
  return Math.round(intervalsLeft * intervalSecs / 60); // minutes
}

export function airQuality(tvoc, eco2) {
  if (tvoc <= 250 && eco2 <= 800)  return { label: "Good",     cls: "good",     border: "#2d8a2d" };
  if (tvoc <= 500 && eco2 <= 1500) return { label: "Moderate", cls: "moderate", border: "#c87800" };
  return                                  { label: "Poor",     cls: "poor",     border: "#b03030" };
}

export function co2Alert(eco2) {
  if (eco2 <= 800)  return { label: "Normal",    cls: "good",     sub: "Safe for cognitive function" };
  if (eco2 <= 1000) return { label: "Elevated",  cls: "moderate", sub: "Monitor closely" };
  if (eco2 <= 2000) return { label: "Impaired",  cls: "poor",     sub: "Cognitive impact likely" };
  return                   { label: "Dangerous", cls: "danger",   sub: "Evacuate or ventilate now" };
}

// ── WMO helpers ───────────────────────────────────────────────────────────────
const WMO_EMOJI = {
  0: "☀️",  1: "🌤️", 2: "⛅",  3: "☁️",
  45: "🌫️", 48: "🌫️",
  51: "🌦️", 53: "🌦️", 55: "🌧️",
  61: "🌧️", 63: "🌧️", 65: "🌧️",
  71: "❄️",  73: "❄️",  75: "❄️",
  77: "🌨️",
  80: "🌦️", 81: "🌦️", 82: "⛈️",
  85: "❄️",  86: "❄️",
  95: "⛈️", 96: "⛈️", 99: "⛈️",
};

// ── WMO weather code descriptions ─────────────────────────────────────────────
const WMO = {
  0:"Clear sky", 1:"Mainly clear", 2:"Partly cloudy", 3:"Overcast",
  45:"Fog", 48:"Icy fog",
  51:"Light drizzle", 53:"Drizzle", 55:"Heavy drizzle",
  61:"Light rain", 63:"Rain", 65:"Heavy rain",
  71:"Light snow", 73:"Snow", 75:"Heavy snow",
  80:"Rain showers", 81:"Showers", 82:"Heavy showers",
  95:"Thunderstorm", 96:"Thunderstorm + hail", 99:"Thunderstorm + heavy hail",
};

export function updateWeather(w, indoorTemp, indoorHum) {
  const sec = document.getElementById("weatherSection");
  if (!w || w.error) { if (sec) sec.style.display = "none"; return; }
  sec.style.display = "";

  document.getElementById("weatherLoc").textContent = w.location || "";
  document.getElementById("weatherUpdated").textContent = "· " + new Date().toLocaleTimeString();

  // Outdoor temp
  const tEl = document.getElementById("outTemp");
  tEl.textContent = w.temp != null ? `${w.temp.toFixed(1)} °C` : "--";
  if (indoorTemp != null && w.temp != null) {
    const d = (indoorTemp - w.temp).toFixed(1);
    document.getElementById("outTempDelta").textContent =
      d > 0 ? `${d}°C warmer indoors` : d < 0 ? `${Math.abs(d)}°C cooler indoors` : "Same as indoors";
  }

  // Outdoor humidity
  const hEl = document.getElementById("outHum");
  hEl.textContent = w.humidity != null ? `${w.humidity} %` : "--";
  if (indoorHum != null && w.humidity != null) {
    const d = (indoorHum - w.humidity).toFixed(0);
    document.getElementById("outHumDelta").textContent =
      d > 0 ? `${d}% more humid indoors` : d < 0 ? `${Math.abs(d)}% less humid indoors` : "Same as indoors";
  }

  // UV Index
  const uvEl = document.getElementById("uvIdx");
  const uv = w.uv_index ?? null;
  uvEl.textContent = uv != null ? uv.toFixed(1) : "--";
  uvEl.className = "value " + (uv == null ? "neutral" : uv < 3 ? "good" : uv < 6 ? "moderate" : uv < 8 ? "poor" : "danger");
  document.getElementById("uvSub").textContent =
    uv == null ? "--" : uv < 3 ? "Low" : uv < 6 ? "Moderate" : uv < 8 ? "High" : uv < 11 ? "Very high" : "Extreme";

  // Wind speed + weather condition
  document.getElementById("windSpd").textContent = w.wind_speed != null ? `${w.wind_speed.toFixed(1)} mph` : "--";
  document.getElementById("weatherCond").textContent = WMO[w.weather_code] ?? `Code ${w.weather_code}`;

  // Ventilation opportunity
  const ventEl = document.getElementById("ventVal");
  const ventSub = document.getElementById("ventSub");
  const ventCard = document.getElementById("ventCard");
  if (indoorTemp != null && indoorHum != null && w.temp != null && w.humidity != null) {
    const cooler = w.temp < indoorTemp - 1;
    const drier  = w.humidity < indoorHum - 5;
    if (cooler && drier)  { ventEl.textContent = "Good"; ventEl.className = "value good";     ventSub.textContent = "Cooler & drier outside — ventilate"; ventCard.style.borderTopColor = "#2d8a2d"; }
    else if (cooler)      { ventEl.textContent = "Partial"; ventEl.className = "value moderate"; ventSub.textContent = "Cooler outside but similar humidity"; ventCard.style.borderTopColor = "#c87800"; }
    else if (drier)       { ventEl.textContent = "Partial"; ventEl.className = "value moderate"; ventSub.textContent = "Drier outside but similar temperature"; ventCard.style.borderTopColor = "#c87800"; }
    else                  { ventEl.textContent = "Poor"; ventEl.className = "value neutral";   ventSub.textContent = "Outside conditions not favourable"; ventCard.style.borderTopColor = "#555"; }
  } else {
    ventEl.textContent = "--"; ventEl.className = "value neutral"; ventSub.textContent = "Awaiting data";
  }
}

// ── Forecast strip ─────────────────────────────────────────────────────────────
let _forecastData = [];   // retained so dialog can look up any slot

export function updateForecast(hours) {
  const strip = document.getElementById("forecastStrip");
  if (!strip) return;
  if (!hours || !hours.length) { strip.innerHTML = ""; return; }

  _forecastData = hours;
  strip.innerHTML = hours.map((h, idx) => {
    const icon = WMO_EMOJI[h.weather_code] ?? "🌡️";
    const temp = h.temp != null ? `${Math.round(h.temp)}°` : "--";
    const rain = h.precip_prob != null
      ? `<div class="fc-rain">💧 ${h.precip_prob}%</div>`
      : "";
    return `
      <button class="forecast-slot" data-idx="${idx}"
              aria-label="Forecast at ${h.time}" title="Tap for details">
        <div class="fc-time">${h.time}</div>
        <div class="fc-icon">${icon}</div>
        <div class="fc-temp">${temp}</div>
        ${rain}
      </button>`;
  }).join("");

  // Single delegated click listener on the strip
  strip.onclick = (e) => {
    const slot = e.target.closest(".forecast-slot");
    if (!slot) return;
    _openForecastDialog(parseInt(slot.dataset.idx, 10));
  };
}

function _openForecastDialog(idx) {
  const h      = _forecastData[idx];
  const dialog = document.getElementById("forecastDialog");
  if (!h || !dialog) return;

  const icon = WMO_EMOJI[h.weather_code] ?? "🌡️";
  const cond = WMO[h.weather_code]       ?? `Code ${h.weather_code}`;

  document.getElementById("fdIcon").textContent  = icon;
  document.getElementById("fdTitle").textContent = `${h.time} — ${cond}`;
  document.getElementById("fdTemp").textContent  =
    h.temp != null ? `${h.temp.toFixed(1)} °C` : "--";
  document.getElementById("fdRain").textContent  =
    h.precip_prob != null ? `${h.precip_prob}%` : "--";
  document.getElementById("fdWindSpd").textContent =
    h.wind_speed != null ? `${h.wind_speed.toFixed(1)} mph` : "--";
  // Feels-like not in hourly data — show wind impact label instead
  document.getElementById("fdWind").textContent  =
    h.wind_speed != null
      ? (h.wind_speed < 5 ? "Calm" : h.wind_speed < 15 ? "Breezy" : h.wind_speed < 25 ? "Windy" : "Very windy")
      : "--";

  dialog.showModal();

  // Close when clicking outside the dialog box
  dialog.onclick = (e) => {
    if (e.target === dialog) dialog.close();
  };
}

export function updateInsights(temp, hum, tvoc, eco2, eco2Series) {
  // Air quality
  const aq = airQuality(tvoc, eco2);
  const aqEl = document.getElementById("aqValue");
  aqEl.textContent = aq.label;
  aqEl.className = `value ${aq.cls}`;
  document.getElementById("aqCard").style.borderTopColor = aq.border;
  document.getElementById("aqSub").textContent = `TVOC ${tvoc} ppb · eCO₂ ${eco2} ppm`;

  // Dew point
  const dp = dewPoint(temp, hum);
  document.getElementById("dewValue").textContent = `${dp.toFixed(1)} °C`;
  const dpDiff = temp - dp;
  document.getElementById("dewSub").textContent =
    dpDiff < 3 ? "⚠️ Condensation risk" : `${dpDiff.toFixed(1)} °C margin to condensation`;

  // Feels like
  const fl = feelsLike(temp, hum);
  document.getElementById("hiValue").textContent = `${fl.toFixed(1)} °C`;

  // CO2 alert
  const co2 = co2Alert(eco2);
  const co2El = document.getElementById("co2AlertValue");
  co2El.textContent = co2.label;
  co2El.className = `value ${co2.cls}`;
  document.getElementById("co2Card").style.borderTopColor =
    co2.cls === "good" ? "#2d8a2d" : co2.cls === "moderate" ? "#c87800" : "#b03030";
  document.getElementById("co2AlertSub").textContent = co2.sub;

  // VPD
  const vpd = vpdKpa(temp, hum);
  const vpdEl = document.getElementById("vpdValue");
  vpdEl.textContent = `${vpd.toFixed(2)} kPa`;
  let vpdCls = "neutral", vpdSub = "";
  if      (vpd < 0.4)  { vpdCls = "moderate"; vpdSub = "Too humid — mould risk"; }
  else if (vpd <= 0.8) { vpdCls = "good";     vpdSub = "Good — seedlings/clones"; }
  else if (vpd <= 1.2) { vpdCls = "good";     vpdSub = "Ideal — vegetative growth"; }
  else if (vpd <= 1.6) { vpdCls = "moderate"; vpdSub = "High — increase humidity"; }
  else                 { vpdCls = "poor";     vpdSub = "Too dry — plant stress"; }
  vpdEl.className = `value ${vpdCls}`;
  document.getElementById("vpdSub").textContent = vpdSub;

  // Time to CO₂ threshold (1000 ppm = cognitive impairment level)
  const LOG_INTERVAL_S = 10;
  const mins = timeToThreshold(eco2Series, 1000, LOG_INTERVAL_S);
  const tttEl = document.getElementById("tttValue");
  if (mins === null)  { tttEl.textContent = "Stable";    tttEl.className = "value good"; }
  else if (mins === 0){ tttEl.textContent = "Now";       tttEl.className = "value danger"; }
  else if (mins < 10) { tttEl.textContent = `~${mins} min`; tttEl.className = "value poor"; }
  else if (mins < 30) { tttEl.textContent = `~${mins} min`; tttEl.className = "value moderate"; }
  else                { tttEl.textContent = `~${mins} min`; tttEl.className = "value neutral"; }
}

// ── Derived atmospheric calculations ─────────────────────────────────────────

let _lastWeather = null;

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
  _lastWeather = w;  // Store for use in updateInsights
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

  // Indoor pressure (from sensor_update via dashboard.js)
  // Outdoor pressure and AQI will be handled in updateInsights

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
  document.getElementById("fdHum").textContent =
    h.humidity != null ? `${h.humidity}%` : "--";
  document.getElementById("fdCloud").textContent =
    h.cloud_cover != null ? `${h.cloud_cover}%` : "--";

  dialog.showModal();

  // Close when clicking outside the dialog box
  dialog.onclick = (e) => {
    if (e.target === dialog) dialog.close();
  };
}

// ── Daily forecast strip ────────────────────────────────────────────────────
let _dailyForecastData = [];

export function updateDailyForecast(days) {
  const strip = document.getElementById("forecastDailyStrip");
  if (!strip) return;
  if (!days || !days.length) { strip.innerHTML = ""; return; }

  _dailyForecastData = days;
  const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  strip.innerHTML = days.map((d, idx) => {
    const date    = new Date(d.date + "T12:00:00");
    const dayName = idx === 0 ? "Today" : DAY_NAMES[date.getDay()];
    const icon    = WMO_EMOJI[d.weather_code] ?? "🌡️";
    const hi      = d.temp_max != null ? `${Math.round(d.temp_max)}°` : "--";
    const lo      = d.temp_min != null ? `${Math.round(d.temp_min)}°` : "--";
    return `
      <button class="forecast-slot forecast-day-slot" data-didx="${idx}"
              aria-label="Forecast for ${dayName}" title="Tap for details">
        <div class="fc-time">${dayName}</div>
        <div class="fc-icon">${icon}</div>
        <div class="fc-temp">${hi}<span class="fc-lo"> / ${lo}</span></div>
        ${d.precip_prob != null ? `<div class="fc-rain">💧 ${d.precip_prob}%</div>` : ""}
      </button>`;
  }).join("");

  strip.onclick = (e) => {
    const slot = e.target.closest(".forecast-day-slot");
    if (!slot) return;
    _openDailyDialog(parseInt(slot.dataset.didx, 10));
  };
}

function _openDailyDialog(idx) {
  const d      = _dailyForecastData[idx];
  const dialog = document.getElementById("forecastDailyDialog");
  if (!d || !dialog) return;

  const FULL_DAYS = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  const date     = new Date(d.date + "T12:00:00");
  const dayLabel = FULL_DAYS[date.getDay()] + " " +
    date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const icon = WMO_EMOJI[d.weather_code] ?? "🌡️";
  const cond = WMO[d.weather_code] ?? `Code ${d.weather_code}`;

  document.getElementById("fddIcon").textContent  = icon;
  document.getElementById("fddTitle").textContent = `${dayLabel} — ${cond}`;
  document.getElementById("fddHigh").textContent  =
    d.temp_max != null ? `${d.temp_max.toFixed(1)} °C` : "--";
  document.getElementById("fddLow").textContent   =
    d.temp_min != null ? `${d.temp_min.toFixed(1)} °C` : "--";
  document.getElementById("fddRain").textContent  =
    d.precip_prob != null ? `${d.precip_prob}%` : "--";
  document.getElementById("fddWind").textContent  =
    d.wind_speed != null ? `${d.wind_speed.toFixed(1)} mph` : "--";
  document.getElementById("fddPrecip").textContent =
    d.precip_sum != null ? `${d.precip_sum.toFixed(1)} mm` : "--";
  document.getElementById("fddUV").textContent =
    d.uv_index != null ? `${d.uv_index.toFixed(1)}` : "--";
  document.getElementById("fddSunrise").textContent = d.sunrise ?? "--";
  document.getElementById("fddSunset").textContent  = d.sunset ?? "--";

  dialog.showModal();
  dialog.onclick = (e) => { if (e.target === dialog) dialog.close(); };
}

export function updateInsights(temp, hum, tvoc, eco2, eco2Series, pressureHistory = []) {
  // Use stored weather data from updateWeather
  const w = _lastWeather;
  if (!w) return;

  // ... existing code ...

  // Ventilation opportunity with pressure-enhanced assessment
  const ventEl = document.getElementById("ventVal");
  const ventSub = document.getElementById("ventSub");
  const ventCard = document.getElementById("ventCard");
  const ventPressure = document.getElementById("ventPressure");

  if (indoorTemp != null && indoorHum != null && w.temp != null && w.humidity != null) {
    const cooler = w.temp < indoorTemp - 1;
    const drier  = w.humidity < indoorHum - 5;

    // Air Quality check - bad AQI means don't open windows
    const aqiBad = w.aqi != null && w.aqi > 100; // Unhealthy for sensitive groups
    const aqiVeryBad = w.aqi != null && w.aqi > 150; // Unhealthy for everyone

    // Pressure differential: higher inside = air wants to move out
    const indoorPressure = window._indoorPressure;
    const outdoorPressure = w.pressure_hpa;
    let pressureDiff = 0;
    let pressureSignal = "";
    if (indoorPressure != null && outdoorPressure != null) {
      pressureDiff = indoorPressure - outdoorPressure;
      if (pressureDiff > 2) {
        pressureSignal = ` (${pressureDiff.toFixed(1)} hPa higher inside — natural exhaust)`;
      } else if (pressureDiff < -2) {
        pressureSignal = ` (${Math.abs(pressureDiff).toFixed(1)} hPa lower inside — air wants in)`;
      }
    }

    // Indoor pressure trend analysis
    let pressureTrendSignal = "";
    let pressureBoost = false;
    if (pressureHistory.length >= 5 && indoorPressure != null) {
      const recent = pressureHistory.slice(-5);
      const trend = recent[recent.length - 1] - recent[0];
      const dropRate = trend / 5;
      if (dropRate < -0.5) {
        pressureTrendSignal = " (pressure dropping — likely open window)";
        pressureBoost = true;
      } else if (dropRate < -0.2) {
        pressureTrendSignal = " (slight pressure drop)";
      } else if (dropRate > 0.5) {
        pressureTrendSignal = " (pressure rising — doors/window likely closed)";
      }
    }

    // Display pressure info
    if (indoorPressure != null && outdoorPressure != null) {
      const trend = pressureHistory.length >= 3
        ? (pressureHistory.slice(-3).reduce((a, b) => a + b, 0) / 3 - pressureHistory[0]).toFixed(1)
        : null;
      const trendStr = trend !== null
        ? ` (${trend > 0 ? '+' : ''}${trend} hPa/30s)`
        : "";
      const aqiStr = w.aqi != null ? ` • AQI: ${w.aqi}` : "";
      ventPressure.textContent = `In: ${indoorPressure.toFixed(1)} hPa${trendStr} • Out: ${outdoorPressure.toFixed(1)} hPa${aqiStr}`;
    }

    // Calculate ventilation rating
    let rating, reason;

    // If AQI is very bad, force Poor regardless of other factors
    if (aqiVeryBad) {
      rating = "Poor";
      reason = `Outdoor AQI ${w.aqi} (unhealthy) — keep windows closed`;
    } else if (aqiBad) {
      // AQI is moderate/unhealthy for sensitive - reduce rating
      if (cooler && drier) {
        rating = "Partial"; reason = "Cooler & drier but outdoor AQI elevated";
      } else if (cooler) {
        rating = "Partial"; reason = "Cooler but outdoor AQI elevated";
      } else if (drier) {
        rating = "Partial"; reason = "Drier but outdoor AQI elevated";
      } else {
        rating = "Poor"; reason = `Outdoor AQI ${w.aqi} (elevated) + conditions poor`;
      }
    } else {
      // AQI OK - use normal temperature/humidity logic
      if (cooler && drier) {
        rating = "Good"; reason = "Cooler & drier outside — ventilate";
      } else if (cooler) {
        rating = "Partial"; reason = "Cooler outside but similar humidity";
      } else if (drier) {
        rating = "Partial"; reason = "Drier outside but similar temperature";
      } else {
        rating = "Poor"; reason = "Outside conditions not favourable";
      }
    }

    // Override if pressure indicates open window/door and conditions aren't terrible
    if (pressureBoost && rating === "Poor" && (cooler || drier) && !aqiVeryBad) {
      rating = "Partial";
      reason = `Outside not ideal but pressure suggests open window${pressureTrendSignal}`;
    }

    ventEl.textContent = rating;
    ventEl.className = "value " + (rating === "Good" ? "good" : rating === "Partial" ? "moderate" : "neutral");
    ventSub.textContent = reason + pressureSignal + pressureTrendSignal;
    ventCard.style.borderTopColor = rating === "Good" ? "#2d8a2d" : rating === "Partial" ? "#c87800" : "#555";
  } else {
    ventEl.textContent = "--"; ventEl.className = "value neutral";
    ventSub.textContent = "Awaiting data";
    ventPressure.textContent = "--";
  }
}

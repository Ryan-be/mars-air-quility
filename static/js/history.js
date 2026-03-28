import { toggleTheme } from './theme.js';
import { renderSensorCharts } from './charts.js';
import { renderEnvCharts } from './charts_env.js';
import { renderCorrelationCharts } from './charts_correlation.js';
import { renderPatternCharts } from './charts_patterns.js';

window.toggleTheme = () => toggleTheme(() => { _rendered = {}; renderActiveTab(); });
window.downloadCSV = () => {
  window.open(`/api/download?range=${document.getElementById("range").value}`, "_blank");
};

const TABS = ["sensors", "particulate", "environment", "correlation", "patterns"];
let _rendered    = {};
let _sensorData  = [];
let _weatherData = [];

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("tab-active"));
    btn.classList.add("tab-active");
    TABS.forEach(t => {
      document.getElementById(`tab-${t}`).classList.toggle("tab-hidden", t !== btn.dataset.tab);
    });
    renderActiveTab();
  });
});

function activeTab() {
  return document.querySelector(".tab-btn.tab-active")?.dataset.tab ?? "sensors";
}

function renderActiveTab() {
  const tab = activeTab();
  if (_rendered[tab]) return;
  _rendered[tab] = true;
  if (tab === "sensors")     renderSensorCharts(_sensorData);
  if (tab === "particulate") renderPmTable(_sensorData);
  if (tab === "environment") renderEnvCharts(_sensorData, _weatherData);
  if (tab === "correlation") renderCorrelationCharts(_sensorData);
  if (tab === "patterns")    renderPatternCharts(_sensorData);
}

document.getElementById("range").addEventListener("change", fetchData);

async function fetchData() {
  const range = document.getElementById("range").value;
  try {
    const [sRes, wRes] = await Promise.all([
      fetch(`/api/data?range=${range}`),
      fetch(`/api/weather/history?range=${range}`),
    ]);
    const rawSensor  = await sRes.json();
    const rawWeather = await wRes.json();

    if (!Array.isArray(rawSensor) || rawSensor.length === 0) {
      document.getElementById("last-updated").textContent = "No data for selected range.";
      return;
    }
    _sensorData  = rawSensor.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    _weatherData = Array.isArray(rawWeather) ? rawWeather : [];
    _rendered    = {};
    renderActiveTab();
    document.getElementById("last-updated").textContent =
      "Last updated: " + new Date().toLocaleString();
  } catch (e) {
    document.getElementById("last-updated").textContent = "Fetch error: " + e.message;
  }
}

// ── PM table rendering ──────────────────────────────────────────────────────
function _pm25Class(v) {
  if (v == null) return "";
  if (v <= 12) return "pm-good";
  if (v <= 35) return "pm-moderate";
  return "pm-unhealthy";
}

function renderPmTable(data) {
  const pmRows = data.filter(d => d.pm2_5 != null || d.pm1_0 != null || d.pm10 != null);

  // Summary averages
  const avg = (arr) => arr.length ? (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1) : "--";
  const pm1Vals  = pmRows.map(d => d.pm1_0).filter(v => v != null);
  const pm25Vals = pmRows.map(d => d.pm2_5).filter(v => v != null);
  const pm10Vals = pmRows.map(d => d.pm10).filter(v => v != null);

  const sumPm1  = document.getElementById("pmSumPm1");
  const sumPm25 = document.getElementById("pmSumPm25");
  const sumPm10 = document.getElementById("pmSumPm10");
  if (sumPm1)  sumPm1.textContent  = avg(pm1Vals);
  if (sumPm25) sumPm25.textContent = avg(pm25Vals);
  if (sumPm10) sumPm10.textContent = avg(pm10Vals);

  // Color the PM2.5 summary
  const avgPm25 = pm25Vals.length ? pm25Vals.reduce((a, b) => a + b, 0) / pm25Vals.length : null;
  if (sumPm25) sumPm25.className = `value ${_pm25Class(avgPm25)}`;

  const tbody = document.getElementById("pmTableBody");
  if (!tbody) return;

  if (!pmRows.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="pm-empty">No particulate data for this range.</td></tr>';
    return;
  }

  // Show most recent first, limit to 200 rows for performance
  const display = pmRows.slice().reverse().slice(0, 200);
  tbody.innerHTML = display.map(d => {
    const ts = new Date(d.timestamp).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
    const cls = _pm25Class(d.pm2_5);
    return `<tr class="${cls}">
      <td>${ts}</td>
      <td>${d.pm1_0 ?? "--"}</td>
      <td>${d.pm2_5 ?? "--"}</td>
      <td>${d.pm10 ?? "--"}</td>
    </tr>`;
  }).join("");
}

// ── Chart info button toggle ─────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".chart-info-btn");
  // Close all open popups first
  document.querySelectorAll(".chart-info-popup.visible").forEach(p => {
    if (!btn || p.id !== `info-${btn.dataset.info}`) p.classList.remove("visible");
  });
  if (!btn) return;
  const popup = document.getElementById(`info-${btn.dataset.info}`);
  if (popup) popup.classList.toggle("visible");
});

fetchData();

// ── SSE: refresh charts when new sensor data arrives (throttled to ~30s) ─────
let _histSSE = null;
let _histRetryMs = 1000;
let _histPending = false;

function _throttledFetch() {
  if (_histPending) return;
  _histPending = true;
  setTimeout(() => {
    _rendered = {};
    fetchData();
    _histPending = false;
  }, 30000);
}

function connectHistorySSE() {
  if (_histSSE) _histSSE.close();
  _histSSE = new EventSource("/api/stream");
  _histSSE.addEventListener("sensor_update", _throttledFetch);
  _histSSE.onopen = () => { _histRetryMs = 1000; };
  _histSSE.onerror = () => {
    _histSSE.close();
    setTimeout(connectHistorySSE, _histRetryMs);
    _histRetryMs = Math.min(_histRetryMs * 2, 30000);
  };
}

connectHistorySSE();

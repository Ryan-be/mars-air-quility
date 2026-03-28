import { toggleTheme } from './theme.js';
import { renderSensorCharts } from './charts.js';
import { renderEnvCharts } from './charts_env.js';
import { renderCorrelationCharts } from './charts_correlation.js';
import { renderPatternCharts } from './charts_patterns.js';

window.toggleTheme = () => toggleTheme(() => { _rendered = {}; renderActiveTab(); });
window.downloadCSV = () => {
  window.open(`/api/download?range=${document.getElementById("range").value}`, "_blank");
};

const TABS = ["sensors", "environment", "correlation", "patterns"];
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

import { toggleTheme } from './theme.js';
import { setFanControl, fetchFanStatus, fetchAutoStatus } from './fan.js';

window.toggleTheme   = () => toggleTheme(null);
window.setFanControl = setFanControl;
window.toggleAutoInfo = toggleAutoInfo;

function updateStatusDot(statusText) {
  const dot = document.getElementById("fanStatusDot");
  if (!dot) return;
  const on = statusText === "true" || statusText === "on";
  dot.className = "status-dot " + (on ? "dot-on" : "dot-off");
}

async function pollFan() {
  await fetchFanStatus();
  const statusEl = document.getElementById("fan-status");
  if (statusEl) updateStatusDot(statusEl.textContent.toLowerCase().trim());
  await updateAutoInfoPanel();
}

async function updateAutoInfoPanel() {
  const panel = document.getElementById("autoInfoPanel");
  if (!panel || panel.classList.contains("hidden")) return;
  await renderAutoInfo();
}

async function renderAutoInfo() {
  const data = await fetchAutoStatus();
  const summary = document.getElementById("autoInfoSummary");
  const list = document.getElementById("autoInfoRules");
  if (!data) {
    summary.textContent = "Unable to fetch auto-status.";
    list.innerHTML = "";
    return;
  }

  if (data.mode !== "auto") {
    summary.textContent = "Auto mode is off — fan is under manual control.";
    list.innerHTML = "";
    return;
  }

  if (!data.auto_enabled) {
    summary.textContent = "Auto mode selected but not enabled in settings.";
    list.innerHTML = "";
    return;
  }

  const actionLabel = data.action === "on" ? "ON" : "OFF";
  summary.textContent = `Auto mode active — fan is ${actionLabel}.`;

  if (data.rules && data.rules.length) {
    list.innerHTML = data.rules.map(r => {
      const cls = r.action === "on" ? "rule-on" : r.action === "off" ? "rule-off" : "rule-no-opinion";
      return `<li class="${cls}"><strong>${r.rule}:</strong> ${r.reason}</li>`;
    }).join("");
  } else {
    list.innerHTML = "<li>No evaluation data yet — waiting for first sensor reading.</li>";
  }
}

function toggleAutoInfo() {
  const panel = document.getElementById("autoInfoPanel");
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden")) {
    renderAutoInfo();
  }
}

pollFan();

// ── SSE for real-time fan status push ───────────────────────────────────────

let _fanSSE = null;
let _fanRetryMs = 1000;

function connectFanSSE() {
  if (_fanSSE) _fanSSE.close();
  _fanSSE = new EventSource("/api/stream");

  _fanSSE.addEventListener("fan_status", () => { pollFan(); });
  _fanSSE.addEventListener("sensor_update", () => { pollFan(); });

  _fanSSE.onopen = () => { _fanRetryMs = 1000; };
  _fanSSE.onerror = () => {
    _fanSSE.close();
    setTimeout(connectFanSSE, _fanRetryMs);
    _fanRetryMs = Math.min(_fanRetryMs * 2, 30000);
  };
}

connectFanSSE();

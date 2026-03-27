import { toggleTheme } from './theme.js';
import { setFanControl, fetchFanStatus } from './fan.js';

window.toggleTheme   = () => toggleTheme(null);
window.setFanControl = setFanControl;

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
}

pollFan();
setInterval(pollFan, 15000);

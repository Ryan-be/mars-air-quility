import { renderGrowCard } from "./components/grow-card.mjs";
import { renderEmptyState } from "./components/empty-state.mjs";

const STATE = { units: [] };


async function _fetchEnrollmentKey() {
  try {
    const r = await fetch("/api/grow/enrollment-key/peek-once");
    if (r.ok) return (await r.json()).key;
  } catch (_) {}
  return null;
}


async function refreshEmpty() {
  const grid = document.getElementById("grow-grid");
  grid.innerHTML = "";
  const key = await _fetchEnrollmentKey();
  grid.appendChild(renderEmptyState({
    enrollmentKey: key,
    mlssHost: window.location.hostname,
  }));
}

async function fetchUnits() {
  const r = await fetch("/api/grow/units");
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return (await r.json()).units;
}

function renderSummary(units) {
  const counts = {
    total: units.length,
    online: units.filter(u => u.status === "online").length,
    stale: units.filter(u => u.status === "stale").length,
    offline: units.filter(u => u.status === "offline").length,
  };
  const el = document.getElementById("grow-summary");
  el.innerHTML = "";
  for (const [k, v, cls] of [
    ["UNITS", counts.total, ""],
    ["ONLINE", counts.online, "ok"],
    ["STALE", counts.stale, "warn"],
    ["OFFLINE", counts.offline, "crit"],
  ]) {
    const div = document.createElement("div");
    div.innerHTML = `<span class="num ${cls}">${v}</span><span class="lbl">${k}</span>`;
    el.appendChild(div);
  }
}

function renderGrid(units) {
  const grid = document.getElementById("grow-grid");
  grid.innerHTML = "";
  if (units.length === 0) {
    refreshEmpty();
    return;
  }
  for (const u of units) grid.appendChild(renderGrowCard(u));
}

async function refresh() {
  try {
    STATE.units = await fetchUnits();
    renderSummary(STATE.units);
    renderGrid(STATE.units);
  } catch (e) {
    console.error("refresh failed", e);
  }
}

document.getElementById("grow-grid").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-action='identify']");
  if (!btn) return;
  ev.preventDefault();
  const unitId = btn.dataset.unitId;
  btn.disabled = true; btn.textContent = "Blinking…";
  try {
    await fetch(`/api/grow/units/${unitId}/identify`, { method: "POST" });
    setTimeout(() => { btn.disabled = false; btn.textContent = "Identify"; }, 11000);
  } catch (e) {
    btn.disabled = false; btn.textContent = "Identify";
  }
});

// Refresh every 5s; SSE wiring is a future polish.
refresh();
setInterval(refresh, 5000);

import { renderStatusPill } from "./components/status-pill.mjs";
import { renderStatTile } from "./components/stat-tile.mjs";

const SUBTABS = [
  { id: "live", label: "● Live", enabled: true },
  { id: "history", label: "📈 History", enabled: false, deferred: "Phase 2" },
  { id: "configure", label: "⚙ Configure", enabled: false, deferred: "Phase 2" },
  { id: "diagnostics", label: "🩺 Diagnostics", enabled: false, deferred: "Phase 3" },
];


export function renderDetailHeader(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-header";

  const back = doc.createElement("a");
  back.className = "du-back";
  back.href = "/grow";
  back.textContent = "← Grow units";
  wrap.appendChild(back);

  const title = doc.createElement("div");
  title.className = "du-title";
  const h = doc.createElement("h2");
  h.textContent = unit.label;
  title.appendChild(h);

  const phasePill = doc.createElement("span");
  phasePill.className = "du-pill phase";
  phasePill.textContent = (unit.current_phase || "").toUpperCase();
  title.appendChild(phasePill);

  const mediumPill = doc.createElement("span");
  mediumPill.className = "du-pill";
  const dayCount = unit.sown_at
    ? Math.floor((Date.now() - new Date(unit.sown_at).getTime()) / 86400000)
    : null;
  mediumPill.textContent = `${(unit.medium_type || "").toUpperCase()}` +
    (dayCount !== null ? ` · day ${dayCount}` : "");
  title.appendChild(mediumPill);

  title.appendChild(renderStatusPill(unit.status, { ownerDocument: doc }));
  wrap.appendChild(title);
  return wrap;
}


export function renderSubTabs(activeTab, doc = document) {
  const nav = doc.createElement("div");
  nav.className = "du-tabs";
  for (const t of SUBTABS) {
    const el = doc.createElement("button");
    el.className = "du-tab" + (t.id === activeTab ? " active" : "")
                  + (!t.enabled ? " disabled" : "");
    el.dataset.tab = t.id;
    el.textContent = t.label;
    if (!t.enabled) {
      el.disabled = true;
      el.title = `Coming in ${t.deferred}`;
    }
    nav.appendChild(el);
  }
  return nav;
}


const CHANNEL_DISPLAY = {
  soil_moisture: { label: "Moisture", format: (v) => `${Math.round(v)}%`,
                   stateKey: "soil_moisture_pct" },
  soil_temp_c: { label: "Soil temp", format: (v) => `${v.toFixed(1)}°C`,
                 stateKey: "soil_temp_c" },
  ambient_lux: { label: "Ambient lux", format: (v) => v.toLocaleString(),
                 stateKey: "ambient_lux" },
  air_temp_c: { label: "Air temp", format: (v) => `${v.toFixed(1)}°C`,
                stateKey: "air_temp_c" },
  air_humidity_pct: { label: "Air humidity", format: (v) => `${Math.round(v)}%`,
                      stateKey: "air_humidity_pct" },
  reservoir_level_pct: { label: "Reservoir", format: (v) => `${Math.round(v)}%`,
                         stateKey: "reservoir_level_pct" },
  light: { label: "Grow light", format: () => "",  // handled specially below
           stateKey: "light_state" },
};


export function renderLiveReadings(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📊 Live readings</span>";
  wrap.appendChild(head);

  const grid = doc.createElement("div");
  grid.className = "du-stat-grid";

  for (const cap of unit.capabilities || []) {
    const meta = CHANNEL_DISPLAY[cap.channel];
    if (!meta) continue;
    const value = unit.last_known_state?.[meta.stateKey];
    if (value == null && cap.channel !== "light") continue;

    let tile;
    if (cap.channel === "light") {
      tile = renderStatTile({
        value: value ? "💡 ON" : "💡 OFF",
        label: meta.label, isRequired: cap.is_required,
        ownerDocument: doc,
      });
    } else {
      const variant = (cap.channel === "soil_moisture" && value < 35) ? "warn" : "normal";
      tile = renderStatTile({
        value: meta.format(value),
        label: meta.label,
        isRequired: cap.is_required,
        variant,
        ownerDocument: doc,
      });
    }
    grid.appendChild(tile);
  }

  wrap.appendChild(grid);
  return wrap;
}


export function renderPhotoPanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📷 Latest photo</span>";
  wrap.appendChild(head);

  const photo = doc.createElement("div");
  photo.className = "du-photo-hero";
  // Cache-bust to refresh on poll
  const url = `/api/grow/units/${unit.id}/photo/latest?ts=${Date.now()}`;
  photo.style.backgroundImage = `url(${url})`;
  photo.style.backgroundSize = "cover";
  photo.style.backgroundPosition = "center";
  wrap.appendChild(photo);
  return wrap;
}


export function computeWaterLockedUntil(lastPulseAt, soakWindowMin, now = new Date()) {
  if (!lastPulseAt) return null;
  const last = lastPulseAt instanceof Date ? lastPulseAt : new Date(lastPulseAt);
  const unlock = new Date(last.getTime() + soakWindowMin * 60 * 1000);
  return unlock > now ? unlock : null;
}


export function renderQuickControls(unit, doc = document) {
  const panel = doc.createElement("div");
  panel.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>⚡ Quick controls</span>";
  panel.appendChild(head);

  const body = doc.createElement("div");
  body.className = "du-quick";

  const lockedUntil = unit._waterLockedUntil ?? null;
  const isLocked = lockedUntil !== null;

  const buttons = [
    { action: "identify", label: "⚡ Identify",
      enabled: true, primary: true },
    { action: "water-now", label: isLocked ? `🔒 Water (locked)` : "💧 Water 5s",
      enabled: !isLocked,
      tooltip: isLocked ? `Locked until ${lockedUntil.toLocaleTimeString()}` : "Pulse pump for 5s" },
    { action: "light-toggle", label: "💡 Toggle light", enabled: true },
    { action: "snap-photo", label: "📷 Snap photo", enabled: true },
  ];

  for (const b of buttons) {
    const btn = doc.createElement("button");
    btn.className = "du-act-btn" + (b.primary ? " primary" : "")
                  + (!b.enabled ? " locked" : "");
    btn.dataset.action = b.action;
    btn.dataset.unitId = unit.id;
    btn.disabled = !b.enabled;
    btn.textContent = b.label;
    if (b.tooltip) btn.title = b.tooltip;
    body.appendChild(btn);
  }

  panel.appendChild(body);

  // Wire click handlers
  panel.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    const url = `/api/grow/units/${unit.id}/${btn.dataset.action}`;
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await fetch(url, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}) });
      btn.textContent = r.ok ? "✓ Sent" : "✗ Failed";
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = old; }, 2000);
    }
  });

  return panel;
}


async function init() {
  const root = document.querySelector("[data-unit-id]");
  const unitId = root.dataset.unitId;
  const r = await fetch(`/api/grow/units/${unitId}`);
  if (!r.ok) {
    document.getElementById("du-body").textContent = "Failed to load unit";
    return;
  }
  const unit = await r.json();
  document.getElementById("du-header").appendChild(renderDetailHeader(unit));
  document.getElementById("du-tabs").appendChild(renderSubTabs("live"));

  const body = document.getElementById("du-body");
  body.appendChild(renderPhotoPanel(unit));
  body.appendChild(renderLiveReadings(unit));

  // Compute water-lock from unit.last_known_state.last_pulse_at + unit.soak_window_min_resolved
  const lastPulse = unit.last_known_state?.last_pulse_at || null;
  const soakMin = unit.soak_window_min_resolved || 30;  // server should send this
  unit._waterLockedUntil = computeWaterLockedUntil(lastPulse, soakMin);

  body.appendChild(renderQuickControls(unit));
}

// Only run init() in a real browser context where the page root is mounted.
// Tests import the module without a [data-unit-id] root present, so guard
// against null dereference on root.dataset.
if (typeof document !== "undefined" && document.querySelector("[data-unit-id]")) {
  init();
}

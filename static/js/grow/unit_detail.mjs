import { renderStatusPill } from "./components/status-pill.mjs";
import { renderStatTile } from "./components/stat-tile.mjs";
import { renderScheduleBar } from "./components/schedule-bar.mjs";
import { renderSensorEventChart } from "./components/sensor-event-chart.mjs";
import { renderProfileEditor } from "./components/profile-editor.mjs";
import { renderPIDEditor } from "./components/pid-editor.mjs";
import { renderLightWindowsEditor } from "./components/light-windows-editor.mjs";
import { renderCalibrationWizard } from "./components/calibration-wizard.mjs";
import { renderPhotoScheduleEditor } from "./components/photo-schedule-editor.mjs";
import { renderSafetyOverride } from "./components/safety-override.mjs";
import { renderHistoryPanel } from "./components/history-panel.mjs";
import { renderDiagnosticsPanel } from "./components/diagnostics-panel.mjs";
import { openLightbox } from "./components/photo-lightbox.mjs";

const SUBTABS = [
  { id: "live", label: "● Live", enabled: true },
  { id: "history", label: "📈 History", enabled: true },
  { id: "configure", label: "⚙ Configure", enabled: true },
  { id: "diagnostics", label: "🩺 Diagnostics", enabled: true },
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


/** Refresh the latest-photo panel by re-setting its background-image
 *  with a fresh cache-bust ts. Exposed so the Quick Controls click
 *  handler can call it after Snap-photo, and so init() can set up a
 *  poll. Looks up the panel by class — there's only ever one
 *  `.du-photo-hero` on the page (Live tab single-mount).
 *
 *  @param {number} unitId  the unit id
 *  @param {Document} doc   the owner document (default: current page)
 */
export function refreshPhotoPanel(unitId, doc = document) {
  const photo = doc.querySelector(".du-photo-hero");
  if (!photo) return;  // Live tab not mounted (e.g., we're on Configure)
  const url = `/api/grow/units/${unitId}/photo/latest?ts=${Date.now()}`;
  photo.style.backgroundImage = `url(${url})`;
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
  // Click to open the lightbox. Live tab is single-photo (no nav
  // context) — we re-fetch /photo/latest with a fresh cache-bust ts so
  // the lightbox shows whatever's current rather than the cached
  // background-image.
  photo.style.cursor = "pointer";
  photo.addEventListener("click", () => {
    const fullUrl = `/api/grow/units/${unit.id}/photo/latest?ts=${Date.now()}`;
    openLightbox({ photoUrl: fullUrl, ownerDocument: doc });
  });
  wrap.appendChild(photo);
  return wrap;
}


export function renderLightSchedulePanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = `<span>🕐 Light schedule · ${unit.current_phase}</span>`;
  wrap.appendChild(head);

  // Phase 1: assume single window from spec defaults if no per-unit windows present.
  // Phase 2 will let users edit windows in the Configure tab.
  const windows = unit.light_windows && unit.light_windows.length > 0
    ? unit.light_windows
    : [{ start: "06:00", end: "22:00" }];
  wrap.appendChild(renderScheduleBar(windows, new Date(), doc));
  return wrap;
}


async function renderWateringHistoryPanel(unit, doc = document) {
  const wrap = doc.createElement("div");
  wrap.className = "du-panel";
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>💧 Watering history · last 24h</span>";
  wrap.appendChild(head);
  const chartDiv = doc.createElement("div");
  chartDiv.id = `watering-chart-${unit.id}`;
  wrap.appendChild(chartDiv);

  const r = await fetch(`/api/grow/units/${unit.id}/history?range=24h`);
  if (r.ok) {
    const data = await r.json();
    renderSensorEventChart(chartDiv, data);
  }
  return wrap;
}


export function computeWaterLockedUntil(lastPulseAt, soakWindowMin, now = new Date()) {
  if (!lastPulseAt) return null;
  const last = lastPulseAt instanceof Date ? lastPulseAt : new Date(lastPulseAt);
  const unlock = new Date(last.getTime() + soakWindowMin * 60 * 1000);
  return unlock > now ? unlock : null;
}


/** Look up the health flag for an actuator capability.
 *  Returns "connected" by default (so units without an explicit health
 *  field render normally). Returns null if the capability isn't present
 *  at all — caller decides whether to skip the button entirely.
 *
 *  Phase 2 sense-only-mode: this is the single read-point for actuator
 *  health from the rendered fleet/detail JSON — keep it tiny + pure so
 *  tests can poke it directly.
 */
export function actuatorHealth(unit, channel) {
  const cap = (unit.capabilities || []).find(c => c.channel === channel);
  if (!cap) return null;
  return cap.health || "connected";
}


/** Mutate `btn` based on capability health. Side-effects only (sets
 *  className, disabled, title) — pure function over the input states.
 *
 *  Health states (Phase 2 sense-only-mode):
 *    - "connected"    → no-op (button looks normal)
 *    - "untested"     → greyed BUT clickable (lets user kick off a test)
 *    - "unresponsive" → greyed AND disabled (last command went unanswered)
 *    - "no_hardware"  → greyed AND disabled (init failed at boot)
 */
export function applyHealthStyling(btn, health) {
  if (!health || health === "connected") return;
  btn.classList.add("greyed");
  if (health === "untested") {
    btn.title = "Click to test — connect 12V PSU to Automation HAT first";
  } else if (health === "unresponsive") {
    btn.classList.add("unresponsive");
    btn.disabled = true;
    btn.title = "Last command didn't reach the unit. Check power + cabling.";
  } else if (health === "no_hardware") {
    btn.classList.add("no-hardware");
    btn.disabled = true;
    btn.title = "Hardware not detected at boot.";
  }
}


/** Map a Quick Controls action to the actuator channel it drives.
 *  Returns null for actions that aren't directly tied to a single
 *  hardware channel (identify blinks the light, but greying it out
 *  on a no_hardware light would block the diagnostic too — leave it
 *  alone). Snap-photo also stays unaffected: a no_hardware camera
 *  would fail at boot loud enough to surface elsewhere.
 */
function actionToChannel(action) {
  if (action === "water-now") return "pump";
  if (action === "light-toggle") return "light";
  return null;
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

    // Phase 2 sense-only-mode: overlay health styling AFTER the lock-state
    // logic above. Health-based disable wins — a no_hardware pump must
    // stay disabled even if the soak timer has elapsed.
    const channel = actionToChannel(b.action);
    if (channel) {
      applyHealthStyling(btn, actuatorHealth(unit, channel));
    }

    body.appendChild(btn);
  }

  panel.appendChild(body);

  // Wire click handlers
  panel.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    const url = `/api/grow/units/${unit.id}/${btn.dataset.action}`;
    const old = btn.textContent;
    const action = btn.dataset.action;
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const r = await fetch(url, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}) });
      btn.textContent = r.ok ? "✓ Sent" : "✗ Failed";
      // For snap-photo: the server returns 202 immediately ("queued")
      // but the actual photo only arrives 2-5s later via the firmware's
      // WS push. Wait, then refresh the panel so the operator sees the
      // result without manual page reload.
      if (r.ok && action === "snap-photo") {
        setTimeout(() => refreshPhotoPanel(unit.id), 4000);
      }
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = old; }, 2000);
    }
  });

  return panel;
}


/** Render the Live tab body. Pulled out so the tab-switcher can call it
 *  without re-fetching the unit. The watering-history fetch is awaited so
 *  the first paint includes the chart; subsequent tab toggles re-issue
 *  the fetch (cheap, and keeps state honest if the user dwells on
 *  Configure for a while).
 */
async function renderLiveContent(body, unit, doc = document) {
  body.appendChild(renderPhotoPanel(unit, doc));
  body.appendChild(renderLiveReadings(unit, doc));
  body.appendChild(renderLightSchedulePanel(unit, doc));
  // Watering history panel is wrapped in try/catch so a Plotly /
  // chart-data shape regression can't take out the rest of the Live
  // tab (in particular: Quick Controls below — losing those means
  // operators can't snap-photo or light-toggle, which is much worse
  // than missing one chart). On failure we surface the error inline
  // and keep going.
  try {
    body.appendChild(await renderWateringHistoryPanel(unit, doc));
  } catch (exc) {
    console.error("renderWateringHistoryPanel failed:", exc);
    const errPanel = doc.createElement("div");
    errPanel.className = "du-panel";
    errPanel.innerHTML =
      "<div class='du-panel-head'><span>💧 Watering history</span></div>" +
      "<div style='padding:14px;color:#ff5252;font-size:12px;'>" +
      "Chart failed to render — see browser console.</div>";
    body.appendChild(errPanel);
  }

  // Compute water-lock from unit.last_known_state.last_pulse_at +
  // unit.soak_window_min_resolved.
  const lastPulse = unit.last_known_state?.last_pulse_at || null;
  const soakMin = unit.soak_window_min_resolved || 30;
  unit._waterLockedUntil = computeWaterLockedUntil(lastPulse, soakMin);

  body.appendChild(renderQuickControls(unit, doc));
}


/** Render the Configure tab body — five panels (Profile, PID, Light
 *  windows, Calibration, Safety override). Phase 3 Task 4 moved the
 *  per-unit token rotator to the Diagnostics tab Danger Zone where
 *  the rest of the operational/admin actions live. Token rotation is
 *  not "configuration" — it's an operations action with operational
 *  side-effects (the unit goes offline) — so co-locating it with
 *  decommission + clear-buffer makes the mental model cleaner.
 */
function renderConfigureContent(body, unit, doc = document) {
  body.appendChild(renderProfileEditor(unit, { ownerDocument: doc }));
  body.appendChild(renderPIDEditor(unit, { ownerDocument: doc }));
  body.appendChild(renderLightWindowsEditor(unit, { ownerDocument: doc }));
  body.appendChild(renderCalibrationWizard(unit, { ownerDocument: doc }));
  body.appendChild(renderPhotoScheduleEditor(unit, { ownerDocument: doc }));
  body.appendChild(renderSafetyOverride(unit, { ownerDocument: doc }));
}


/** Switch the body content between subtabs. Re-renders the whole body
 *  rather than caching panels because the panels are cheap to build and
 *  caching invites stale-state bugs (e.g. a pump-pulse happens while the
 *  user is on Configure → the Live water-lock tile would show stale data).
 *
 *  Exported for tests — the test mounts a JSDOM page with the three host
 *  elements and calls switchSubtab directly to assert tab activation
 *  swaps the body content correctly. The production click handler in
 *  init() is just a thin wrapper that calls this same function.
 */
export async function switchSubtab(tabId, unit, doc = document) {
  for (const tab of doc.querySelectorAll(".du-tab")) {
    tab.classList.toggle("active", tab.dataset.tab === tabId);
  }
  const body = doc.getElementById("du-body");
  body.innerHTML = "";
  if (tabId === "live") {
    await renderLiveContent(body, unit, doc);
  } else if (tabId === "history") {
    body.appendChild(renderHistoryPanel(unit, { ownerDocument: doc }));
  } else if (tabId === "configure") {
    renderConfigureContent(body, unit, doc);
  } else if (tabId === "diagnostics") {
    // Async like History: the orchestrator does the consolidated
    // /diagnostics fetch before mount so the panel paints fully-built.
    body.appendChild(await renderDiagnosticsPanel(unit, { ownerDocument: doc }));
  }
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
  const tabsHost = document.getElementById("du-tabs");
  tabsHost.appendChild(renderSubTabs("live"));
  // Tab click → switch body. Click events bubble from the rendered
  // <button data-tab> children up to the host div.
  tabsHost.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-tab]");
    if (!btn || btn.disabled) return;
    switchSubtab(btn.dataset.tab, unit, document);
  });

  const body = document.getElementById("du-body");
  await renderLiveContent(body, unit, document);
}

// Only run init() in a real browser context where the page root is mounted.
// Tests import the module without a [data-unit-id] root present, so guard
// against null dereference on root.dataset.
if (typeof document !== "undefined" && document.querySelector("[data-unit-id]")) {
  init();
}

/**
 * /grow page — fleet view.
 *
 * Layout:
 *   #grow-summary    UNITS / ONLINE / STALE / OFFLINE counts
 *   #grow-filter     filter+sort row (Phase 2 Task 4)
 *   #grow-grid       responsive grid of unit cards
 *
 * Filter+sort: the row owns its own state and emits a snapshot via its
 * onChange callback. We keep a copy of that snapshot here and re-render
 * the card grid through `applyFilters` whenever it (or the units list)
 * changes. The /api/grow/units response shape is unchanged — the row is
 * pure client-side over fetched data.
 *
 * Refresh strategy: poll every 5s. SSE / WS push is a future polish.
 *
 * Testability: the rendering logic is exposed as `renderFleet({units,
 * ownerDocument})`. The boot sequence (which fetches and starts the 5s
 * interval) only runs when imported into a page that already has the
 * #grow-grid host element. Test files mount their own JSDOM and call
 * `renderFleet` directly, sidestepping the interval + fetch.
 */
import { renderGrowCard } from "./components/grow-card.mjs";
import { renderEmptyState } from "./components/empty-state.mjs";
import {
  renderFleetFilterRow,
  applyFilters,
} from "./components/fleet-filter-row.mjs";


const STATE = {
  units: [],
  filter: { phases: [], statuses: [], plant_types: [], sort: "label" },
};


async function _fetchEnrollmentKey() {
  try {
    const r = await fetch("/api/grow/enrollment-key/peek-once");
    if (r.ok) return (await r.json()).key;
  } catch (_) {}
  return null;
}


async function refreshEmpty(doc) {
  const grid = doc.getElementById("grow-grid");
  grid.innerHTML = "";
  const key = await _fetchEnrollmentKey();
  grid.appendChild(renderEmptyState({
    enrollmentKey: key,
    mlssHost: (typeof window !== "undefined" && window.location)
      ? window.location.hostname : "",
  }));
}


async function fetchUnits() {
  const r = await fetch("/api/grow/units");
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return (await r.json()).units;
}


function renderSummary(units, doc) {
  const counts = {
    total: units.length,
    online: units.filter((u) => u.status === "online").length,
    stale: units.filter((u) => u.status === "stale").length,
    offline: units.filter((u) => u.status === "offline").length,
  };
  const el = doc.getElementById("grow-summary");
  if (!el) return;
  el.innerHTML = "";
  for (const [k, v, cls] of [
    ["UNITS", counts.total, ""],
    ["ONLINE", counts.online, "ok"],
    ["STALE", counts.stale, "warn"],
    ["OFFLINE", counts.offline, "crit"],
  ]) {
    const div = doc.createElement("div");
    div.innerHTML = `<span class="num ${cls}">${v}</span><span class="lbl">${k}</span>`;
    el.appendChild(div);
  }
}


/**
 * Render the filter row into #grow-filter. The row's onChange writes
 * the new state into STATE.filter and re-renders the card grid.
 *
 * Idempotent: clears the host and remounts. Called every refresh so the
 * plant-type chip set picks up newly-enrolled units.
 */
function renderFilterRow(units, doc) {
  const host = doc.getElementById("grow-filter");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(renderFleetFilterRow({
    units,
    ownerDocument: doc,
    onChange: (newState) => {
      STATE.filter = newState;
      renderGrid(STATE.units, doc);
    },
  }));
}


function renderGrid(units, doc) {
  const grid = doc.getElementById("grow-grid");
  if (!grid) return;
  grid.innerHTML = "";
  if (units.length === 0) {
    refreshEmpty(doc);
    return;
  }
  const filtered = applyFilters(units, STATE.filter);
  for (const u of filtered) grid.appendChild(renderGrowCard(u, doc));
}


/**
 * Top-level renderer used by the page boot sequence and by tests.
 * Renders the summary, filter row, and card grid into the host elements
 * (#grow-summary, #grow-filter, #grow-grid) found in the given document.
 */
export function renderFleet({ units, ownerDocument } = {}) {
  const doc = ownerDocument || document;
  STATE.units = units || [];
  renderSummary(STATE.units, doc);
  renderFilterRow(STATE.units, doc);
  renderGrid(STATE.units, doc);
}


async function refresh() {
  try {
    const units = await fetchUnits();
    renderFleet({ units, ownerDocument: document });
  } catch (e) {
    console.error("refresh failed", e);
  }
}


/**
 * Wire up the click delegation + start the 5s refresh poll. Must be
 * called from a page that has both #grow-grid and #grow-filter mounted.
 *
 * Exported (rather than auto-running on import) so tests can dynamically
 * import the module without spinning up an outgoing-fetch poll loop.
 * The grow_fleet.html template calls boot() from a tiny inline script.
 */
export function boot() {
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

  refresh();
  setInterval(refresh, 5000);
}


// Auto-boot when running in a real browser context (http/https). Tests
// import the module under JSDOM where location.protocol is "about:" or
// "file:" — those skip the auto-boot to avoid runaway timers + fetches.
if (typeof window !== "undefined"
    && /^https?:$/.test(window.location.protocol)
    && document.getElementById("grow-grid")) {
  boot();
}

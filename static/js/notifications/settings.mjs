/**
 * Renders the Notifications card body — 4 severity preference selects,
 * subscription list, push enable button.
 *
 * Pattern matches other admin component modules: a single `render*`
 * export taking { fetchFn, ownerDocument } so jsdom tests can inject.
 */

import { subscribeForPush } from "./push-subscribe.mjs";


const SEVERITIES = ["off", "info", "warning", "critical"];
const CATEGORIES = [
  { key: "air_quality",     label: "Air quality" },
  { key: "grow_units",      label: "Grow units" },
  { key: "system_health",   label: "System health" },
  { key: "backup_pipeline", label: "Backup pipeline" },
];


export function renderNotificationsCard(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const card = doc.createElement("div");
  card.className = "card notif-card";
  card.dataset.testid = "notifications-card";
  card.innerHTML = `
    <h3>🔔 Notifications</h3>
    <p class="card-desc">
      Notify me when these event categories fire at or above the chosen
      severity. Saved per user.
    </p>
    <form id="notif-prefs-form" class="notif-prefs">
      ${CATEGORIES.map(c => `
        <div class="notif-prefs-row">
          <label for="notif-${c.key}">${c.label}</label>
          <select id="notif-${c.key}" data-pref="${c.key}" class="notif-select">
            ${SEVERITIES.map(s =>
              `<option value="${s}">${s}</option>`
            ).join("")}
          </select>
        </div>
      `).join("")}
      <button type="submit" class="btn-save" data-testid="notif-save">
        Save preferences
      </button>
      <p class="notif-status" data-testid="notif-status"></p>
    </form>

    <h4 class="notif-devices-heading">Devices subscribed for push</h4>
    <div class="notif-devices" data-testid="notif-devices"></div>
    <button type="button" class="btn-secondary" data-testid="notif-enable-push">
      Enable push on this device
    </button>
    <p class="notif-push-status" data-testid="notif-push-status"></p>
  `;

  const statusEl     = card.querySelector("[data-testid='notif-status']");
  const pushStatusEl = card.querySelector("[data-testid='notif-push-status']");
  const devicesEl    = card.querySelector("[data-testid='notif-devices']");

  function _setStatus(msg, ok) {
    statusEl.textContent = msg;
    statusEl.className = "notif-status " + (ok ? "status-ok" : "status-err");
  }
  function _setPushStatus(msg, ok) {
    pushStatusEl.textContent = msg;
    pushStatusEl.className = "notif-push-status " + (ok ? "status-ok" : "status-err");
  }

  async function loadPrefs() {
    try {
      const r = await fetchFn("/api/notifications/preferences");
      if (!r.ok) return;
      const prefs = await r.json();
      for (const c of CATEGORIES) {
        const sel = card.querySelector(`[data-pref="${c.key}"]`);
        if (sel && prefs[c.key]) sel.value = prefs[c.key];
      }
    } catch (e) { /* ignore — selects keep defaults */ }
  }

  async function loadDevices() {
    try {
      const r = await fetchFn("/api/notifications/subscriptions");
      if (!r.ok) return;
      const subs = await r.json();
      if (!subs.length) {
        devicesEl.innerHTML =
          `<p class="notif-devices-empty" data-testid="notif-devices-empty">
             No devices yet — enable push below to add this one.
           </p>`;
        return;
      }
      devicesEl.innerHTML = subs.map(s => `
        <div class="notif-device-row" data-testid="notif-device-row"
             data-sub-id="${s.id}">
          <div class="notif-device-meta">
            <strong>${s.device_label || "Unnamed device"}</strong>
            <span class="notif-device-when">
              Added ${(s.created_at || "").slice(0,10)}
              ${s.last_used_at ? ` · last seen ${s.last_used_at.slice(0,10)}` : ""}
            </span>
          </div>
          <button type="button" class="btn-danger" data-testid="notif-device-remove">
            Remove
          </button>
        </div>
      `).join("");
      // Wire remove buttons
      devicesEl.querySelectorAll("[data-testid='notif-device-remove']")
        .forEach(btn => {
          btn.addEventListener("click", async () => {
            const row = btn.closest("[data-sub-id]");
            const id = row.dataset.subId;
            const r = await fetchFn(`/api/notifications/subscriptions/${id}`,
                                    { method: "DELETE" });
            if (r.ok) {
              await loadDevices();
              _setPushStatus("Device removed.", true);
            } else {
              _setPushStatus("Could not remove device.", false);
            }
          });
        });
    } catch (e) { /* ignore — list stays empty */ }
  }

  // Form submit -> PATCH
  card.querySelector("#notif-prefs-form").addEventListener("submit", async e => {
    e.preventDefault();
    const payload = {};
    for (const c of CATEGORIES) {
      const sel = card.querySelector(`[data-pref="${c.key}"]`);
      if (sel) payload[c.key] = sel.value;
    }
    try {
      const r = await fetchFn("/api/notifications/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      _setStatus(d.message || d.error, r.ok);
    } catch (e) {
      _setStatus("Failed to save preferences.", false);
    }
  });

  // Enable push button
  card.querySelector("[data-testid='notif-enable-push']")
    .addEventListener("click", async () => {
      const label = prompt("Give this device a name (optional):",
                           "My iPhone") || "";
      try {
        await subscribeForPush({ fetchFn, deviceLabel: label });
        _setPushStatus("Push enabled. Devices list refreshed.", true);
        await loadDevices();
      } catch (e) {
        _setPushStatus("Push enable failed: " + e.message, false);
      }
    });

  // Kick off initial loads
  loadPrefs();
  loadDevices();

  return card;
}

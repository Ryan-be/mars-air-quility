/**
 * Holiday-mode toggle — Settings → Grow.
 *
 * Reads GET /api/grow/settings/holiday-mode on mount; click the toggle
 * to flip → confirm modal ("This will pause watering on all units;
 * lights + telemetry continue. Continue?") → PUT.
 *
 * Confirmation is a single OK/Cancel: holiday mode is fully reversible
 * (toggle it off again). The single confirm step exists so accidentally
 * clicking the switch on a vacation-pre-flight check doesn't actually
 * leave the units mid-cycle without explicit user consent.
 *
 * Note on lag: the v1 server PUT does NOT broadcast a config_changed
 * push — units pick up the new flag on their next reconnect-pull. This
 * is documented in api_grow_settings.set_holiday_mode. The toggle still
 * reflects the server-side truth immediately because the switch's
 * displayed state comes from the PUT response, not from the units.
 */


export function renderHolidayModeToggle(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "settings-panel hm-toggle";

  const head = doc.createElement("div");
  head.className = "settings-panel-head";
  head.innerHTML = "<span>Holiday mode</span>";
  wrap.appendChild(head);

  const blurb = doc.createElement("p");
  blurb.className = "hm-blurb";
  blurb.textContent =
    "Pause pump pulses on every unit while leaving lights + telemetry " +
    "running. Use it when you're going on vacation and don't want to " +
    "come back to over-watered plants.";
  wrap.appendChild(blurb);

  const row = doc.createElement("div");
  row.className = "hm-row";

  const stateLbl = doc.createElement("span");
  stateLbl.className = "hm-state-label";
  stateLbl.dataset.testid = "hm-state";
  stateLbl.textContent = "—";
  row.appendChild(stateLbl);

  const toggleBtn = doc.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "px-btn hm-toggle-btn";
  toggleBtn.textContent = "Loading…";
  toggleBtn.dataset.testid = "hm-toggle-btn";
  toggleBtn.disabled = true;
  row.appendChild(toggleBtn);

  wrap.appendChild(row);

  const confirmGroup = doc.createElement("div");
  confirmGroup.className = "hm-confirm-group";
  confirmGroup.dataset.testid = "hm-confirm-group";
  confirmGroup.style.display = "none";

  const warnEl = doc.createElement("span");
  warnEl.className = "hm-warn";
  warnEl.dataset.testid = "hm-warn";
  confirmGroup.appendChild(warnEl);

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn primary hm-confirm-btn";
  confirmBtn.textContent = "Confirm";
  confirmBtn.dataset.testid = "hm-confirm-btn";
  confirmGroup.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn hm-cancel-btn";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "hm-cancel-btn";
  confirmGroup.appendChild(cancelBtn);

  wrap.appendChild(confirmGroup);

  const errEl = doc.createElement("div");
  errEl.className = "hm-error";
  errEl.dataset.testid = "hm-error";
  errEl.style.display = "none";
  wrap.appendChild(errEl);

  // Local state mirror — updated on GET + after PUT confirmation
  let currentEnabled = null;
  let pendingTarget = null;  // the value Confirm will commit

  function _renderState() {
    if (currentEnabled === null) {
      stateLbl.textContent = "—";
      toggleBtn.textContent = "Loading…";
      toggleBtn.disabled = true;
      return;
    }
    stateLbl.textContent = currentEnabled ? "ON" : "OFF";
    stateLbl.className = "hm-state-label " +
      (currentEnabled ? "hm-on" : "hm-off");
    toggleBtn.textContent = currentEnabled
      ? "Turn holiday mode OFF"
      : "Turn holiday mode ON";
    toggleBtn.disabled = false;
  }

  async function _loadState() {
    try {
      const r = await fetchFn("/api/grow/settings/holiday-mode");
      if (!r.ok) {
        errEl.textContent = `✗ Failed to load (${r.status})`;
        errEl.style.display = "";
        return;
      }
      const body = await r.json();
      currentEnabled = !!body.enabled;
      _renderState();
    } catch (exc) {
      errEl.textContent = `✗ ${exc.message || "Network error"}`;
      errEl.style.display = "";
    }
  }

  function _arm() {
    if (currentEnabled === null) return;
    pendingTarget = !currentEnabled;
    if (pendingTarget) {
      warnEl.textContent =
        "This will pause watering on every unit. Lights + telemetry " +
        "continue. Confirm?";
    } else {
      warnEl.textContent = "Resume normal watering on every unit. Confirm?";
    }
    toggleBtn.style.display = "none";
    confirmGroup.style.display = "";
    errEl.style.display = "none";
  }

  function _disarm() {
    pendingTarget = null;
    toggleBtn.style.display = "";
    confirmGroup.style.display = "none";
  }

  async function _commit() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Saving…";
    try {
      const r = await fetchFn("/api/grow/settings/holiday-mode", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: pendingTarget }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        errEl.textContent = `✗ ${err.error || r.statusText || "Error"}`;
        errEl.style.display = "";
        confirmBtn.disabled = false;
        cancelBtn.disabled = false;
        confirmBtn.textContent = "Confirm";
        return;
      }
      const body = await r.json();
      currentEnabled = !!body.enabled;
      _disarm();
      _renderState();
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm";
    } catch (exc) {
      errEl.textContent = `✗ ${exc.message || "Network error"}`;
      errEl.style.display = "";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm";
    }
  }

  toggleBtn.addEventListener("click", _arm);
  cancelBtn.addEventListener("click", _disarm);
  confirmBtn.addEventListener("click", _commit);

  _loadState();
  return wrap;
}

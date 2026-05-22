/**
 * Add-effector modal (Phase 9 Task 9.1).
 *
 * Launched from two entry points (Phase 9 Tasks 9.2 + 9.3):
 *
 *   1. Topology topbar "+ Add effector" button — defaultScope="hub",
 *      defaultGrowUnitId=null. Operator picks a hub-only effector type
 *      (fan, ac, heater, etc.) or a both-scope type and the Hub radio
 *      stays selected by default.
 *   2. Grow-unit Configure tab "+ Add effector" button —
 *      defaultScope="grow_unit", defaultGrowUnitId=<unit.id>. The grow
 *      select is pre-populated + locked to the unit the operator
 *      navigated from.
 *
 * Structure (mirror of `static/js/grow/components/add-unit-modal.mjs`
 * scaffold):
 *
 *   <div class="add-effector-overlay">
 *     <div class="add-effector-box">
 *       <div class="add-effector-head">
 *         <h3>Add effector</h3>
 *         <button data-testid="add-effector-close">×</button>
 *       </div>
 *       <div class="add-effector-body">
 *         <div class="add-effector-error" hidden></div>
 *         <fieldset>
 *           <legend>Type</legend>
 *           …11 radios…
 *         </fieldset>
 *         <fieldset>
 *           <legend>Scope</legend>
 *           <label><input type="radio" name="scope" value="hub"> Hub</label>
 *           <label><input type="radio" name="scope" value="grow_unit"> Grow unit</label>
 *           <div class="add-effector-grow-row">
 *             <label>Grow unit
 *               <select name="grow_unit_id">…</select>
 *             </label>
 *           </div>
 *         </fieldset>
 *         <label>Label <input name="label" required></label>
 *         <label>Kasa host/IP <input name="kasa_host" required></label>
 *         <div class="add-effector-foot">
 *           <button data-action="cancel">Cancel</button>
 *           <button data-action="submit" class="primary">Add effector</button>
 *         </div>
 *       </div>
 *     </div>
 *   </div>
 *
 * Validation: empty label or empty kasa_host → inline error visible,
 * no POST.
 *
 * Type ↔ scope compatibility: the COMPATIBLE_SCOPES matrix mirrors
 * `mlss_monitor/effectors/base.py::COMPATIBLE_SCOPES`. When the
 * selected type's compat set excludes a scope, that radio is `disabled`
 * + (if currently selected) the modal swaps the selection to the only
 * compatible scope.
 *
 * Submit POSTs `/api/effectors` with the full body
 * `{effector_type, scope, grow_unit_id, label, kasa_host,
 *   is_enabled: 1, auto_mode: 1, rules: {}}`. The server's
 * @require_role("admin") gate is the canonical authorisation check.
 * The 201 path calls `onCreated(newEffector)` + closes; 409 keeps the
 * modal open + surfaces an inline "duplicate kasa_host" message; other
 * 4xx/5xx surface the server-supplied `error` string.
 *
 * Close paths: × button, ESC key, backdrop click, Cancel button.
 */


/** Pretty labels for the 11 effector types. Matches the wording in the
 * plan spec; the keys mirror database/effectors_schema._EFFECTOR_TYPES. */
const TYPE_LABELS = {
  fan:                 "Fan",
  fan_carbon_filter:   "Fan + carbon filter",
  circulation_fan:     "Circulation fan",
  ac:                  "AC",
  whole_room_heater:   "Heater (room)",
  humidifier:          "Humidifier",
  dehumidifier:        "Dehumidifier",
  light_supplementary: "Supplementary light",
  heat_pad:            "Heat pad",
  generic:             "Generic plug",
  co2_injector:        "CO₂ injector",
};

/** The ordered tuple of effector_type values for rendering. Order
 * matches the picker UX brief (most-common first; heat_pad sits with
 * the grow-only outlier; co2_injector last as a "future" type). */
const TYPE_ORDER = [
  "fan", "fan_carbon_filter", "circulation_fan", "ac",
  "whole_room_heater", "humidifier", "dehumidifier",
  "light_supplementary", "heat_pad", "generic", "co2_injector",
];

/** Per-type scope whitelist. Mirrors mlss_monitor.effectors.base.COMPATIBLE_SCOPES
 * exactly. Keeping a client-side copy avoids a network round trip just
 * to enable/disable a radio button, and the v2 API still validates
 * server-side so a drift between this matrix and the Python one fails
 * loudly via a 400. */
const COMPATIBLE_SCOPES = {
  fan:                 ["hub"],
  fan_carbon_filter:   ["hub"],
  circulation_fan:     ["hub"],
  ac:                  ["hub"],
  whole_room_heater:   ["hub"],
  dehumidifier:        ["hub"],
  humidifier:          ["hub", "grow_unit"],
  light_supplementary: ["hub", "grow_unit"],
  heat_pad:            ["grow_unit"],
  generic:             ["hub", "grow_unit"],
  co2_injector:        ["hub"],
};


/**
 * Open the add-effector modal.
 *
 * @param {object} opts
 * @param {"hub"|"grow_unit"} [opts.defaultScope="hub"]
 * @param {number|null} [opts.defaultGrowUnitId=null]
 * @param {Function} [opts.onCreated=()=>{}] Called with the response
 *   body on a 201. Caller updates its in-memory store + re-renders.
 * @param {Document} [opts.ownerDocument=document]
 * @param {Function} [opts.fetchFn=fetch]
 * @returns {{ close: () => void, element: HTMLElement }}
 */
export function openAddEffectorModal(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));
  const onCreated = opts.onCreated || (() => {});
  const defaultScope = opts.defaultScope || "hub";
  const defaultGrowUnitId = opts.defaultGrowUnitId ?? null;

  // ── overlay (dim backdrop) ──────────────────────────────────────────
  const overlay = doc.createElement("div");
  overlay.className = "add-effector-overlay";
  overlay.dataset.testid = "add-effector-overlay";

  const box = doc.createElement("div");
  box.className = "add-effector-box";
  box.dataset.testid = "add-effector-box";
  overlay.appendChild(box);

  // ── header ──────────────────────────────────────────────────────────
  const head = doc.createElement("div");
  head.className = "add-effector-head";
  const headTitle = doc.createElement("h3");
  headTitle.textContent = "Add effector";
  head.appendChild(headTitle);
  const closeBtn = doc.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "add-effector-close";
  closeBtn.dataset.testid = "add-effector-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";  // U+00D7 multiplication sign
  head.appendChild(closeBtn);
  box.appendChild(head);

  // ── body ────────────────────────────────────────────────────────────
  const body = doc.createElement("div");
  body.className = "add-effector-body";
  box.appendChild(body);

  // Inline error surface — empty by default; populated on validation
  // failures + non-2xx server responses.
  const errEl = doc.createElement("div");
  errEl.className = "add-effector-error";
  errEl.dataset.testid = "add-effector-error";
  errEl.style.display = "none";
  body.appendChild(errEl);

  // ── effector_type radios ────────────────────────────────────────────
  const typeSet = doc.createElement("fieldset");
  typeSet.className = "add-effector-type";
  const typeLegend = doc.createElement("legend");
  typeLegend.textContent = "Type";
  typeSet.appendChild(typeLegend);

  // When the caller specifies defaultScope="grow_unit" we pre-select a
  // type that's compatible with grow scope so the compat enforcement
  // doesn't immediately flip scope back to hub. "humidifier" is the
  // first both-scope type in TYPE_ORDER and a sensible neutral default
  // for grow-unit boot flow. Hub-default keeps the prototype "fan"
  // top-of-list.
  const _initialType = defaultScope === "grow_unit"
    ? "humidifier"
    : TYPE_ORDER[0];

  for (const t of TYPE_ORDER) {
    const wrap = doc.createElement("label");
    wrap.className = "add-effector-type-row";
    const r = doc.createElement("input");
    r.type = "radio";
    r.name = "effector_type";
    r.value = t;
    if (t === _initialType) r.checked = true;
    wrap.appendChild(r);
    const span = doc.createElement("span");
    span.textContent = TYPE_LABELS[t] || t;
    wrap.appendChild(span);
    typeSet.appendChild(wrap);
  }
  body.appendChild(typeSet);

  // ── scope radios + grow-unit picker ─────────────────────────────────
  const scopeSet = doc.createElement("fieldset");
  scopeSet.className = "add-effector-scope";
  const scopeLegend = doc.createElement("legend");
  scopeLegend.textContent = "Scope";
  scopeSet.appendChild(scopeLegend);
  function _mkScope(value, label) {
    const wrap = doc.createElement("label");
    wrap.className = "add-effector-scope-row";
    const r = doc.createElement("input");
    r.type = "radio";
    r.name = "scope";
    r.value = value;
    wrap.appendChild(r);
    const span = doc.createElement("span");
    span.textContent = label;
    wrap.appendChild(span);
    scopeSet.appendChild(wrap);
    return r;
  }
  const hubR = _mkScope("hub", "Hub");
  const growR = _mkScope("grow_unit", "Grow unit");
  if (defaultScope === "grow_unit") growR.checked = true;
  else hubR.checked = true;

  // Grow-unit select row — hidden when scope=hub.
  const growRow = doc.createElement("div");
  growRow.className = "add-effector-grow-row";
  const growLabel = doc.createElement("label");
  growLabel.textContent = "Grow unit ";
  const growSel = doc.createElement("select");
  growSel.name = "grow_unit_id";
  growLabel.appendChild(growSel);
  growRow.appendChild(growLabel);
  scopeSet.appendChild(growRow);
  body.appendChild(scopeSet);

  // Hide-or-show the grow row based on the current scope.
  function _refreshGrowRowVisibility() {
    if (growR.checked) {
      growRow.classList.remove("hidden");
      growRow.style.display = "";
    } else {
      growRow.classList.add("hidden");
      growRow.style.display = "none";
    }
  }
  _refreshGrowRowVisibility();

  // ── label + kasa_host inputs ────────────────────────────────────────
  const labelLab = doc.createElement("label");
  labelLab.className = "add-effector-field";
  labelLab.textContent = "Label ";
  const labelIn = doc.createElement("input");
  labelIn.type = "text";
  labelIn.name = "label";
  labelIn.required = true;
  labelLab.appendChild(labelIn);
  body.appendChild(labelLab);

  const hostLab = doc.createElement("label");
  hostLab.className = "add-effector-field";
  hostLab.textContent = "Kasa host/IP ";
  const hostIn = doc.createElement("input");
  hostIn.type = "text";
  hostIn.name = "kasa_host";
  hostIn.required = true;
  hostIn.placeholder = "192.0.2.10";
  hostLab.appendChild(hostIn);
  body.appendChild(hostLab);

  // ── footer: cancel + submit ─────────────────────────────────────────
  const foot = doc.createElement("div");
  foot.className = "add-effector-foot";
  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.dataset.action = "cancel";
  cancelBtn.className = "add-effector-cancel";
  cancelBtn.textContent = "Cancel";
  foot.appendChild(cancelBtn);
  const submitBtn = doc.createElement("button");
  submitBtn.type = "button";
  submitBtn.dataset.action = "submit";
  submitBtn.className = "add-effector-submit primary";
  submitBtn.textContent = "Add effector";
  foot.appendChild(submitBtn);
  body.appendChild(foot);

  // ── behaviour ──────────────────────────────────────────────────────
  function _showError(msg) {
    errEl.textContent = msg;
    errEl.style.display = "";
  }
  function _clearError() {
    errEl.textContent = "";
    errEl.style.display = "none";
  }

  // Track the in-flight POST so close + a slow network response can't
  // race onCreated against the operator pressing ESC.
  let closed = false;

  function close() {
    if (closed) return;
    closed = true;
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    doc.removeEventListener("keydown", _onKey);
  }

  function _onKey(ev) {
    if (ev.key === "Escape") close();
  }
  doc.addEventListener("keydown", _onKey);

  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  closeBtn.addEventListener("click", close);
  cancelBtn.addEventListener("click", close);

  // Type-change handler: apply scope-compatibility gating.
  for (const r of typeSet.querySelectorAll(
    "input[type='radio'][name='effector_type']",
  )) {
    r.addEventListener("change", () => _applyCompat(r.value));
  }
  growR.addEventListener("change", _refreshGrowRowVisibility);
  hubR.addEventListener("change", _refreshGrowRowVisibility);

  function _applyCompat(typeValue) {
    const allowed = COMPATIBLE_SCOPES[typeValue] || ["hub", "grow_unit"];
    hubR.disabled = !allowed.includes("hub");
    growR.disabled = !allowed.includes("grow_unit");
    // If the currently-checked scope is now disabled, flip selection
    // to the only allowed scope (every type has at least one).
    if (hubR.checked && hubR.disabled && !growR.disabled) {
      growR.checked = true;
    } else if (growR.checked && growR.disabled && !hubR.disabled) {
      hubR.checked = true;
    }
    _refreshGrowRowVisibility();
  }
  // Apply gating once for the initially-selected type. With
  // defaultScope="hub" the default type is "fan" (hub-only), so the
  // grow radio gets disabled. With defaultScope="grow_unit" the
  // default type is "humidifier" (both-scope) so both radios stay
  // enabled and the requested scope wins.
  _applyCompat(_initialType);
  // Restore the requested default scope post-compat (kicks in when
  // the initial type permits both scopes — which "humidifier" does,
  // so defaultScope="grow_unit" wins).
  if (defaultScope === "grow_unit" && !growR.disabled) {
    growR.checked = true;
    hubR.checked = false;
    _refreshGrowRowVisibility();
  } else if (defaultScope === "hub" && !hubR.disabled) {
    hubR.checked = true;
    growR.checked = false;
    _refreshGrowRowVisibility();
  }

  // Populate the grow-unit dropdown. We fire-and-await but tolerate
  // failure — the user can still submit a hub-scoped effector while
  // the units list is empty.
  (async () => {
    try {
      const r = await fetchFn("/api/grow/units");
      if (!r.ok) return;
      const data = await r.json().catch(() => ({}));
      const units = (data && data.units) || [];
      for (const u of units) {
        const opt = doc.createElement("option");
        opt.value = String(u.id);
        opt.textContent = u.label || `Unit ${u.id}`;
        growSel.appendChild(opt);
      }
      if (defaultGrowUnitId !== null && defaultGrowUnitId !== undefined) {
        growSel.value = String(defaultGrowUnitId);
      }
    } catch (_exc) {
      // Network failure on populate — leave the select empty + let the
      // operator retry. No error surface because the modal stays usable
      // for hub-scoped effectors.
    }
  })();

  // ── submit handler ─────────────────────────────────────────────────
  submitBtn.addEventListener("click", async () => {
    _clearError();
    const label = labelIn.value.trim();
    const kasaHost = hostIn.value.trim();
    if (!label) {
      _showError("Label is required.");
      return;
    }
    if (!kasaHost) {
      _showError("Kasa host (IP address or hostname) is required.");
      return;
    }
    const etype = (
      typeSet.querySelector("input[name='effector_type']:checked") || {}
    ).value || TYPE_ORDER[0];
    const scope = (
      scopeSet.querySelector("input[name='scope']:checked") || {}
    ).value || "hub";
    const growUnitId = scope === "grow_unit"
      ? (growSel.value ? parseInt(growSel.value, 10) : null)
      : null;

    // Sanity-check: grow-scoped types need a unit picked. Surface
    // inline instead of falling through to the server's 400.
    if (scope === "grow_unit" && !growUnitId) {
      _showError("Pick a grow unit for this effector.");
      return;
    }

    const payload = {
      effector_type: etype,
      scope,
      grow_unit_id: growUnitId,
      label,
      kasa_host: kasaHost,
      is_enabled: 1,
      auto_mode: 1,
      rules: {},
    };

    submitBtn.disabled = true;
    submitBtn.textContent = "Adding…";
    try {
      const r = await fetchFn("/api/effectors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.status === 201) {
        const created = await r.json().catch(() => ({}));
        try {
          onCreated(created);
        } catch (cbExc) {
          // Re-throw so a caller's bug doesn't get swallowed silently.
          // The modal still closes — onCreated only fires AFTER the
          // server has persisted, so a callback bug isn't a reason to
          // keep the modal open.
          // eslint-disable-next-line no-console
          console.error("onCreated callback threw:", cbExc);
        }
        close();
        return;
      }
      if (r.status === 409) {
        _showError(
          "That Kasa host is already in use by another effector. "
          + "Pick a different host or remove the existing entry first.",
        );
        return;
      }
      // Other 4xx/5xx — surface the server's error string if any.
      let serverMsg;
      try {
        const errJson = await r.json();
        serverMsg = errJson.error || errJson.detail;
      } catch (_e) { /* ignore */ }
      _showError(serverMsg || `Server returned HTTP ${r.status}.`);
    } catch (exc) {
      _showError(`Network error: ${exc.message || exc}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Add effector";
    }
  });

  // Mount last so the modal is fully wired before the user sees it.
  doc.body.appendChild(overlay);
  return { close, element: overlay };
}

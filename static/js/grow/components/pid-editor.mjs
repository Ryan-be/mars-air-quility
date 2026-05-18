/**
 * PID editor — second panel of the Configure tab.
 *
 * Surfaces the per-unit overrides for the soil-moisture control loop:
 * target%, kp/ki/kd, soak_window_min, min/max_pulse_s. Each field shows
 * a (default) badge when `unit.overrides.<field>` is null and a (custom)
 * badge + Reset button when it has a value. Reset queues an explicit
 * `null` for that field, which the server applies as `<col> = NULL` to
 * fall back to grow_plant_profiles defaults.
 *
 * The PUT only includes fields the user actually changed in this session
 * (or reset). That way a partial edit doesn't accidentally write defaults
 * over other untouched overrides — every PUT is a true delta.
 *
 * Field naming gotcha: GET /api/grow/units/<id> exposes the override for
 * `target_pct` under `overrides.watering_target` (db column is
 * `watering_target_override`), but the PUT body uses the contracts model
 * field name `target_pct`. We translate at the boundary.
 *
 * Client-side guard: min_pulse_s ≤ max_pulse_s. The server's pydantic
 * model_validator enforces this too, but a pre-fetch check spares the
 * user a network round-trip and gives a clear inline error.
 */


// Form-field descriptors. Each one knows its (default) value source on
// `unit.overrides` and its name on the PIDUpdate contract. step/min/max
// are passed straight to the <input type="number"> for native browser
// validation hints; they mirror the pydantic Field bounds.
const FIELDS = [
  { id: "target_pct",      overrideKey: "watering_target", label: "Target %",
    step: 1, min: 0, max: 100 },
  { id: "kp",              overrideKey: "kp",              label: "Kp",
    step: 0.1, min: 0, max: 10 },
  { id: "ki",              overrideKey: "ki",              label: "Ki",
    step: 0.1, min: 0, max: 10 },
  { id: "kd",              overrideKey: "kd",              label: "Kd",
    step: 0.1, min: 0, max: 10 },
  { id: "soak_window_min", overrideKey: "soak_window_min", label: "Soak window (min)",
    step: 1, min: 0, max: 240 },
  { id: "min_pulse_s",     overrideKey: "min_pulse_s",     label: "Min pulse (s)",
    step: 0.5, min: 0, max: 60 },
  { id: "max_pulse_s",     overrideKey: "max_pulse_s",     label: "Max pulse (s)",
    step: 0.5, min: 0, max: 60 },
];


/**
 * Build the PID editor panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `overrides`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderPIDEditor(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const overrides = unit.overrides || {};

  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-pid";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🎛 PID controller</span>";
  wrap.appendChild(head);

  const form = doc.createElement("form");
  form.dataset.testid = "pid-form";
  form.className = "cfg-form";
  wrap.appendChild(form);

  // Track per-field state. `dirty` flips on input; `resetToNull` flips on
  // reset-button click. Together they decide what's in the PUT body.
  // Keeping mutation isolated to this map (not the DOM) means form-state
  // recovery after validation errors doesn't have to re-read inputs.
  const fieldState = {};
  for (const f of FIELDS) {
    fieldState[f.id] = {
      original: overrides[f.overrideKey],
      dirty: false,
      resetToNull: false,
    };
  }

  for (const f of FIELDS) {
    const row = doc.createElement("div");
    row.className = "cfg-row cfg-pid-row";
    row.dataset.testid = `pid-row-${f.id}`;

    const lbl = doc.createElement("label");
    lbl.textContent = f.label;
    row.appendChild(lbl);

    const input = doc.createElement("input");
    input.type = "number";
    input.dataset.testid = `pid-input-${f.id}`;
    input.step = String(f.step);
    input.min = String(f.min);
    input.max = String(f.max);
    const overrideVal = overrides[f.overrideKey];
    input.value = overrideVal != null ? String(overrideVal) : "";
    input.placeholder = "(default)";
    row.appendChild(input);

    // Status dot (design-critique #13): replaces the previous "(DEFAULT)"
    // / "(CUSTOM)" text badge — that label was duplicative with the
    // input's "(default)" placeholder text. The dot is filled when an
    // override is active (custom value), hollow when inheriting the
    // plant-profile default. Hover shows the descriptive label so
    // accessibility / screen-reader users still get the verbose text.
    const badge = doc.createElement("span");
    badge.className = "cfg-badge " + (overrideVal == null ? "default" : "custom");
    badge.textContent = overrideVal == null ? "(default)" : "(custom)";
    badge.title = overrideVal == null
      ? "Inheriting plant-profile default"
      : "Custom override active for this unit";
    badge.setAttribute("aria-label", badge.title);
    row.appendChild(badge);

    const reset = doc.createElement("button");
    reset.type = "button";
    reset.className = "cfg-reset";
    reset.dataset.testid = `pid-reset-${f.id}`;
    reset.textContent = "Reset";
    reset.disabled = overrideVal == null;
    reset.title = "Reset to plant-profile default";
    row.appendChild(reset);

    form.appendChild(row);

    // Wire input → mark dirty so this field will be in the PUT body
    input.addEventListener("input", () => {
      fieldState[f.id].dirty = true;
      fieldState[f.id].resetToNull = false;
      // If the user types into a (default) field, flip the badge to (custom)
      // so they get a live preview before saving.
      badge.className = "cfg-badge " + (input.value === "" ? "default" : "custom");
      badge.textContent = input.value === "" ? "(default)" : "(custom)";
      reset.disabled = input.value === "";
    });

    // Wire reset → queue null + clear input
    reset.addEventListener("click", () => {
      input.value = "";
      fieldState[f.id].dirty = false;
      fieldState[f.id].resetToNull = true;
      badge.className = "cfg-badge default";
      badge.textContent = "(default)";
      reset.disabled = true;
    });
  }

  // Save row — appended last, after all field rows.
  const saveRow = doc.createElement("div");
  saveRow.className = "cfg-row cfg-actions";
  const saveBtn = doc.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "cfg-save px-btn";
  saveBtn.textContent = "Save";
  saveBtn.dataset.testid = "pid-save";
  saveRow.appendChild(saveBtn);

  const status = doc.createElement("span");
  status.className = "cfg-status";
  status.dataset.testid = "pid-status";
  saveRow.appendChild(status);
  form.appendChild(saveRow);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    // Build PUT body from dirty fields + explicit-null resets only.
    const body = {};
    for (const f of FIELDS) {
      const s = fieldState[f.id];
      if (s.resetToNull) {
        body[f.id] = null;
      } else if (s.dirty) {
        const inp = wrap.querySelector(`[data-testid='pid-input-${f.id}']`);
        const raw = inp.value.trim();
        if (raw === "") {
          // User typed and then cleared. If the override was already null,
          // skip the field entirely (no-op) — sending null would be
          // indistinguishable from Reset and surprises the user. Only
          // send an explicit null when blanking an actually-set override.
          if (s.original !== null && s.original !== undefined) {
            body[f.id] = null;
          }
          // else: no-op, skip field entirely
        } else {
          const num = Number(raw);
          if (Number.isNaN(num)) continue;
          body[f.id] = num;
        }
      }
    }
    // Client-side guard: if both min/max_pulse are present in the PUT (or
    // resolved-against-current values), make sure min ≤ max before fetch.
    // Use the about-to-PUT value if dirty, else fall back to the original
    // override. Skip the check if either side is null — the server
    // compares against the resolved profile default, which we don't see
    // here (would need a separate fetch).
    const candidateMin = "min_pulse_s" in body
      ? body.min_pulse_s : fieldState.min_pulse_s.original;
    const candidateMax = "max_pulse_s" in body
      ? body.max_pulse_s : fieldState.max_pulse_s.original;
    if (
      candidateMin != null && candidateMax != null
      && Number(candidateMin) > Number(candidateMax)
    ) {
      status.textContent = "✗ min_pulse_s must be ≤ max_pulse_s";
      status.className = "cfg-status err";
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    status.textContent = "";
    status.className = "cfg-status";
    try {
      const r = await fetch(`/api/grow/units/${unit.id}/pid`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.ok) {
        status.textContent = "✓ Saved";
        status.className = "cfg-status ok";
      } else {
        const err = await r.json().catch(() => ({}));
        const msg = err.error || err.detail || r.statusText || "Save failed";
        status.textContent = `✗ ${msg}`;
        status.className = "cfg-status err";
      }
    } catch (exc) {
      status.textContent = `✗ ${exc.message || "Network error"}`;
      status.className = "cfg-status err";
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save";
    }
  });

  return wrap;
}

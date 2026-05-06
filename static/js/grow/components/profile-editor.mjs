/**
 * Profile editor — first panel of the Configure tab.
 *
 * Renders form fields for the per-unit "what's planted here" metadata that
 * the server stores on grow_units (label, plant_type, medium_type, sown_at,
 * current_phase, description) and PUTs them to /api/grow/units/<id>/profile.
 *
 * Fields map directly to mlss_contracts.config_payloads.ProfileUpdate. The
 * server applies a partial update with `model_dump(exclude_none=True)`, so
 * sending the whole form back on every save is fine — unchanged fields are
 * just no-ops on the SQL UPDATE.
 *
 * `data-testid` attributes are used for selector stability so the JSDOM
 * tests aren't coupled to className changes for styling tweaks.
 */

const PHASES = ["seedling", "vegetative", "flowering", "fruiting", "dormant"];
const MEDIUMS = ["soil", "coco", "rockwool", "custom"];


function _isoToDateInput(iso) {
  if (!iso) return "";
  // Date input expects YYYY-MM-DD. Slice the ISO string rather than going
  // through a Date so a Z-suffixed UTC midnight doesn't flip days in
  // negative-offset locales.
  return String(iso).slice(0, 10);
}


function _row(doc, labelText, control) {
  const row = doc.createElement("div");
  row.className = "cfg-row";
  const lbl = doc.createElement("label");
  lbl.textContent = labelText;
  row.appendChild(lbl);
  row.appendChild(control);
  return row;
}


function _makeSelect(doc, options, currentValue, testid) {
  const sel = doc.createElement("select");
  sel.dataset.testid = testid;
  for (const opt of options) {
    const o = doc.createElement("option");
    o.value = opt;
    o.textContent = opt;
    if (opt === currentValue) o.selected = true;
    sel.appendChild(o);
  }
  // Make sure value attribute reflects the selection for tests that read .value
  if (currentValue) sel.value = currentValue;
  return sel;
}


/**
 * Build the profile editor panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderProfileEditor(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-profile";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🌱 Plant profile</span>";
  wrap.appendChild(head);

  const form = doc.createElement("form");
  form.dataset.testid = "profile-form";
  form.className = "cfg-form";

  // Label
  const labelInput = doc.createElement("input");
  labelInput.type = "text";
  labelInput.dataset.testid = "profile-label";
  labelInput.value = unit.label || "";
  labelInput.maxLength = 64;
  form.appendChild(_row(doc, "Label", labelInput));

  // Plant type — free text (server allows any string up to 32)
  const plantInput = doc.createElement("input");
  plantInput.type = "text";
  plantInput.dataset.testid = "profile-plant-type";
  plantInput.value = unit.plant_type || "";
  plantInput.maxLength = 32;
  form.appendChild(_row(doc, "Plant type", plantInput));

  // Medium type — restricted enum
  const mediumSel = _makeSelect(doc, MEDIUMS, unit.medium_type, "profile-medium-type");
  form.appendChild(_row(doc, "Medium", mediumSel));

  // Phase — restricted enum. Changing this stamps phase_set_by='user'
  // on the server, which the timeline respects.
  const phaseSel = _makeSelect(doc, PHASES, unit.current_phase, "profile-current-phase");
  form.appendChild(_row(doc, "Current phase", phaseSel));

  // Sown date — accept date input, convert to ISO datetime on submit.
  const sownInput = doc.createElement("input");
  sownInput.type = "date";
  sownInput.dataset.testid = "profile-sown-at";
  sownInput.value = _isoToDateInput(unit.sown_at);
  form.appendChild(_row(doc, "Sown at", sownInput));

  // Description — free text, optional
  const descInput = doc.createElement("textarea");
  descInput.dataset.testid = "profile-description";
  descInput.value = unit.description || "";
  descInput.maxLength = 500;
  descInput.rows = 2;
  form.appendChild(_row(doc, "Description", descInput));

  // Save row
  const saveRow = doc.createElement("div");
  saveRow.className = "cfg-row cfg-actions";
  const saveBtn = doc.createElement("button");
  saveBtn.type = "submit";
  saveBtn.className = "cfg-save px-btn";
  saveBtn.textContent = "Save";
  saveBtn.dataset.testid = "profile-save";
  saveRow.appendChild(saveBtn);

  const status = doc.createElement("span");
  status.className = "cfg-status";
  status.dataset.testid = "profile-status";
  saveRow.appendChild(status);
  form.appendChild(saveRow);

  wrap.appendChild(form);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {
      label: labelInput.value,
      plant_type: plantInput.value || null,
      medium_type: mediumSel.value || null,
      current_phase: phaseSel.value || null,
      description: descInput.value || null,
    };
    // sown_at: convert YYYY-MM-DD to a midnight-UTC ISO string the server
    // can parse as a datetime. Empty stays out of the body so a user
    // doesn't accidentally clobber an existing sown date by saving the form.
    if (sownInput.value) {
      body.sown_at = `${sownInput.value}T00:00:00Z`;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    status.textContent = "";
    status.className = "cfg-status";
    try {
      const r = await fetch(`/api/grow/units/${unit.id}/profile`, {
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

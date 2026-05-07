/**
 * Plant profiles editor — Settings → Grow.
 *
 * Renders one row per (plant_type, phase) combination from
 * GET /api/grow/plant-profiles. Click a row to expand into an inline
 * edit form; Save fires PUT /api/grow/plant-profiles/<id>.
 *
 * is_shipped breadcrumb: shipped rows always show a "shipped" pill; once
 * a shipped row is edited (we infer this from notes containing the
 * sentinel "[modified]" appended by Save), it also shows a "modified
 * from default" badge. The modification flag is purely a UI breadcrumb
 * — it doesn't change which fields are editable.
 *
 * The editor edits one row at a time (single open editor) — opening a
 * second row collapses the first. Keeps focus management trivial.
 */


// Editable fields. Each maps directly to a column in grow_plant_profiles
// and to a Field in the server's _ProfileUpdate pydantic model.
const FIELDS = [
  { id: "target_moisture_pct", label: "Target %",
    step: 1,   min: 0, max: 100 },
  { id: "deadband_pct",        label: "Deadband %",
    step: 0.5, min: 0, max: 20 },
  { id: "kp",                  label: "Kp",
    step: 0.1, min: 0, max: 10 },
  { id: "ki",                  label: "Ki",
    step: 0.1, min: 0, max: 10 },
  { id: "kd",                  label: "Kd",
    step: 0.1, min: 0, max: 10 },
  { id: "min_pulse_s",         label: "Min pulse (s)",
    step: 0.5, min: 0, max: 60 },
  { id: "max_pulse_s",         label: "Max pulse (s)",
    step: 0.5, min: 0, max: 60 },
  { id: "soak_window_min",     label: "Soak window (min)",
    step: 1,   min: 0, max: 240 },
  { id: "default_light_hours", label: "Light hours",
    step: 0.5, min: 0, max: 24 },
];


function _isModified(profile) {
  // We use a notes prefix as a lightweight modification breadcrumb;
  // could be promoted to a real `modified_at` column if it ever needs
  // sorting. For v1, presence of "[modified]" anywhere in notes is enough.
  return profile.is_shipped === 1
    && (profile.notes || "").includes("[modified]");
}


/**
 * Build the plant-profiles editor panel.
 *
 * @param {object} opts  { ownerDocument?, fetchFn? }
 * @returns {HTMLElement}
 */
export function renderPlantProfilesEditor(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "settings-panel pp-editor";

  const head = doc.createElement("div");
  head.className = "settings-panel-head";
  head.innerHTML = "<span>Plant profiles (default tunables)</span>";
  wrap.appendChild(head);

  const blurb = doc.createElement("p");
  blurb.className = "pp-blurb";
  blurb.textContent =
    "Per-(plant_type, phase) defaults. Per-unit overrides on the " +
    "Configure tab still apply on top of these.";
  wrap.appendChild(blurb);

  const tbody = doc.createElement("div");
  tbody.className = "pp-rows";
  tbody.dataset.testid = "pp-rows";
  wrap.appendChild(tbody);

  const status = doc.createElement("div");
  status.className = "pp-status";
  status.dataset.testid = "pp-status";
  wrap.appendChild(status);

  // Track which row is open and its in-flight form state. Single-open
  // editor — opening row B collapses row A.
  let openProfileId = null;
  let openEditorEl = null;

  function _setStatus(text, kind = "info") {
    status.textContent = text;
    status.className = `pp-status ${kind}`;
  }

  function _renderRow(profile) {
    const row = doc.createElement("div");
    row.className = "pp-row";
    row.dataset.testid = `pp-row-${profile.id}`;
    row.dataset.profileId = String(profile.id);

    // Summary clickable header
    const headerEl = doc.createElement("button");
    headerEl.type = "button";
    headerEl.className = "pp-row-head";
    headerEl.dataset.testid = `pp-row-head-${profile.id}`;

    const left = doc.createElement("span");
    left.className = "pp-row-label";
    left.textContent = `${profile.plant_type} · ${profile.phase}`;
    headerEl.appendChild(left);

    const stats = doc.createElement("span");
    stats.className = "pp-row-stats";
    stats.textContent =
      `target ${profile.target_moisture_pct}% · ` +
      `kp ${profile.kp} · ${profile.min_pulse_s}-${profile.max_pulse_s}s pulse`;
    headerEl.appendChild(stats);

    if (profile.is_shipped === 1) {
      const pill = doc.createElement("span");
      pill.className = "pp-pill pp-pill-shipped";
      pill.textContent = "shipped";
      headerEl.appendChild(pill);
    }
    if (_isModified(profile)) {
      const pill = doc.createElement("span");
      pill.className = "pp-pill pp-pill-modified";
      pill.dataset.testid = `pp-pill-modified-${profile.id}`;
      pill.textContent = "modified from default";
      headerEl.appendChild(pill);
    }

    headerEl.addEventListener("click", () => {
      _toggleEditor(profile, row);
    });
    row.appendChild(headerEl);

    return row;
  }

  function _renderEditor(profile, row) {
    const editor = doc.createElement("div");
    editor.className = "pp-editor-form";
    editor.dataset.testid = `pp-editor-${profile.id}`;

    // Track edited values. Initial = current profile values (not null);
    // dirty when input changes. PUT sends only the dirty subset.
    const fieldState = {};
    for (const f of FIELDS) {
      fieldState[f.id] = {
        original: profile[f.id],
        current: profile[f.id],
        dirty: false,
      };
    }

    for (const f of FIELDS) {
      const fr = doc.createElement("div");
      fr.className = "pp-field-row";

      const lbl = doc.createElement("label");
      lbl.textContent = f.label;
      fr.appendChild(lbl);

      const inp = doc.createElement("input");
      inp.type = "number";
      inp.dataset.testid = `pp-input-${profile.id}-${f.id}`;
      inp.step = String(f.step);
      inp.min = String(f.min);
      inp.max = String(f.max);
      const v = profile[f.id];
      inp.value = v == null ? "" : String(v);
      inp.addEventListener("input", () => {
        const raw = inp.value;
        if (raw === "" || raw == null) {
          fieldState[f.id].current = null;
        } else {
          const n = Number(raw);
          fieldState[f.id].current = Number.isFinite(n) ? n : null;
        }
        fieldState[f.id].dirty = true;
      });
      fr.appendChild(inp);
      editor.appendChild(fr);
    }

    const actions = doc.createElement("div");
    actions.className = "pp-editor-actions";

    const saveBtn = doc.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "px-btn primary pp-save-btn";
    saveBtn.textContent = "Save";
    saveBtn.dataset.testid = `pp-save-${profile.id}`;

    const cancelBtn = doc.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "px-btn pp-cancel-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.dataset.testid = `pp-cancel-${profile.id}`;

    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    editor.appendChild(actions);

    const localErr = doc.createElement("div");
    localErr.className = "pp-editor-error";
    localErr.dataset.testid = `pp-error-${profile.id}`;
    editor.appendChild(localErr);

    cancelBtn.addEventListener("click", () => {
      _closeEditor();
    });

    saveBtn.addEventListener("click", async () => {
      // Build the dirty-subset PUT body
      const body = {};
      for (const f of FIELDS) {
        const st = fieldState[f.id];
        if (st.dirty && st.current != null) {
          body[f.id] = st.current;
        }
      }
      if (Object.keys(body).length === 0) {
        _setStatus("No changes to save.", "info");
        return;
      }
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
      try {
        const r = await fetchFn(`/api/grow/plant-profiles/${profile.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          const detail = err.detail || err.error || r.statusText;
          localErr.textContent = `✗ ${typeof detail === "string"
            ? detail : JSON.stringify(detail)}`;
          saveBtn.disabled = false;
          saveBtn.textContent = "Save";
          return;
        }
        _setStatus(
          `Saved ${profile.plant_type} · ${profile.phase}`,
          "ok",
        );
        // Refresh the list so the row's summary reflects new values + the
        // modified badge appears for shipped rows.
        await _reload();
      } catch (exc) {
        localErr.textContent = `✗ ${exc.message || "Network error"}`;
        saveBtn.disabled = false;
        saveBtn.textContent = "Save";
      }
    });

    return editor;
  }

  function _closeEditor() {
    if (openEditorEl && openEditorEl.parentNode) {
      openEditorEl.parentNode.removeChild(openEditorEl);
    }
    openEditorEl = null;
    openProfileId = null;
  }

  function _toggleEditor(profile, rowEl) {
    if (openProfileId === profile.id) {
      _closeEditor();
      return;
    }
    _closeEditor();
    const editor = _renderEditor(profile, rowEl);
    rowEl.appendChild(editor);
    openEditorEl = editor;
    openProfileId = profile.id;
  }

  async function _reload() {
    _setStatus("Loading…", "info");
    try {
      const r = await fetchFn("/api/grow/plant-profiles");
      if (!r.ok) {
        _setStatus(`Failed to load (${r.status})`, "err");
        return;
      }
      const profiles = await r.json();
      tbody.innerHTML = "";
      openProfileId = null;
      openEditorEl = null;
      for (const p of profiles) {
        tbody.appendChild(_renderRow(p));
      }
      _setStatus(`Loaded ${profiles.length} profiles.`, "info");
    } catch (exc) {
      _setStatus(`✗ ${exc.message || "Network error"}`, "err");
    }
  }

  // Kick off initial load on next tick so the caller can attach the
  // element to the DOM first (matters for tests that snapshot the DOM
  // before the fetch resolves).
  _reload();

  return wrap;
}

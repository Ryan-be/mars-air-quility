/**
 * Fleet filter/sort row — top of the /grow page.
 *
 * The row owns its own filter state (a Set per category + a single sort
 * key) and emits a serialised snapshot via opts.onChange whenever any
 * control changes. The parent (fleet.mjs) subscribes to that callback,
 * stores the snapshot, and re-renders the card grid using applyFilters.
 *
 * Design choices:
 *   - Phase + status chip lists are STATIC (5 phases match the server
 *     phase enum; status is a binary derived from last_seen_at). They
 *     render whether or not the fleet currently has units in each
 *     bucket, so the UI doesn't shift as units come/go.
 *   - Plant-type chip list is DYNAMIC: derived from the fleet's
 *     `unit.plant_type` values. We don't hardcode "tomato/basil/lettuce"
 *     because users add custom plant types via plant_profiles, and a
 *     stale chip list would be more confusing than helpful.
 *   - "Online" = last_seen_at within 5 minutes. This matches the
 *     server's _OFFLINE_AFTER threshold in api_grow_units.py — any
 *     telemetry within 5 min keeps the unit out of "offline" land.
 *     (Inside that 5 min the server further distinguishes online ≤ 30s
 *     vs stale 30s–5min, but for a coarse user-facing filter the
 *     online/offline binary is what matters.)
 *   - applyFilters is exported separately so tests can exercise the
 *     filtering logic without spinning up a DOM. It takes `now` as an
 *     injectable thunk so tests can pin time deterministically.
 *   - Empty filter set in a category means "no filter for that
 *     category" — i.e. show all units, not show none.
 */

const PHASES = ["seedling", "vegetative", "flowering", "fruiting", "dormant"];
const STATUSES = ["online", "offline"];
const SORTS = [
  { id: "label", label: "Label (A-Z)" },
  { id: "last_seen", label: "Last seen (recent first)" },
  { id: "moisture", label: "Moisture (driest first)" },
];
const ONLINE_WINDOW_MS = 5 * 60 * 1000;


/**
 * Apply a filter+sort state to a units list. Pure function.
 *
 * @param {Array} units  the fleet
 * @param {{phases: string[], statuses: string[], plant_types: string[], sort: string}} state
 * @param {function} now  optional clock injection for test determinism
 * @returns {Array} filtered + sorted (does not mutate input)
 */
export function applyFilters(units, state, now = () => Date.now()) {
  let result = units;

  if (state.phases && state.phases.length > 0) {
    result = result.filter((u) => state.phases.includes(u.current_phase));
  }
  if (state.statuses && state.statuses.length > 0) {
    result = result.filter((u) => {
      const lastSeen = u.last_seen_at ? new Date(u.last_seen_at).getTime() : 0;
      const isOnline = (now() - lastSeen) < ONLINE_WINDOW_MS;
      return (state.statuses.includes("online") && isOnline)
          || (state.statuses.includes("offline") && !isOnline);
    });
  }
  if (state.plant_types && state.plant_types.length > 0) {
    result = result.filter((u) => state.plant_types.includes(u.plant_type));
  }

  // Sort (always returns a new array so we don't mutate)
  if (state.sort === "label") {
    result = [...result].sort((a, b) => (a.label || "").localeCompare(b.label || ""));
  } else if (state.sort === "last_seen") {
    result = [...result].sort((a, b) => {
      const ta = a.last_seen_at ? new Date(a.last_seen_at).getTime() : 0;
      const tb = b.last_seen_at ? new Date(b.last_seen_at).getTime() : 0;
      return tb - ta;
    });
  } else if (state.sort === "moisture") {
    result = [...result].sort((a, b) => {
      // Treat missing moisture as 999 so never-seen units land at the
      // bottom of "driest first".
      const ma = a.last_known_state && a.last_known_state.soil_moisture_pct != null
        ? a.last_known_state.soil_moisture_pct : 999;
      const mb = b.last_known_state && b.last_known_state.soil_moisture_pct != null
        ? b.last_known_state.soil_moisture_pct : 999;
      return ma - mb;
    });
  }

  return result;
}


/**
 * Render the filter/sort controls.
 *
 * @param {object} opts
 *   - units: list used to derive the plant_type chip set
 *   - onChange: function called with the filter state on every change
 *   - ownerDocument: optional (defaults to global document)
 * @returns {HTMLElement}
 */
export function renderFleetFilterRow(opts) {
  const doc = (opts && opts.ownerDocument) || document;
  const units = (opts && opts.units) || [];
  const onChange = (opts && opts.onChange) || (() => {});

  const wrap = doc.createElement("div");
  wrap.className = "fleet-filter-row";
  wrap.dataset.testid = "fleet-filter-row";

  const state = {
    phases: new Set(),
    statuses: new Set(),
    plant_types: new Set(),
    sort: "label",
  };

  function emit() {
    onChange({
      phases: Array.from(state.phases),
      statuses: Array.from(state.statuses),
      plant_types: Array.from(state.plant_types),
      sort: state.sort,
    });
  }

  function _makeChip(text, datasetKey, datasetVal, set) {
    const chip = doc.createElement("button");
    chip.type = "button";
    chip.className = "fleet-filter-chip";
    chip.textContent = text;
    chip.dataset[datasetKey] = datasetVal;
    chip.addEventListener("click", () => {
      if (set.has(datasetVal)) {
        set.delete(datasetVal);
        chip.classList.remove("active");
      } else {
        set.add(datasetVal);
        chip.classList.add("active");
      }
      emit();
    });
    return chip;
  }

  function _makeGroup(labelText) {
    const group = doc.createElement("div");
    group.className = "fleet-filter-group";
    const lbl = doc.createElement("span");
    lbl.className = "fleet-filter-label";
    lbl.textContent = labelText;
    group.appendChild(lbl);
    return group;
  }

  // Phase chips
  const phaseGroup = _makeGroup("Phase");
  for (const phase of PHASES) {
    phaseGroup.appendChild(_makeChip(phase, "phase", phase, state.phases));
  }
  wrap.appendChild(phaseGroup);

  // Status chips
  const statusGroup = _makeGroup("Status");
  for (const s of STATUSES) {
    statusGroup.appendChild(_makeChip(s, "status", s, state.statuses));
  }
  wrap.appendChild(statusGroup);

  // Plant-type chips — derived from the units list so users don't see
  // chips for types not in their fleet. Sorted for stable display.
  const plantTypes = Array.from(
    new Set(units.map((u) => u.plant_type).filter(Boolean)),
  ).sort();
  if (plantTypes.length > 0) {
    const plantGroup = _makeGroup("Plant");
    for (const t of plantTypes) {
      plantGroup.appendChild(_makeChip(t, "plantType", t, state.plant_types));
    }
    wrap.appendChild(plantGroup);
  }

  // Sort dropdown
  const sortGroup = _makeGroup("Sort");
  const sortSel = doc.createElement("select");
  sortSel.className = "fleet-filter-sort";
  sortSel.dataset.testid = "fleet-filter-sort";
  for (const s of SORTS) {
    const o = doc.createElement("option");
    o.value = s.id;
    o.textContent = s.label;
    sortSel.appendChild(o);
  }
  sortSel.value = state.sort;
  sortSel.addEventListener("change", () => {
    state.sort = sortSel.value;
    emit();
  });
  sortGroup.appendChild(sortSel);
  wrap.appendChild(sortGroup);

  return wrap;
}

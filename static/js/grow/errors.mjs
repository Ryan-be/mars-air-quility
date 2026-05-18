/**
 * /grow/errors page — fleet-wide error log orchestrator.
 *
 * Layout:
 *   #grow-errors-filter  filter row: severity chips, kind dropdown,
 *                        unresolved-only toggle (default ON), Refresh
 *   #grow-errors-list    list of error-row components, or empty-state
 *
 * Filter state lives in this module. Any change refetches with a fresh
 * query string and rebuilds the list. The kind dropdown's options are
 * built from the response's distinct kinds (NOT a hardcoded list) so
 * users only see chips for kinds that exist in their fleet.
 *
 * Refresh strategy: explicit button, plus auto-refresh after every
 * successful per-row PATCH (the row dispatches `error-updated` and we
 * listen for it on the list host).
 *
 * Testability: renderErrorsPage({rows, ownerDocument, fetcher}) is
 * exposed so tests can drive the rendering with a fake fetcher and
 * inspect the DOM. The boot sequence only runs in real browser
 * contexts (auto-detected by location.protocol).
 */
import { renderErrorRow } from "./components/error-row.mjs";

const SEVERITIES = ["info", "warning", "critical"];
const DEFAULT_LIMIT = 100;

const STATE = {
  unresolvedOnly: true,
  severities: new Set(),     // empty → no severity filter
  kind: null,                 // null → no kind filter
  limit: DEFAULT_LIMIT,
};


function _buildQueryString() {
  const params = new URLSearchParams();
  // unresolved_only is always present so the server intent is unambiguous.
  params.set("unresolved_only", STATE.unresolvedOnly ? "true" : "false");
  if (STATE.severities.size > 0) {
    // Server only accepts a single severity value; if multiple are
    // selected, we OR them client-side after the fetch. For a single
    // selection we narrow the request.
    if (STATE.severities.size === 1) {
      params.set("severity", Array.from(STATE.severities)[0]);
    }
  }
  if (STATE.kind) params.set("kind", STATE.kind);
  params.set("limit", String(STATE.limit));
  return params.toString();
}


function _applyClientSideFilters(rows) {
  let result = rows;
  // Multi-severity: server only narrows to a single severity, so when
  // 2+ chips are active we OR them client-side.
  if (STATE.severities.size > 1) {
    result = result.filter((r) => STATE.severities.has(r.severity));
  }
  return result;
}


/**
 * Build the filter row DOM. Owns its widgets but writes shared filter
 * state into module-level STATE and triggers a refetch via opts.refetch.
 */
function _renderFilterRow(opts) {
  const doc = opts.ownerDocument;
  const knownKinds = opts.kinds || [];
  const refetch = opts.refetch;

  const wrap = doc.createElement("div");
  wrap.className = "fleet-filter-row grow-errors-filter-row";
  wrap.dataset.testid = "grow-errors-filter-row";

  // Severity chips
  const sevGroup = doc.createElement("div");
  sevGroup.className = "fleet-filter-group";
  const sevLbl = doc.createElement("span");
  sevLbl.className = "fleet-filter-label";
  sevLbl.textContent = "Severity";
  sevGroup.appendChild(sevLbl);
  for (const sev of SEVERITIES) {
    const chip = doc.createElement("button");
    chip.type = "button";
    chip.className = "fleet-filter-chip";
    chip.textContent = sev;
    chip.dataset.severity = sev;
    chip.dataset.testid = `grow-errors-sev-chip-${sev}`;
    if (STATE.severities.has(sev)) chip.classList.add("active");
    chip.addEventListener("click", () => {
      if (STATE.severities.has(sev)) {
        STATE.severities.delete(sev);
        chip.classList.remove("active");
      } else {
        STATE.severities.add(sev);
        chip.classList.add("active");
      }
      refetch();
    });
    sevGroup.appendChild(chip);
  }
  wrap.appendChild(sevGroup);

  // Kind dropdown — built from the current response's distinct kinds.
  const kindGroup = doc.createElement("div");
  kindGroup.className = "fleet-filter-group";
  const kindLbl = doc.createElement("span");
  kindLbl.className = "fleet-filter-label";
  kindLbl.textContent = "Kind";
  kindGroup.appendChild(kindLbl);
  const kindSel = doc.createElement("select");
  kindSel.className = "fleet-filter-sort";
  kindSel.dataset.testid = "grow-errors-kind-select";
  const allOpt = doc.createElement("option");
  allOpt.value = "";
  allOpt.textContent = "All kinds";
  kindSel.appendChild(allOpt);
  for (const k of knownKinds) {
    const opt = doc.createElement("option");
    opt.value = k;
    opt.textContent = k;
    kindSel.appendChild(opt);
  }
  if (STATE.kind) kindSel.value = STATE.kind;
  kindSel.addEventListener("change", () => {
    STATE.kind = kindSel.value || null;
    refetch();
  });
  kindGroup.appendChild(kindSel);
  wrap.appendChild(kindGroup);

  // Unresolved-only toggle
  const togGroup = doc.createElement("div");
  togGroup.className = "fleet-filter-group";
  const togLbl = doc.createElement("label");
  togLbl.className = "fleet-filter-label grow-errors-toggle-label";
  const cb = doc.createElement("input");
  cb.type = "checkbox";
  cb.dataset.testid = "grow-errors-unresolved-toggle";
  cb.checked = STATE.unresolvedOnly;
  cb.addEventListener("change", () => {
    STATE.unresolvedOnly = cb.checked;
    refetch();
  });
  togLbl.appendChild(cb);
  togLbl.appendChild(doc.createTextNode(" Unresolved only"));
  togGroup.appendChild(togLbl);
  wrap.appendChild(togGroup);

  // Refresh button
  const refreshBtn = doc.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "px-btn primary grow-errors-refresh";
  refreshBtn.dataset.testid = "grow-errors-refresh";
  refreshBtn.textContent = "Refresh";
  refreshBtn.addEventListener("click", () => refetch());
  wrap.appendChild(refreshBtn);

  return wrap;
}


/**
 * Render the empty-state when zero errors come back.
 */
function _renderEmptyState(doc) {
  const el = doc.createElement("div");
  el.className = "grow-errors-empty";
  el.dataset.testid = "grow-errors-empty";
  el.textContent = STATE.unresolvedOnly
    ? "No unresolved errors. Toggle off \"Unresolved only\" to see resolved history."
    : "No errors match the current filter.";
  return el;
}


/**
 * Top-level renderer. Pure DOM build over the supplied rows.
 *
 * @param {object} opts
 *   - rows: response rows (already fetched by caller)
 *   - ownerDocument: defaults to global document
 *   - refetch: thunk called when any filter widget changes (caller wires
 *     this to a real fetch + re-render).
 *   - now: optional clock thunk (test injection for snooze rendering)
 */
export function renderErrorsPage(opts = {}) {
  const doc = opts.ownerDocument || document;
  const rows = opts.rows || [];
  const refetch = opts.refetch || (() => {});

  const filterHost = doc.getElementById("grow-errors-filter");
  const listHost = doc.getElementById("grow-errors-list");
  if (!filterHost || !listHost) return;

  // Distinct kinds from current response, sorted for stable display.
  const kinds = Array.from(new Set(rows.map((r) => r.kind).filter(Boolean))).sort();

  filterHost.innerHTML = "";
  filterHost.appendChild(_renderFilterRow({
    ownerDocument: doc,
    kinds,
    refetch,
  }));

  listHost.innerHTML = "";
  const filtered = _applyClientSideFilters(rows);
  if (filtered.length === 0) {
    listHost.appendChild(_renderEmptyState(doc));
    return;
  }
  for (const row of filtered) {
    listHost.appendChild(renderErrorRow(row, { ownerDocument: doc, now: opts.now }));
  }
}


/**
 * Build the query string used by the GET endpoint. Exported for tests.
 */
export function buildQueryString() {
  return _buildQueryString();
}


/**
 * Return the current filter state. Exported for tests.
 */
export function getFilterState() {
  return {
    unresolvedOnly: STATE.unresolvedOnly,
    severities: Array.from(STATE.severities),
    kind: STATE.kind,
    limit: STATE.limit,
  };
}


/**
 * Reset filter state to defaults. Test helper.
 */
export function resetState() {
  STATE.unresolvedOnly = true;
  STATE.severities = new Set();
  STATE.kind = null;
  STATE.limit = DEFAULT_LIMIT;
}


async function _fetchAndRender(doc, fetcher) {
  const qs = _buildQueryString();
  let rows = [];
  try {
    const r = await fetcher(`/api/grow/errors?${qs}`);
    if (r && r.ok) rows = await r.json();
  } catch (e) {
    console.error("fetch errors failed", e);
  }
  renderErrorsPage({
    rows,
    ownerDocument: doc,
    refetch: () => _fetchAndRender(doc, fetcher),
  });
}


/**
 * Wire up the page: initial fetch + listener for `error-updated`.
 * Exposed (rather than auto-running on import) so tests can import
 * the module without spinning up a real fetch.
 */
export function boot(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetcher = opts.fetcher || ((url, init) => fetch(url, init));

  // Per-row PATCH triggers a refetch via the bubbling custom event.
  const listHost = doc.getElementById("grow-errors-list");
  if (listHost) {
    listHost.addEventListener("error-updated", () => {
      _fetchAndRender(doc, fetcher);
    });
  }
  _fetchAndRender(doc, fetcher);
}


// Auto-boot in real browser contexts only.
if (typeof window !== "undefined"
    && /^https?:$/.test(window.location.protocol)
    && document.getElementById("grow-errors-list")) {
  boot();
}

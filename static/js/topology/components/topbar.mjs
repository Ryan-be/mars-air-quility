/**
 * Telemetry topbar component (Phase 7 Task 7.1).
 *
 * The chrome row stamped into `#tp-topbar-host` at boot. Layout:
 *
 *   <header class="tp-topbar-inner">
 *     <div class="tp-brand">MLSS · NODE MAP</div>
 *
 *     <div class="tp-stat">…Hub Status pill / nominal…</div>
 *     <div class="tp-stat">…Grows: 2…</div>
 *     <div class="tp-stat">…Effectors: 4…</div>
 *     <div class="tp-stat">…Active: 1…</div>
 *     <div class="tp-stat">…Auto vs Forced: 3 / 1…</div>
 *
 *     <button data-action="rearrange">⟲ Re-arrange</button>
 *     <button data-action="recenter">⌖ Recenter</button>
 *     <button data-action="add-effector">+ Add effector</button>   ← admin
 *   </header>
 *
 * Pure renderer — never opens an SSE channel, never starts a setInterval.
 *
 * Mission Time was originally the leftmost cell (T+HH:MM:SS relative
 * to service start) but operator feedback flagged it as decorative
 * noise. Removed entirely — the topbar shows 5 stat cells now.
 *
 * The `+ Add effector` button is admin-gated client-side (the gate
 * mirrors the server-side `@require_role("admin")` on POST
 * /api/effectors). Hiding the button keeps non-admins from getting a
 * 403 on click; defence in depth.
 */


function _renderStatCell(doc, labelText, valueText, valueRole) {
  const cell = doc.createElement("div");
  cell.className = "tp-stat";

  const label = doc.createElement("span");
  label.className = "tp-stat-label";
  label.textContent = labelText;
  cell.appendChild(label);

  const value = doc.createElement("span");
  value.className = "tp-stat-value";
  value.textContent = valueText;
  // Optional data-role lets the boot's SSE handlers target a single
  // cell's text without re-rendering the whole topbar. Phase 10 uses
  // this for the "Hub Status" cell that follows health_update events.
  if (valueRole) value.dataset.role = valueRole;
  cell.appendChild(value);
  return cell;
}


function _renderActionButton(doc, action, label, onClick) {
  const btn = doc.createElement("button");
  btn.type = "button";
  btn.className = "tp-topbar-btn";
  btn.dataset.action = action;
  btn.textContent = label;
  btn.addEventListener("click", () => {
    if (typeof onClick === "function") onClick();
  });
  return btn;
}


/**
 * Render the topology telemetry topbar.
 *
 * @param {object} opts
 * @param {object} opts.stats         Result of `computeStats(nodes)`.
 * @param {boolean} opts.isAdmin      Show the `+ Add effector` button when true.
 * @param {Function} opts.onRearrange Click handler for the ⟲ button.
 * @param {Function} opts.onRecenter  Click handler for the ⌖ button.
 * @param {Function} opts.onAddEffector Click handler for the + button.
 * @param {Document} [opts.doc=document] Owner document (tests pass JSDOM).
 * @returns {HTMLElement} The `<header class="tp-topbar-inner">` element.
 */
export function renderTopbar({
  stats,
  isAdmin = false,
  onRearrange = () => {},
  onRecenter = () => {},
  onAddEffector = () => {},
  doc = document,
} = {}) {
  const root = doc.createElement("header");
  root.className = "tp-topbar-inner";

  // ── Left: brand cell ────────────────────────────────────────────────
  const brand = doc.createElement("div");
  brand.className = "tp-brand";
  // Middle-dot separator matches the prototype + `templates/base.html`'s
  // gsb-app-name "MLSS" treatment. Plain ASCII would render as a hyphen
  // and break the design intent.
  brand.textContent = "MLSS · NODE MAP";
  root.appendChild(brand);

  // ── Middle: 5 telemetry cells ───────────────────────────────────────
  // (Mission Time cell removed per operator feedback — decorative noise.)

  // Hub Status — derived label rather than a numeric. The boot
  // orchestrator's `onHealthUpdate` SSE handler targets the
  // `data-role="hub-status"` value cell so a live health flip lands
  // here without re-rendering the whole topbar.
  root.appendChild(_renderStatCell(doc, "Hub Status", "Nominal",
    "hub-status"));

  // Pure node-count rollups from computeStats(nodes).
  root.appendChild(_renderStatCell(doc, "Grows",
    String(stats.grows ?? 0)));
  root.appendChild(_renderStatCell(doc, "Effectors",
    String(stats.effectors ?? 0)));
  root.appendChild(_renderStatCell(doc, "Active",
    String(stats.active ?? 0)));
  // Auto vs Forced is the only compound cell. Format "N / M" so the
  // operator can scan auto vs override ratio at a glance.
  root.appendChild(_renderStatCell(doc, "Auto vs Forced",
    `${stats.auto ?? 0} / ${stats.forced ?? 0}`));

  // ── Right: action buttons ──────────────────────────────────────────
  // Spacer pushes the buttons to the right edge. Display:flex on the
  // root + margin-left:auto on the first action makes this a one-cell
  // job rather than requiring a wrapper div.
  const actions = doc.createElement("div");
  actions.className = "tp-topbar-actions";
  root.appendChild(actions);

  // ⟲ Re-arrange — always visible. The icon glyph is a U+27F2 anticlockwise
  // gapped circle arrow; the textContent reads "⟲ Re-arrange" so screen
  // readers pick up the verb.
  actions.appendChild(_renderActionButton(
    doc, "rearrange", "⟲ Re-arrange", onRearrange,
  ));
  // ⌖ Recenter — U+2316 position indicator glyph.
  actions.appendChild(_renderActionButton(
    doc, "recenter", "⌖ Recenter", onRecenter,
  ));
  // + Add effector — admin only. Server-side @require_role("admin")
  // already gates POST /api/effectors; hiding the button is purely
  // visual reveal.
  if (isAdmin) {
    actions.appendChild(_renderActionButton(
      doc, "add-effector", "+ Add effector", onAddEffector,
    ));
  }
  return root;
}

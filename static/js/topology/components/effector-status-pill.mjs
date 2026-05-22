/**
 * Effector on/off/auto/fault status pill (Phase 6 Task 6.2).
 *
 * Port of `docs/assets/effector-map-handoff/nodes.jsx::StatusPill`.
 * Renders a small `<span class="tp-pill tp-pill-<state>">` with a
 * leading dot and the state label. Two visual variants:
 *
 *   * Outlined (default): transparent fill, border + text in the
 *     state colour. Used for `off` / `auto` / `fault`.
 *   * Solid (`{solid: true}`): filled with the state colour, dark
 *     text. Used for `on` to make the active state pop visually.
 *
 * Filename is `effector-status-pill.mjs` (not `status-pill.mjs`) to
 * avoid a name collision with `static/js/grow/components/status-pill.mjs`
 * — that one renders the grow-unit online/stale/offline pill and has
 * completely different semantics.
 */


/**
 * @param {object} args
 * @param {"on"|"off"|"auto"|"fault"} args.state Drives the colour class.
 * @param {string} [args.label]    Display text. Defaults to state.
 * @param {boolean} [args.solid]   Solid-fill variant.
 * @param {Document} [args.ownerDocument]
 * @returns {HTMLSpanElement}
 */
export function renderEffectorStatusPill({ state, label, solid, ownerDocument }) {
  const doc = ownerDocument || document;
  const span = doc.createElement("span");
  const classes = ["tp-pill", `tp-pill-${state}`];
  if (solid) classes.push("tp-pill-solid");
  span.className = classes.join(" ");
  const ball = doc.createElement("i");
  ball.className = "tp-pill-ball";
  span.appendChild(ball);
  const text = doc.createElement("span");
  text.className = "tp-pill-label";
  text.textContent = (label || state || "").toString().toUpperCase();
  span.appendChild(text);
  return span;
}

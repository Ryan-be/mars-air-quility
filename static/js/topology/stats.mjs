/**
 * Topology telemetry stats (Phase 7 Task 7.2).
 *
 * Pure rollup driving the topbar's 4 numeric cells. The boot
 * orchestrator builds the flat node array via `_flattenTopology()`,
 * pipes it through `computeStats()`, and hands the result to
 * `renderTopbar({stats, ...})`.
 *
 * No DOM. No side effects. The function is the single canonical
 * "what's in this topology right now" answer — keeping it in its own
 * module means the topbar component test can drive it without pulling
 * in any of the renderer footprint.
 *
 * Definitions (from the plan):
 *   * total      — every node, including the hub.
 *   * grows      — nodes whose `kind === "grow"`.
 *   * effectors  — nodes whose `kind === "effector"`.
 *   * active     — effectors with `current_state === "on"` (i.e.
 *                  physically energised), regardless of auto vs
 *                  forced mode.
 *   * auto       — effectors with `mode === "auto"` (back to rule-driven
 *                  control after a Recover-from-Override).
 *   * forced     — effectors with `mode !== "auto"` (operator has
 *                  pinned them ON or OFF). Missing mode counts as
 *                  forced — it means the row pre-dates auto_mode and
 *                  the evaluator can't have promoted it.
 */


/**
 * Compute the telemetry rollup for the topbar.
 *
 * @param {Array<object>} nodes Flat node list from `_flattenTopology()`.
 * @returns {{
 *   total: number,
 *   active: number,
 *   grows: number,
 *   effectors: number,
 *   auto: number,
 *   forced: number,
 * }}
 */
export function computeStats(nodes) {
  // A non-array (or missing argument) gracefully degrades to all-zeroes;
  // the cards-driven boot path can be invoked before /api/topology
  // resolves, in which case nodes is undefined.
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return {
      total: 0,
      active: 0,
      grows: 0,
      effectors: 0,
      auto: 0,
      forced: 0,
    };
  }

  let grows = 0;
  let effectors = 0;
  let active = 0;
  let auto = 0;
  let forced = 0;

  for (const n of nodes) {
    if (n.kind === "grow") {
      grows += 1;
    } else if (n.kind === "effector") {
      effectors += 1;
      if (n.current_state === "on") active += 1;
      if (n.mode === "auto") {
        auto += 1;
      } else {
        // Either mode==="on" / "off" (explicit forced) OR mode missing.
        // Roll missing into forced — see module docstring rationale.
        forced += 1;
      }
    }
    // Hub + any future kind contribute to `total` only.
  }

  return {
    total: nodes.length,
    active,
    grows,
    effectors,
    auto,
    forced,
  };
}

/**
 * History tab orchestrator — Task 5 of the History-tab plan.
 *
 * Composes the long-range moisture chart (Task 3) and the photo
 * timelapse scrubber (Task 4) into a single panel so the unit_detail
 * tab switcher has one element to mount on tab activation.
 *
 * Intentionally thin: no business logic, no state, no fetches of its
 * own. Both children read from the same `unit` object so they share
 * the same id (for URL construction) and the same overrides context
 * (the chart uses `unit.overrides.watering_target` for the dashed
 * target line — see moisture-history-chart.mjs).
 *
 * The `data-testid="history-panel"` on the wrap lets tests assert the
 * panel mounted without coupling to className changes.
 */
import { renderMoistureHistoryChart } from "./moisture-history-chart.mjs";
import { renderPhotoTimelapse } from "./photo-timelapse.mjs";


/**
 * Build the History tab body.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `id`
 *                       and `overrides`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderHistoryPanel(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.dataset.testid = "history-panel";
  wrap.appendChild(renderMoistureHistoryChart(unit, opts));
  wrap.appendChild(renderPhotoTimelapse(unit, opts));
  return wrap;
}

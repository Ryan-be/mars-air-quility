/**
 * History tab orchestrator — Task 5 of the History-tab plan.
 *
 * Composes the long-range moisture chart (Task 3), the photo
 * timelapse scrubber (Task 4), and the plant journal editor
 * (Phase 4 #7) into a single panel so the unit_detail tab switcher
 * has one element to mount on tab activation.
 *
 * Intentionally thin: no business logic, no state, no fetches of its
 * own. Children read from the same `unit` object so they share the
 * same id (for URL construction) and the same overrides context
 * (the chart uses `unit.overrides.watering_target` for the dashed
 * target line — see moisture-history-chart.mjs).
 *
 * The journal editor emits a `journal-changed` CustomEvent after any
 * successful CRUD; we don't currently re-render the chart/timelapse
 * here in response (markers are a v2 enhancement). The event still
 * bubbles so external listeners (e.g. a future TOC sidebar) can hook
 * in without changes here.
 *
 * The `data-testid="history-panel"` on the wrap lets tests assert the
 * panel mounted without coupling to className changes.
 */
import { renderMoistureHistoryChart } from "./moisture-history-chart.mjs";
import { renderPhotoTimelapse } from "./photo-timelapse.mjs";
import { renderJournalEditor } from "./journal-editor.mjs";
import { renderTimelapseGenerator } from "./timelapse-generator.mjs";


function _readSessionRole(doc) {
  // Pages we mount on stamp role+user onto body.dataset (see
  // templates/grow_unit_detail.html). Default to viewer if absent
  // (test environment, or a logged-out fall-through).
  const body = doc.body;
  return (body && body.dataset && body.dataset.role) || "viewer";
}


function _readSessionUser(doc) {
  const body = doc.body;
  return (body && body.dataset && body.dataset.user) || "";
}


/**
 * Build the History tab body.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `id`
 *                       and `overrides`)
 * @param {object} opts  { ownerDocument?, currentUser?, currentRole?, fetchFn? }
 * @returns {HTMLElement}
 */
export function renderHistoryPanel(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.dataset.testid = "history-panel";
  wrap.appendChild(renderMoistureHistoryChart(unit, opts));
  wrap.appendChild(renderPhotoTimelapse(unit, opts));

  const sessionRole = opts.currentRole ?? _readSessionRole(doc);
  const sessionUser = opts.currentUser ?? _readSessionUser(doc);

  const journalOpts = {
    ownerDocument: doc,
    currentUser: sessionUser,
    currentRole: sessionRole,
    fetchFn: opts.fetchFn,
  };
  wrap.appendChild(renderJournalEditor(unit, journalOpts));

  const tlapseOpts = {
    ownerDocument: doc,
    currentRole: sessionRole,
    fetchFn: opts.fetchFn,
  };
  wrap.appendChild(renderTimelapseGenerator(unit, tlapseOpts));
  return wrap;
}

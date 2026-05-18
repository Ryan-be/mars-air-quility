/**
 * Diagnostics tab orchestrator — Phase 3 Task 4.
 *
 * Composes five child sections fetched from a single consolidated
 * endpoint (Phase 3 Task 3 added the endpoint):
 *
 *   - firmware-info: version + uptime + buffer size
 *   - buffer-inspector: per-msg_type counts + bytes + age (Phase 3
 *                       follow-up: WHAT is queued, not just count)
 *   - connection-log: 20 most recent online/offline events
 *   - sensor-sanity: per-capability staleness
 *   - danger-zone: token rotation + decommission + clear remote buffer
 *
 * Mirrors the `history-panel.mjs` orchestrator pattern — thin shell, no
 * business logic, but here we DO own the single fetch (the children are
 * pure render funcs that take pre-fetched data slices). The fetch lives
 * here rather than in each child so we don't fan out four round-trips
 * on tab activation.
 *
 * Async: returns a Promise<HTMLElement>. The unit_detail tab switcher
 * calls `await renderDiagnosticsPanel(unit)` like it does for History,
 * so the body element is fully built before mount and the operator
 * doesn't see a partially-rendered placeholder while the fetch races.
 *
 * Failure surface: a failed fetch shows "Failed to load diagnostics"
 * inside the panel. Children render normally on success — if a SLICE
 * is empty (e.g. no connection events yet) each child renders its own
 * "no data" message; this orchestrator doesn't gate per-section.
 */
import { renderFirmwareInfo } from "./firmware-info.mjs";
import { renderBufferInspector } from "./buffer-inspector.mjs";
import { renderConnectionLog } from "./connection-log.mjs";
import { renderSensorSanity } from "./sensor-sanity.mjs";
import { renderDangerZone } from "./danger-zone.mjs";


/**
 * Build the Diagnostics tab body.
 *
 * @param {object} unit  GET /api/grow/units/<id> response — the orchestrator
 *                       reads `id` (URL construction) + `label`
 *                       (decommission confirm copy).
 * @param {object} opts  { ownerDocument?, fetchFn? }
 * @returns {Promise<HTMLElement>}
 */
export async function renderDiagnosticsPanel(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.dataset.testid = "diagnostics-panel";

  let data;
  try {
    const r = await fetchFn(`/api/grow/units/${unit.id}/diagnostics`);
    if (!r.ok) {
      const err = doc.createElement("p");
      err.className = "diag-error";
      err.dataset.testid = "diag-error";
      err.textContent = "Failed to load diagnostics";
      wrap.appendChild(err);
      // Even on fetch failure, we still mount the danger-zone — those
      // actions don't depend on the diagnostics payload (decommission
      // + clear-buffer + token rotation use the unit object directly).
      // This way an admin can still recover from a unit that's failing
      // the diagnostics fetch (e.g. soft-deleted, broken row).
      wrap.appendChild(renderDangerZone(unit, opts));
      return wrap;
    }
    data = await r.json();
  } catch (exc) {
    const err = doc.createElement("p");
    err.className = "diag-error";
    err.dataset.testid = "diag-error";
    err.textContent = `Failed to load diagnostics: ${exc.message || exc}`;
    wrap.appendChild(err);
    wrap.appendChild(renderDangerZone(unit, opts));
    return wrap;
  }

  wrap.appendChild(renderFirmwareInfo(data, opts));
  // Buffer inspector mounts between firmware-info and connection-log:
  // it's a deeper drill-down on the buffer-size field shown in the
  // firmware card, so adjacency keeps the related info together.
  wrap.appendChild(renderBufferInspector(data, opts));
  wrap.appendChild(renderConnectionLog(data.connection_log, opts));
  wrap.appendChild(renderSensorSanity(data.sensor_sanity, opts));
  wrap.appendChild(renderDangerZone(unit, opts));

  return wrap;
}

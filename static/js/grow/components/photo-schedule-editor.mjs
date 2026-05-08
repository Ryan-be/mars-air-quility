/**
 * Photo capture schedule editor — sixth panel of the Configure tab.
 *
 * Two states:
 *   * "Capture 24/7" (default; both hours null) — checkbox checked,
 *     hour selectors disabled. Photos taken at the firmware's normal
 *     interval (default 30 min) regardless of time-of-day.
 *   * "Capture between [start] and [end]" — checkbox unchecked, two
 *     hour selectors active (0..23). Wraps midnight when start > end.
 *
 * Wire-up:
 *   PUT /api/grow/units/<id>/photo_schedule
 *     body: {start_hour: int|null, end_hour: int|null}
 *     - Both null  ⇒ 24/7
 *     - Both set   ⇒ window
 *     - Only one set ⇒ 400 invalid_payload (server contract)
 *
 * Why a UI editor rather than YAML on the Pi: operators set the
 * watering schedule, plant phase, etc. through the UI; gating photo
 * capture is the same shape of decision and belongs in the same place.
 *
 * Default-on-load: read `unit.photo_schedule` from the GET response
 * (`{start_hour, end_hour}`); both null ⇒ start the checkbox checked
 * with hour selectors at the (greyed-out) defaults of 6 and 22 — those
 * pre-fill values shown if the operator unchecks 24/7, so they don't
 * have to start from 00..00.
 */

const PRELOAD_DEFAULT_START = 6;
const PRELOAD_DEFAULT_END = 22;


/**
 * Build the photo-schedule editor panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response — needs `id`
 *                       and `photo_schedule.{start_hour, end_hour}`.
 * @param {object} opts  { ownerDocument?, fetchFn? }
 * @returns {HTMLElement}
 */
export function renderPhotoScheduleEditor(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "du-panel ps-panel";
  wrap.dataset.testid = "photo-schedule-editor";

  // ── header
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📷 Photo capture schedule</span>";
  wrap.appendChild(head);

  // ── body
  const body = doc.createElement("div");
  body.className = "ps-body";
  wrap.appendChild(body);

  // Helper text — one sentence, no jargon. Operators don't care
  // about UTC vs local; just the practical effect.
  const blurb = doc.createElement("p");
  blurb.className = "ps-blurb";
  blurb.textContent =
    "When should the camera take photos? Default is 24/7 — set a window " +
    "if you want to skip overnight or pause around feeding times. Photo " +
    "interval (every ~30 min) is fixed in firmware.";
  body.appendChild(blurb);

  // ── 24/7 checkbox
  const initialStart = unit.photo_schedule?.start_hour ?? null;
  const initialEnd = unit.photo_schedule?.end_hour ?? null;
  const initial24x7 = (initialStart === null && initialEnd === null);

  const cbRow = doc.createElement("label");
  cbRow.className = "ps-247-row";
  const cb = doc.createElement("input");
  cb.type = "checkbox";
  cb.dataset.testid = "ps-247-checkbox";
  cb.checked = initial24x7;
  const cbText = doc.createElement("span");
  cbText.textContent = " Capture 24/7 (no schedule)";
  cbRow.appendChild(cb);
  cbRow.appendChild(cbText);
  body.appendChild(cbRow);

  // ── hour selectors row (start + end)
  const hourRow = doc.createElement("div");
  hourRow.className = "ps-hour-row";

  function _buildHourSelect(testid, label, current) {
    const wrap = doc.createElement("label");
    wrap.className = "ps-hour-cell";
    const span = doc.createElement("span");
    span.textContent = label;
    const sel = doc.createElement("select");
    sel.dataset.testid = testid;
    for (let h = 0; h < 24; h++) {
      const opt = doc.createElement("option");
      opt.value = String(h);
      opt.textContent = String(h).padStart(2, "0") + ":00";
      sel.appendChild(opt);
    }
    sel.value = String(current);
    wrap.appendChild(span);
    wrap.appendChild(sel);
    return { wrap, sel };
  }

  const startCtrl = _buildHourSelect(
    "ps-start-hour",
    "Start capturing at: ",
    initialStart ?? PRELOAD_DEFAULT_START,
  );
  const endCtrl = _buildHourSelect(
    "ps-end-hour",
    "Stop capturing at: ",
    initialEnd ?? PRELOAD_DEFAULT_END,
  );

  hourRow.appendChild(startCtrl.wrap);
  hourRow.appendChild(endCtrl.wrap);
  body.appendChild(hourRow);

  // ── save row
  const saveRow = doc.createElement("div");
  saveRow.className = "ps-save-row";
  const saveBtn = doc.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "px-btn primary";
  saveBtn.dataset.testid = "ps-save-btn";
  saveBtn.textContent = "Save";
  saveRow.appendChild(saveBtn);

  const statusEl = doc.createElement("span");
  statusEl.className = "ps-status";
  statusEl.dataset.testid = "ps-status";
  saveRow.appendChild(statusEl);

  body.appendChild(saveRow);

  // ── enable/disable hour selectors based on the checkbox
  function _syncHourEnabled() {
    const disabled = cb.checked;
    startCtrl.sel.disabled = disabled;
    endCtrl.sel.disabled = disabled;
    hourRow.classList.toggle("ps-hour-row-disabled", disabled);
  }
  _syncHourEnabled();
  cb.addEventListener("change", _syncHourEnabled);

  // ── save handler
  saveBtn.addEventListener("click", async () => {
    statusEl.textContent = "";
    statusEl.className = "ps-status";

    let startHour = null;
    let endHour = null;
    if (!cb.checked) {
      startHour = Number(startCtrl.sel.value);
      endHour = Number(endCtrl.sel.value);
      if (startHour === endHour) {
        statusEl.textContent =
          "✗ Start and stop hours must differ (or check 24/7).";
        statusEl.className = "ps-status err";
        return;
      }
    }

    saveBtn.disabled = true;
    const old = saveBtn.textContent;
    saveBtn.textContent = "Saving…";
    try {
      const r = await fetchFn(`/api/grow/units/${unit.id}/photo_schedule`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_hour: startHour, end_hour: endHour }),
      });
      if (r.ok) {
        statusEl.textContent = cb.checked
          ? "✓ Saved — capturing 24/7."
          : `✓ Saved — capturing ${String(startHour).padStart(2, "0")}:00–` +
            `${String(endHour).padStart(2, "0")}:00 UTC.`;
        statusEl.className = "ps-status ok";
      } else {
        let msg;
        if (r.status === 403) {
          msg = "Forbidden — controller or admin role required.";
        } else if (r.status === 404) {
          msg = "Unit not found.";
        } else {
          const err = await r.json().catch(() => ({}));
          msg = err.error || r.statusText || "Save failed";
        }
        statusEl.textContent = `✗ ${msg}`;
        statusEl.className = "ps-status err";
      }
    } catch (exc) {
      statusEl.textContent = `✗ ${exc.message || "Network error"}`;
      statusEl.className = "ps-status err";
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = old;
    }
  });

  return wrap;
}

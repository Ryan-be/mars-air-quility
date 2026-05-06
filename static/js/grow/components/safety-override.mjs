/**
 * Safety override — fifth panel of the Configure tab.
 *
 * Intentional-friction admin path. The 3-clicks-in-5s confirmation is
 * the core: clicking the big red button arms it, the second click moves
 * to "Confirm 2/3", and only the third click within 5s of the first
 * fires POST /api/grow/units/<id>/safety_override. Five seconds with no
 * third click resets the FSM back to idle.
 *
 * Five actions match the server's _SAFETY_ACTION Literal:
 *   force_pump_on, force_pump_off, force_light_on, force_light_off,
 *   skip_next_soak.
 *
 * Why three clicks rather than a confirm() dialog: a single confirm()
 * is one keypress (Enter) away from accidental fire. Three explicit
 * clicks within a tight window force a deliberate motor pattern that's
 * very hard to do by accident.
 *
 * Acknowledged warnings: the contracts schema accepts a list of warning
 * codes the user has acknowledged; for Task 7 we attach a single
 * action-specific warning code (pump_safety / light_safety / soak) so
 * the audit row in grow_errors records what the user agreed to. A
 * future enhancement could surface checkboxes the user must tick.
 */

const FSM_TIMEOUT_MS = 5000;

const ACTIONS = [
  { value: "force_pump_on",   label: "Force pump ON" },
  { value: "force_pump_off",  label: "Force pump OFF" },
  { value: "force_light_on",  label: "Force light ON" },
  { value: "force_light_off", label: "Force light OFF" },
  { value: "skip_next_soak",  label: "Skip next soak window" },
];


function _warningCodesFor(action) {
  if (action === "force_pump_on" || action === "force_pump_off") {
    return ["pump_safety"];
  }
  if (action === "force_light_on" || action === "force_light_off") {
    return ["light_safety"];
  }
  return ["soak_safety"];
}


/**
 * Build the safety-override panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderSafetyOverride(unit, opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-safety";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🚨 Safety override</span>";
  wrap.appendChild(head);

  const form = doc.createElement("div");
  form.className = "cfg-form";
  wrap.appendChild(form);

  // Action picker
  const actionRow = doc.createElement("div");
  actionRow.className = "cfg-row";
  const actionLbl = doc.createElement("label");
  actionLbl.textContent = "Action";
  actionRow.appendChild(actionLbl);
  const actionSel = doc.createElement("select");
  actionSel.className = "cfg-safety-action-select";
  actionSel.dataset.testid = "safety-action";
  for (const a of ACTIONS) {
    const o = doc.createElement("option");
    o.value = a.value;
    o.textContent = a.label;
    actionSel.appendChild(o);
  }
  actionRow.appendChild(actionSel);
  form.appendChild(actionRow);

  // Duration
  const durRow = doc.createElement("div");
  durRow.className = "cfg-row";
  const durLbl = doc.createElement("label");
  durLbl.textContent = "Duration (s)";
  durRow.appendChild(durLbl);
  const durInput = doc.createElement("input");
  durInput.type = "number";
  durInput.min = "0";
  durInput.max = "300";  // matches SafetyOverrideRequest.duration_s le=300
  durInput.step = "1";
  durInput.value = "10";
  durInput.className = "cfg-safety-duration";
  durInput.dataset.testid = "safety-duration";
  durRow.appendChild(durInput);
  form.appendChild(durRow);

  // The big red button + status
  const actions = doc.createElement("div");
  actions.className = "cfg-row cfg-safety-actions";

  const button = doc.createElement("button");
  button.type = "button";
  button.className = "cfg-safety-btn";
  const INITIAL_LABEL = "🚨 Override";
  button.textContent = INITIAL_LABEL;
  button.dataset.testid = "safety-button";
  actions.appendChild(button);

  const status = doc.createElement("span");
  status.className = "cfg-status";
  status.dataset.testid = "safety-status";
  actions.appendChild(status);

  form.appendChild(actions);

  // FSM state. clickCount counts deliberate confirmations (1..3). resetTimer
  // is the setTimeout id; it fires FSM_TIMEOUT_MS after the first click in a
  // sequence and reverts to idle if not all 3 clicks have happened by then.
  const fsm = {
    clickCount: 0,
    resetTimer: null,
  };

  function _reset() {
    fsm.clickCount = 0;
    if (fsm.resetTimer != null) {
      clearTimeout(fsm.resetTimer);
      fsm.resetTimer = null;
    }
    button.textContent = INITIAL_LABEL;
    button.classList.remove("armed");
  }

  async function _fire() {
    const body = {
      action: actionSel.value,
      duration_s: Number(durInput.value),
      acknowledged_warnings: _warningCodesFor(actionSel.value),
    };
    button.disabled = true;
    button.textContent = "Sending…";
    status.textContent = "";
    status.className = "cfg-status";
    try {
      const r = await fetch(`/api/grow/units/${unit.id}/safety_override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.status === 202 || r.ok) {
        status.textContent = `✓ Override sent (${body.duration_s}s)`;
        status.className = "cfg-status ok";
      } else if (r.status === 503) {
        status.textContent = "✗ Unit offline — try again when reconnected";
        status.className = "cfg-status err";
      } else {
        const err = await r.json().catch(() => ({}));
        const msg = err.error || err.detail || r.statusText || "Override failed";
        status.textContent = `✗ ${msg}`;
        status.className = "cfg-status err";
      }
    } catch (exc) {
      status.textContent = `✗ ${exc.message || "Network error"}`;
      status.className = "cfg-status err";
    } finally {
      button.disabled = false;
      _reset();
    }
  }

  button.addEventListener("click", () => {
    fsm.clickCount += 1;
    if (fsm.clickCount === 1) {
      // Start the 5s timer
      fsm.resetTimer = setTimeout(_reset, FSM_TIMEOUT_MS);
      button.textContent = "Confirm 1/3";
      button.classList.add("armed");
    } else if (fsm.clickCount === 2) {
      button.textContent = "Confirm 2/3";
    } else if (fsm.clickCount >= 3) {
      // Cancel the reset timer; we're firing
      if (fsm.resetTimer != null) {
        clearTimeout(fsm.resetTimer);
        fsm.resetTimer = null;
      }
      _fire();
    }
  });

  return wrap;
}

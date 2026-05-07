/**
 * Per-unit bearer-token rotator — Configure tab → Profile editor panel.
 *
 * Layout: a small "Operations" sub-section sitting below the profile
 * Save button, hosting one danger-styled button. The component is
 * structured so Phase 3's Diagnostics tab Danger Zone can host it
 * without modification — it owns its own confirm modal + reveal panel
 * and only needs the `unit` (for unit_id and label) on construction.
 *
 * State machine:
 *
 *   idle:
 *     [ 🔑 Rotate bearer token ]  ← danger-styled
 *
 *   armed (after first click):
 *     ⚠ Rotating will invalidate the unit's current token immediately.
 *        The unit will go offline until you write the new token to
 *        /etc/mlss-grow/token.json on the Pi. Continue?
 *     [ Confirm ] [ Cancel ]
 *
 *   reveal (after POST 201):
 *     ✓ New bearer token — copy now, this is the only time it'll be shown.
 *     [ <input readonly value=TOKEN> ] [Copy]
 *     [ Done ]                        ← clears + back to idle
 *
 * Confirmation pattern follows holiday-mode-toggle / enrollment-key-rotator:
 * a single OK/Cancel rather than the safety-override 3-clicks-in-5s
 * pattern, because rotation is recoverable (rotate again → grab new
 * token from peek-once or the response body).
 *
 * Error responses (403, 404 etc) are surfaced inline without entering
 * the reveal pane — defence in depth so a non-admin who somehow reaches
 * the button doesn't see anything sensitive.
 */


export function renderTokenRotator(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-token-rotator";
  wrap.dataset.testid = "token-rotator";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>🔑 Operations</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "tr-body";
  wrap.appendChild(body);

  const blurb = doc.createElement("p");
  blurb.className = "tr-blurb";
  blurb.textContent =
    "Rotate this unit's bearer token. Use it if the token may have " +
    "leaked, or on scheduled rotation. The unit will go offline until " +
    "you write the new token to /etc/mlss-grow/token.json on the Pi.";
  body.appendChild(blurb);

  // ── Idle row: rotate button ────────────────────────────────────────
  const armRow = doc.createElement("div");
  armRow.className = "tr-arm-row";
  body.appendChild(armRow);

  const rotateBtn = doc.createElement("button");
  rotateBtn.type = "button";
  rotateBtn.className = "px-btn danger tr-rotate-btn";
  rotateBtn.textContent = "🔑 Rotate bearer token";
  rotateBtn.dataset.testid = "tr-rotate-btn";
  armRow.appendChild(rotateBtn);

  // ── Confirm modal ──────────────────────────────────────────────────
  const confirmGroup = doc.createElement("div");
  confirmGroup.className = "tr-confirm-group";
  confirmGroup.style.display = "none";
  confirmGroup.dataset.testid = "tr-confirm-group";

  const warn = doc.createElement("p");
  warn.className = "tr-warn";
  warn.textContent =
    "⚠ Rotating will invalidate this unit's current token immediately. " +
    "The unit will go offline until you write the new token to " +
    "/etc/mlss-grow/token.json on the Pi. Continue?";
  confirmGroup.appendChild(warn);

  const confirmRow = doc.createElement("div");
  confirmRow.className = "tr-confirm-row";

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn danger tr-confirm-btn";
  confirmBtn.textContent = "Confirm";
  confirmBtn.dataset.testid = "tr-confirm-btn";
  confirmRow.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn tr-cancel-btn";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "tr-cancel-btn";
  confirmRow.appendChild(cancelBtn);

  confirmGroup.appendChild(confirmRow);
  body.appendChild(confirmGroup);

  // ── Reveal pane ────────────────────────────────────────────────────
  const reveal = doc.createElement("div");
  reveal.className = "tr-reveal";
  reveal.style.display = "none";
  reveal.dataset.testid = "tr-reveal";

  const revealHead = doc.createElement("div");
  revealHead.className = "tr-reveal-head";
  revealHead.textContent =
    "✓ New bearer token — copy it now, this is the only time it will be shown:";
  reveal.appendChild(revealHead);

  const tokenRow = doc.createElement("div");
  tokenRow.className = "tr-token-row";

  const tokenInput = doc.createElement("input");
  tokenInput.type = "text";
  tokenInput.readOnly = true;
  tokenInput.className = "tr-token-input";
  tokenInput.dataset.testid = "tr-token-input";
  tokenInput.addEventListener("focus", () => tokenInput.select());
  tokenRow.appendChild(tokenInput);

  const copyBtn = doc.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "px-btn tr-copy-btn";
  copyBtn.textContent = "Copy";
  copyBtn.dataset.testid = "tr-copy-btn";
  copyBtn.addEventListener("click", async () => {
    tokenInput.select();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(tokenInput.value);
      } else {
        doc.execCommand && doc.execCommand("copy");
      }
      copyBtn.textContent = "Copied!";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
    } catch (e) {
      copyBtn.textContent = "Copy failed";
    }
  });
  tokenRow.appendChild(copyBtn);
  reveal.appendChild(tokenRow);

  const doneBtn = doc.createElement("button");
  doneBtn.type = "button";
  doneBtn.className = "px-btn tr-done-btn";
  doneBtn.textContent = "Done";
  doneBtn.dataset.testid = "tr-done-btn";
  reveal.appendChild(doneBtn);

  body.appendChild(reveal);

  // ── Inline error surface ───────────────────────────────────────────
  const errEl = doc.createElement("div");
  errEl.className = "tr-error";
  errEl.dataset.testid = "tr-error";
  errEl.style.display = "none";
  body.appendChild(errEl);

  // ── State transitions ──────────────────────────────────────────────
  function _toIdle() {
    rotateBtn.style.display = "";
    confirmGroup.style.display = "none";
    reveal.style.display = "none";
    tokenInput.value = "";
    errEl.style.display = "none";
    errEl.textContent = "";
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
    confirmBtn.textContent = "Confirm";
  }

  function _toArmed() {
    rotateBtn.style.display = "none";
    confirmGroup.style.display = "";
    errEl.style.display = "none";
  }

  async function _doRotate() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Rotating…";
    try {
      const r = await fetchFn(
        `/api/grow/units/${unit.id}/rotate-token`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
      );
      if (r.status === 201) {
        const body = await r.json();
        tokenInput.value = body.token || "";
        rotateBtn.style.display = "none";
        confirmGroup.style.display = "none";
        reveal.style.display = "";
        confirmBtn.disabled = false;
        cancelBtn.disabled = false;
        confirmBtn.textContent = "Confirm";
        return;
      }
      // Error path — keep reveal hidden, surface message inline
      let msg;
      if (r.status === 403) {
        msg = "Forbidden — admin role required to rotate tokens.";
      } else if (r.status === 404) {
        msg = "Unit not found — it may have been deleted.";
      } else {
        const err = await r.json().catch(() => ({}));
        msg = err.error || r.statusText || "Rotation failed";
      }
      errEl.textContent = `✗ ${msg}`;
      errEl.style.display = "";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm";
    } catch (exc) {
      errEl.textContent = `✗ ${exc.message || "Network error"}`;
      errEl.style.display = "";
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      confirmBtn.textContent = "Confirm";
    }
  }

  rotateBtn.addEventListener("click", _toArmed);
  cancelBtn.addEventListener("click", _toIdle);
  confirmBtn.addEventListener("click", _doRotate);
  doneBtn.addEventListener("click", _toIdle);

  return wrap;
}

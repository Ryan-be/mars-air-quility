/**
 * Enrollment-key rotator panel — Settings → Grow (admin-only).
 *
 * Two states:
 *
 *   idle:
 *     [ Rotate enrollment key ]   ← clicking arms the confirm step
 *
 *   armed (after first click):
 *     ⚠ Confirm rotation — the previous key will stop working immediately.
 *     [ Confirm ] [ Cancel ]
 *
 * Confirm → POST /api/grow/enrollment-key/rotate → reveal pane:
 *
 *     ✓ New enrollment key (copy now — won't be shown again)
 *     [ <input readonly value=KEY> ] [Copy]
 *     [ Done ]                         ← clears the key from the DOM
 *
 * The reveal pane uses a readonly <input> rather than plain text so the
 * user can select-all + copy on any platform. After clicking Done, the
 * key is cleared from the input and the panel returns to idle.
 *
 * Confirmation is a single OK/Cancel rather than the safety-override
 * 3-clicks-in-5s pattern: rotation is recoverable (rotate again) and
 * the existing per-unit bearer tokens are unaffected, so heavy friction
 * isn't warranted. The explicit Confirm step exists only to prevent an
 * accidental click from invalidating the previous key.
 */


/**
 * Build the rotator panel.
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderEnrollmentKeyRotator(opts = {}) {
  const doc = opts.ownerDocument || document;

  const wrap = doc.createElement("div");
  wrap.className = "settings-panel ek-rotator";

  const head = doc.createElement("div");
  head.className = "settings-panel-head";
  head.innerHTML = "<span>Enrollment key</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "settings-panel-body";
  wrap.appendChild(body);

  const blurb = doc.createElement("p");
  blurb.className = "ek-blurb";
  blurb.textContent =
    "Rotate the household enrollment key. Existing enrolled units are " +
    "unaffected — they hold per-unit bearer tokens. Only future enroll " +
    "attempts will need the new key.";
  body.appendChild(blurb);

  // ── Idle / armed area ─────────────────────────────────────────────
  const armRow = doc.createElement("div");
  armRow.className = "ek-arm-row";
  body.appendChild(armRow);

  const rotateBtn = doc.createElement("button");
  rotateBtn.type = "button";
  rotateBtn.className = "px-btn primary ek-rotate-btn";
  rotateBtn.textContent = "Rotate enrollment key";
  rotateBtn.dataset.testid = "ek-rotate-btn";
  armRow.appendChild(rotateBtn);

  const confirmGroup = doc.createElement("div");
  confirmGroup.className = "ek-confirm-group";
  confirmGroup.style.display = "none";
  confirmGroup.dataset.testid = "ek-confirm-group";

  const warn = doc.createElement("span");
  warn.className = "ek-warn";
  warn.textContent =
    "Confirm? The previous key will stop accepting new enrollments.";
  confirmGroup.appendChild(warn);

  const confirmBtn = doc.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "px-btn danger ek-confirm-btn";
  confirmBtn.textContent = "Confirm";
  confirmBtn.dataset.testid = "ek-confirm-btn";
  confirmGroup.appendChild(confirmBtn);

  const cancelBtn = doc.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-btn ek-cancel-btn";
  cancelBtn.textContent = "Cancel";
  cancelBtn.dataset.testid = "ek-cancel-btn";
  confirmGroup.appendChild(cancelBtn);

  armRow.appendChild(confirmGroup);

  // ── Reveal pane ──────────────────────────────────────────────────
  const reveal = doc.createElement("div");
  reveal.className = "ek-reveal";
  reveal.style.display = "none";
  reveal.dataset.testid = "ek-reveal";

  const revealHead = doc.createElement("div");
  revealHead.className = "ek-reveal-head";
  revealHead.textContent =
    "✓ New enrollment key — copy it now, this is the only time it will be shown:";
  reveal.appendChild(revealHead);

  const keyRow = doc.createElement("div");
  keyRow.className = "ek-key-row";

  const keyInput = doc.createElement("input");
  keyInput.type = "text";
  keyInput.readOnly = true;
  keyInput.className = "ek-key-input";
  keyInput.dataset.testid = "ek-key-input";
  // Auto-select on focus so click-to-copy works
  keyInput.addEventListener("focus", () => keyInput.select());
  keyRow.appendChild(keyInput);

  const copyBtn = doc.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "px-btn ek-copy-btn";
  copyBtn.textContent = "Copy";
  copyBtn.dataset.testid = "ek-copy-btn";
  copyBtn.addEventListener("click", async () => {
    keyInput.select();
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(keyInput.value);
      } else {
        doc.execCommand && doc.execCommand("copy");
      }
      copyBtn.textContent = "Copied!";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
    } catch (e) {
      copyBtn.textContent = "Copy failed";
    }
  });
  keyRow.appendChild(copyBtn);
  reveal.appendChild(keyRow);

  const doneBtn = doc.createElement("button");
  doneBtn.type = "button";
  doneBtn.className = "px-btn ek-done-btn";
  doneBtn.textContent = "Done";
  doneBtn.dataset.testid = "ek-done-btn";
  reveal.appendChild(doneBtn);

  const errEl = doc.createElement("div");
  errEl.className = "ek-error";
  errEl.dataset.testid = "ek-error";
  errEl.style.display = "none";
  body.appendChild(errEl);

  body.appendChild(reveal);

  // ── State transitions ────────────────────────────────────────────
  function _toIdle() {
    rotateBtn.style.display = "";
    confirmGroup.style.display = "none";
    reveal.style.display = "none";
    keyInput.value = "";
    errEl.style.display = "none";
    errEl.textContent = "";
  }

  function _toArmed() {
    rotateBtn.style.display = "none";
    confirmGroup.style.display = "";
  }

  async function _doRotate() {
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    confirmBtn.textContent = "Rotating…";
    try {
      const r = await fetch("/api/grow/enrollment-key/rotate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (r.status !== 201 && !r.ok) {
        const err = await r.json().catch(() => ({}));
        const msg = err.error || r.statusText || "Rotation failed";
        errEl.textContent = `✗ ${msg}`;
        errEl.style.display = "";
        confirmBtn.disabled = false;
        cancelBtn.disabled = false;
        confirmBtn.textContent = "Confirm";
        return;
      }
      const body = await r.json();
      keyInput.value = body.key || "";
      // Hide arming UI; show reveal pane
      rotateBtn.style.display = "none";
      confirmGroup.style.display = "none";
      reveal.style.display = "";
      // Reset confirm button state for the next rotation
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

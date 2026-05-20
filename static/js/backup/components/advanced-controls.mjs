/**
 * Advanced (destructive) controls.
 *
 * Four buttons. Two of them — clear_outbox and force_rebootstrap — are
 * gated by an inline confirm dialog with a magic-word challenge.
 * Pause / Resume are reversible so they fire after a single click.
 *
 * Why NOT window.confirm():
 *
 *   - The native dialog can't be styled — it breaks the design system.
 *   - A magic-word challenge stops a stray double-tap from wiping
 *     hours of un-shipped backup data.
 *
 * The fetchFn seam exists for tests; in production the global fetch
 * is used.
 */


/**
 * Per-action magic word. Operators must type this string EXACTLY for
 * the confirm button to enable. Lowercase variants are rejected — the
 * dialog also displays the word in monospaced ALL-CAPS so there's
 * no ambiguity.
 */
const MAGIC_WORDS = {
  "clear-outbox":      "CLEAR",
  "force-rebootstrap": "BOOTSTRAP",
};


/** action-name → maintenance API-action mapping. */
const ACTION_API = {
  "clear-outbox":      "clear_outbox",
  "force-rebootstrap": "force_rebootstrap",
};


/**
 * Build the inline confirm dialog markup for a destructive action.
 * Hidden by default (display:none); the caller toggles it open on
 * primary-button click.
 */
function _renderConfirmDialog(doc, action, copy) {
  const wrap = doc.createElement("div");
  wrap.className = "bk-confirm-inline";
  wrap.dataset.confirm = action;
  wrap.style.display = "none";

  const word = MAGIC_WORDS[action];
  wrap.innerHTML = `
    <p class="bk-confirm-warn">${copy.warn}</p>
    <p class="bk-confirm-instruct">
      Type <code>${word}</code> below to confirm.
    </p>
    <input type="text" class="bk-confirm-challenge" data-field="challenge"
           autocomplete="off" spellcheck="false">
    <div class="bk-confirm-actions">
      <button type="button" class="btn-modal-cancel"  data-action="cancel">Cancel</button>
      <button type="button" class="btn-modal-confirm" data-action="confirm" disabled>
        ${copy.confirmLabel}
      </button>
    </div>
    <p class="bk-confirm-result" data-result="${action}"></p>
  `;
  return wrap;
}


/**
 * Render the advanced controls section. Returns the root <section>
 * element with:
 *   - .setPaused(bool): flip the pause/resume button label
 */
export function renderAdvancedControls({ paused, ownerDocument, fetchFn }) {
  const doc = ownerDocument || document;
  const f = fetchFn || ((u, o) => fetch(u, o));

  // Internal mutable state. Captured by handlers below.
  const state = { paused: !!paused };

  const root = doc.createElement("section");
  root.className = "card bk-advanced-controls";
  root.innerHTML = `<h3>Advanced controls</h3>
    <p class="card-desc">
      Operator-level actions. The two destructive operations require
      typing a magic word as a guardrail against accidental clicks.
    </p>`;

  // --- Pause / Resume button (reversible) ---
  const pauseBtn = doc.createElement("button");
  pauseBtn.type = "button";
  pauseBtn.dataset.action = "pause-resume";
  pauseBtn.className = "btn-search";  // updated by _refreshPauseBtn
  _refreshPauseBtn();
  root.appendChild(pauseBtn);

  function _refreshPauseBtn() {
    pauseBtn.textContent = state.paused ? "Resume shipping" : "Pause shipping";
    pauseBtn.className = state.paused ? "btn-modal-cancel" : "btn-search";
  }

  pauseBtn.addEventListener("click", async () => {
    const action = state.paused ? "resume" : "pause";
    pauseBtn.disabled = true;
    try {
      const r = await f("/api/admin/backup/maintenance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, confirm: true }),
      });
      const data = await r.json();
      if (data.ok) {
        state.paused = action === "pause";
        _refreshPauseBtn();
      }
    } finally {
      pauseBtn.disabled = false;
    }
  });

  // --- Force re-bootstrap ---
  const fbBtn = doc.createElement("button");
  fbBtn.type = "button";
  fbBtn.dataset.action = "force-rebootstrap";
  fbBtn.className = "btn-modal-confirm";
  fbBtn.textContent = "Force re-bootstrap";
  root.appendChild(fbBtn);

  const fbDialog = _renderConfirmDialog(doc, "force-rebootstrap", {
    warn: "This restarts the historical-data scan from scratch. " +
          "Any in-progress drain will be queued behind the re-scan, " +
          "which can take many minutes on a Pi with months of history.",
    confirmLabel: "Re-bootstrap now",
  });
  root.appendChild(fbDialog);

  // --- Clear outbox ---
  const coBtn = doc.createElement("button");
  coBtn.type = "button";
  coBtn.dataset.action = "clear-outbox";
  coBtn.className = "btn-modal-confirm";
  coBtn.textContent = "Clear outbox";
  root.appendChild(coBtn);

  const coDialog = _renderConfirmDialog(doc, "clear-outbox", {
    warn: "This DELETES every un-shipped row in outbox_changes, " +
          "outbox_blobs, and outbox_delete_scope. The data already on " +
          "the home server is unaffected, but anything not yet shipped " +
          "is gone forever. Use only after a fresh bootstrap.",
    confirmLabel: "Clear outbox permanently",
  });
  root.appendChild(coDialog);


  /**
   * Toggle a confirm dialog open, then wire the magic-word listener
   * and the cancel/confirm button handlers. Idempotent: re-opening
   * resets the challenge field.
   */
  function _openDialog(actionName) {
    const dialog = root.querySelector(`[data-confirm='${actionName}']`);
    dialog.style.display = "";
    const challenge = dialog.querySelector("[data-field='challenge']");
    const confirmBtn = dialog.querySelector("[data-action='confirm']");
    const cancelBtn = dialog.querySelector("[data-action='cancel']");
    challenge.value = "";
    confirmBtn.disabled = true;

    // Magic-word listener: enable when EXACT match.
    const word = MAGIC_WORDS[actionName];
    challenge.oninput = () => {
      confirmBtn.disabled = challenge.value !== word;
    };

    cancelBtn.onclick = () => { dialog.style.display = "none"; };

    confirmBtn.onclick = async () => {
      confirmBtn.disabled = true;
      const result = dialog.querySelector(`[data-result='${actionName}']`);
      result.textContent = "…";
      result.className = "bk-confirm-result";
      try {
        const r = await f("/api/admin/backup/maintenance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: ACTION_API[actionName],
            confirm: true,
          }),
        });
        const data = await r.json();
        if (data.ok) {
          result.textContent = data.action || "OK";
          result.className = "bk-confirm-result bk-result-ok";
          // Close the dialog after a successful action so the operator
          // returns to the calm idle layout.
          setTimeout(() => { dialog.style.display = "none"; }, 1500);
        } else {
          result.textContent = data.error || "Failed";
          result.className = "bk-confirm-result bk-result-err";
        }
      } catch (exc) {
        result.textContent = `Failed: ${exc.message}`;
        result.className = "bk-confirm-result bk-result-err";
      }
    };
  }

  fbBtn.addEventListener("click", () => _openDialog("force-rebootstrap"));
  coBtn.addEventListener("click", () => _openDialog("clear-outbox"));


  /**
   * Flip the pause-button label. Called by the orchestrator after the
   * /status endpoint returns a fresh paused flag.
   */
  root.setPaused = function (newPaused) {
    state.paused = !!newPaused;
    _refreshPauseBtn();
  };

  return root;
}

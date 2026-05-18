/**
 * Add-unit modal — shown from the /grow fleet header "+ Add Unit" button.
 *
 * Minimum-viable enrollment helper: a modal that reveals the existing
 * household enrollment key (via the one-shot peek-once endpoint) plus
 * brief operator instructions for installing on a new Pi.
 *
 * State machine:
 *
 *   idle:
 *     [ Reveal enrollment key ]    ← admin-only; click triggers peek-once
 *
 *   reveal (after GET 200):
 *     ✓ Enrollment key — copy now, this is the only time it'll be shown.
 *     [ <input readonly value=KEY> ] [Copy]
 *     <operator instructions>
 *     [ Done ]
 *
 *   already-revealed (after GET 410):
 *     The enrollment key has already been viewed. To enroll a new unit
 *     you need to mint a fresh key.
 *     [ Go to Settings → Grow ]   ← link to /grow/settings rotator
 *
 *   error:
 *     ✗ <message>                  ← surface 403/network errors inline
 *
 * The reveal pane mirrors the token-rotator + enrollment-key-rotator
 * pattern: a readonly <input> + Copy button so click-to-copy works on
 * any platform. Closing the modal (via Done, the × button, ESC, or
 * clicking the backdrop) clears the key from the DOM.
 *
 * Server-side, GET /api/grow/enrollment-key/peek-once is gated by
 * @require_role("admin"). The button on the fleet page is also hidden
 * for non-admins, so this is defence in depth — if a non-admin somehow
 * reaches openAddUnitModal() they'll see a 403 inline rather than the
 * key.
 */


function _isAdmin(doc) {
  // body.dataset.role is set by the Jinja template from session["user_role"].
  // Falls back to "" (no admin actions) when missing — safer than a truthy
  // default that might leak the reveal button in test environments that
  // don't stamp the body.
  const body = doc && doc.body;
  return !!body && body.dataset && body.dataset.role === "admin";
}


/**
 * Open the add-unit modal. Returns a handle with `close()` so callers
 * can dismiss programmatically (mostly for tests).
 *
 * @param {object} opts
 * @param {Document} [opts.ownerDocument] Document to mount into.
 * @param {Function} [opts.fetchFn] Override fetch (for tests).
 * @param {string}   [opts.mlssHost] Override the host shown in the
 *                                   install one-liner. Defaults to
 *                                   window.location.hostname.
 * @returns {{ close: () => void, element: HTMLElement }}
 */
export function openAddUnitModal(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));
  const mlssHost = opts.mlssHost
    ?? (typeof window !== "undefined" && window.location
      ? window.location.hostname || "mlss.local"
      : "mlss.local");

  // ── overlay (dim backdrop) ──────────────────────────────────────────
  const overlay = doc.createElement("div");
  overlay.className = "add-unit-overlay";
  overlay.dataset.testid = "add-unit-overlay";

  const box = doc.createElement("div");
  box.className = "add-unit-box";
  box.dataset.testid = "add-unit-box";
  overlay.appendChild(box);

  // ── header ──────────────────────────────────────────────────────────
  const head = doc.createElement("div");
  head.className = "add-unit-head";
  head.innerHTML = "<h3>Add a new grow unit</h3>";
  box.appendChild(head);

  const closeBtn = doc.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "add-unit-close";
  closeBtn.dataset.testid = "add-unit-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";
  head.appendChild(closeBtn);

  // ── body ────────────────────────────────────────────────────────────
  const body = doc.createElement("div");
  body.className = "add-unit-body";
  box.appendChild(body);

  const blurb = doc.createElement("p");
  blurb.className = "add-unit-blurb";
  blurb.textContent =
    "Reveal the household enrollment key and install the grow firmware " +
    "on a fresh Pi Zero W. The key authorises the new unit's first-boot " +
    "enrollment — only show it once, then it's gone.";
  body.appendChild(blurb);

  // Inline error surface (403, network, etc.)
  const errEl = doc.createElement("div");
  errEl.className = "add-unit-error";
  errEl.dataset.testid = "add-unit-error";
  errEl.style.display = "none";
  body.appendChild(errEl);

  // ── Idle row: Reveal button (admin-only) ───────────────────────────
  const idleRow = doc.createElement("div");
  idleRow.className = "add-unit-idle";
  idleRow.dataset.testid = "add-unit-idle";
  body.appendChild(idleRow);

  const revealBtn = doc.createElement("button");
  revealBtn.type = "button";
  revealBtn.className = "px-btn primary add-unit-reveal-btn";
  revealBtn.textContent = "Reveal enrollment key";
  revealBtn.dataset.testid = "add-unit-reveal-btn";
  idleRow.appendChild(revealBtn);

  // Non-admin viewers see a stub message rather than the reveal button.
  if (!_isAdmin(doc)) {
    revealBtn.style.display = "none";
    const adminOnly = doc.createElement("p");
    adminOnly.className = "add-unit-admin-only";
    adminOnly.dataset.testid = "add-unit-admin-only";
    adminOnly.textContent =
      "Adding new units requires the admin role. Ask your household admin " +
      "to enroll the unit.";
    idleRow.appendChild(adminOnly);
  }

  // ── Reveal pane (hidden until peek-once succeeds) ───────────────────
  const reveal = doc.createElement("div");
  reveal.className = "add-unit-reveal";
  reveal.dataset.testid = "add-unit-reveal";
  reveal.style.display = "none";

  const revealHead = doc.createElement("div");
  revealHead.className = "add-unit-reveal-head";
  revealHead.textContent =
    "✓ Enrollment key — copy it now, this is the only time it will be shown:";
  reveal.appendChild(revealHead);

  const keyRow = doc.createElement("div");
  keyRow.className = "add-unit-key-row";

  const keyInput = doc.createElement("input");
  keyInput.type = "text";
  keyInput.readOnly = true;
  keyInput.className = "add-unit-key-input";
  keyInput.dataset.testid = "add-unit-key-input";
  keyInput.addEventListener("focus", () => keyInput.select());
  keyRow.appendChild(keyInput);

  const copyBtn = doc.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "px-btn add-unit-copy-btn";
  copyBtn.textContent = "Copy";
  copyBtn.dataset.testid = "add-unit-copy-btn";
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

  const instructions = doc.createElement("ol");
  instructions.className = "add-unit-instructions";
  instructions.dataset.testid = "add-unit-instructions";
  instructions.innerHTML = `
    <li>SSH into the new Pi and run:
      <pre><code>curl -k https://${mlssHost}:5000/api/grow/install.sh | sudo bash</code></pre>
    </li>
    <li>When prompted, paste the enrollment key above.</li>
    <li>The unit will appear in this fleet view once it connects.</li>
  `;
  reveal.appendChild(instructions);

  body.appendChild(reveal);

  // ── Already-revealed pane ──────────────────────────────────────────
  const gone = doc.createElement("div");
  gone.className = "add-unit-gone";
  gone.dataset.testid = "add-unit-gone";
  gone.style.display = "none";

  const goneMsg = doc.createElement("p");
  goneMsg.className = "add-unit-gone-msg";
  goneMsg.textContent =
    "The enrollment key has already been viewed. To add a new unit you " +
    "need to mint a fresh key — rotate it from Grow → Settings.";
  gone.appendChild(goneMsg);

  const rotateLink = doc.createElement("a");
  rotateLink.className = "px-btn primary add-unit-rotate-link";
  rotateLink.dataset.testid = "add-unit-rotate-link";
  rotateLink.href = "/grow/settings";
  rotateLink.textContent = "Go to Grow → Settings";
  gone.appendChild(rotateLink);

  body.appendChild(gone);

  // ── Footer: Done button ────────────────────────────────────────────
  const foot = doc.createElement("div");
  foot.className = "add-unit-foot";
  body.appendChild(foot);

  const doneBtn = doc.createElement("button");
  doneBtn.type = "button";
  doneBtn.className = "px-btn add-unit-done-btn";
  doneBtn.textContent = "Done";
  doneBtn.dataset.testid = "add-unit-done-btn";
  foot.appendChild(doneBtn);

  // ── Behaviour ──────────────────────────────────────────────────────
  function close() {
    // Clear any revealed key from the DOM before tearing down — defence
    // in depth against a screen capture taken between close + GC.
    keyInput.value = "";
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    doc.removeEventListener("keydown", _onKey);
  }

  function _onKey(ev) {
    if (ev.key === "Escape") close();
  }
  doc.addEventListener("keydown", _onKey);

  overlay.addEventListener("click", (ev) => {
    // Clicks on the dim backdrop (overlay itself) dismiss; clicks
    // inside the box don't bubble out because the box is a child.
    if (ev.target === overlay) close();
  });

  closeBtn.addEventListener("click", close);
  doneBtn.addEventListener("click", close);

  async function _doReveal() {
    revealBtn.disabled = true;
    revealBtn.textContent = "Revealing…";
    errEl.style.display = "none";
    errEl.textContent = "";
    try {
      const r = await fetchFn("/api/grow/enrollment-key/peek-once");
      if (r.ok) {
        const data = await r.json().catch(() => ({}));
        keyInput.value = data.key || "";
        idleRow.style.display = "none";
        reveal.style.display = "";
        return;
      }
      if (r.status === 410) {
        // Already-revealed — direct user to rotate it via Settings.
        idleRow.style.display = "none";
        gone.style.display = "";
        return;
      }
      let msg;
      if (r.status === 403) {
        msg = "Forbidden — admin role required to reveal the enrollment key.";
      } else {
        const err = await r.json().catch(() => ({}));
        msg = err.error || r.statusText || "Reveal failed";
      }
      errEl.textContent = `✗ ${msg}`;
      errEl.style.display = "";
      revealBtn.disabled = false;
      revealBtn.textContent = "Reveal enrollment key";
    } catch (exc) {
      errEl.textContent = `✗ ${exc.message || "Network error"}`;
      errEl.style.display = "";
      revealBtn.disabled = false;
      revealBtn.textContent = "Reveal enrollment key";
    }
  }

  revealBtn.addEventListener("click", _doReveal);

  // Mount last so the modal is fully wired before the user sees it.
  doc.body.appendChild(overlay);

  return { close, element: overlay };
}

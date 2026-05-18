/**
 * Plant journal editor — Phase 4 #7.
 *
 * Lists existing operator notes for a unit (most-recent first) and lets
 * controllers + admins add / edit / delete them. Each note is pinned to
 * a timestamp on the unit's history so the moisture chart and photo
 * timelapse can overlay markers at those moments — that overlay is
 * wired by the History tab orchestrator (history-panel.mjs) which
 * listens for the `journal-changed` CustomEvent the editor emits after
 * any successful CRUD.
 *
 * RBAC mirrors the server (see api_grow_journal.py):
 *   - viewer: list-only, no add/edit/delete
 *   - controller: add / edit-own / delete-own
 *   - admin: full
 *
 * The "edit" and "delete" buttons render only when the session user can
 * act on the row. We compute that by reading the current user from a
 * helper that the orchestrator passes in (so this module stays
 * test-friendly without coupling to a global). The server enforces the
 * gate authoritatively — the UI hide is just polish.
 */


/** Default timestamp for the "Add note" composer (right now, ISO8601). */
function _nowIsoLocalForInput() {
  // <input type="datetime-local"> wants "YYYY-MM-DDTHH:MM" without
  // seconds or timezone suffix. We construct from local time so the
  // operator types in their own clock; the editor re-shifts to UTC at
  // POST time.
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}


/** Convert a "YYYY-MM-DDTHH:MM" datetime-local string to ISO8601 UTC. */
export function _localToIsoUtc(local) {
  // Browser parses naive local strings in the local TZ. Date#toISOString
  // returns UTC. The conversion is exactly what we want.
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}


/** Format an ISO8601 UTC timestamp as "MMM D, HH:MM UTC" for display. */
function _formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"];
  const pad = n => String(n).padStart(2, "0");
  return (
    `${months[d.getUTCMonth()]} ${d.getUTCDate()}, ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}


/** True if the session user can edit/delete a row authored by `author`. */
function _canMutate(author, currentUser, currentRole) {
  if (currentRole === "admin") return true;
  if (currentRole !== "controller") return false;
  return author === currentUser;
}


/** True if the session role can add new notes (controller+). */
function _canCreate(currentRole) {
  return currentRole === "controller" || currentRole === "admin";
}


/**
 * Build the journal editor panel.
 *
 * @param {object} unit   GET /api/grow/units/<id> response (we read `id`).
 * @param {object} opts
 *   - ownerDocument: the document to create elements in (defaults to global).
 *   - currentUser:  session["user"] — login of the viewer.
 *   - currentRole:  session["user_role"] — "viewer" / "controller" / "admin".
 *   - fetchFn:      fetch implementation (defaults to global fetch); injected
 *                   for tests so they don't need a live server.
 *   - rangeStr:     range vocab matching /history (default "7d"); passed as
 *                   ?range=… on the list call.
 * @returns {HTMLElement}
 */
export function renderJournalEditor(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const currentUser = opts.currentUser ?? null;
  const currentRole = opts.currentRole ?? "viewer";
  const fetchFn = opts.fetchFn || (typeof fetch !== "undefined" ? fetch : null);
  const rangeStr = opts.rangeStr || "7d";

  const wrap = doc.createElement("div");
  wrap.className = "du-panel journal-editor";
  wrap.dataset.testid = "journal-editor";

  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📝 Notes</span>";
  wrap.appendChild(head);

  const body = doc.createElement("div");
  body.className = "journal-body";
  wrap.appendChild(body);

  const composerHost = doc.createElement("div");
  composerHost.className = "journal-composer-host";
  body.appendChild(composerHost);

  const list = doc.createElement("div");
  list.className = "journal-list";
  list.dataset.testid = "journal-list";
  body.appendChild(list);

  const _emitChanged = () => {
    wrap.dispatchEvent(new CustomEvent("journal-changed", {
      bubbles: true,
      detail: { unitId: unit.id },
    }));
  };

  const _renderEmpty = () => {
    list.textContent = "";
    const p = doc.createElement("p");
    p.className = "journal-empty";
    p.textContent = "No notes yet.";
    list.appendChild(p);
  };

  const _renderEntry = (entry) => {
    const row = doc.createElement("div");
    row.className = "journal-entry";
    row.dataset.entryId = entry.id;
    row.dataset.testid = `journal-entry-${entry.id}`;

    const meta = doc.createElement("div");
    meta.className = "journal-entry-meta";
    const ts = doc.createElement("span");
    ts.className = "journal-entry-ts";
    ts.textContent = _formatTimestamp(entry.timestamp_utc);
    const auth = doc.createElement("span");
    auth.className = "journal-entry-author";
    auth.textContent = ` — ${entry.author}`;
    meta.appendChild(ts);
    meta.appendChild(auth);
    if (entry.updated_at) {
      const edited = doc.createElement("span");
      edited.className = "journal-entry-edited";
      edited.textContent = " (edited)";
      meta.appendChild(edited);
    }
    row.appendChild(meta);

    const bodyEl = doc.createElement("div");
    bodyEl.className = "journal-entry-body";
    bodyEl.textContent = entry.body;
    row.appendChild(bodyEl);

    if (_canMutate(entry.author, currentUser, currentRole)) {
      const actions = doc.createElement("div");
      actions.className = "journal-entry-actions";
      const editBtn = doc.createElement("button");
      editBtn.className = "gu-btn journal-edit-btn";
      editBtn.textContent = "Edit";
      editBtn.dataset.action = "edit";
      editBtn.addEventListener(
        "click", () => _enterEditMode(row, bodyEl, entry, actions),
      );
      const delBtn = doc.createElement("button");
      delBtn.className = "gu-btn journal-delete-btn";
      delBtn.textContent = "Delete";
      delBtn.dataset.action = "delete";
      delBtn.addEventListener("click", () => _deleteEntry(entry));
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      row.appendChild(actions);
    }
    return row;
  };

  const _enterEditMode = (row, bodyEl, entry, actionsEl) => {
    const ta = doc.createElement("textarea");
    ta.className = "journal-edit-textarea";
    ta.value = entry.body;
    bodyEl.replaceWith(ta);

    const saveBtn = doc.createElement("button");
    saveBtn.className = "gu-btn journal-edit-save";
    saveBtn.textContent = "Save";
    const cancelBtn = doc.createElement("button");
    cancelBtn.className = "gu-btn journal-edit-cancel";
    cancelBtn.textContent = "Cancel";
    actionsEl.textContent = "";
    actionsEl.appendChild(saveBtn);
    actionsEl.appendChild(cancelBtn);

    cancelBtn.addEventListener("click", () => _refresh());
    saveBtn.addEventListener("click", async () => {
      const newBody = ta.value.trim();
      if (!newBody) return;
      saveBtn.disabled = true;
      try {
        const resp = await fetchFn(
          `/api/grow/units/${unit.id}/journal/${entry.id}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ body: newBody }),
          },
        );
        if (!resp.ok) throw new Error(`PATCH failed: ${resp.status}`);
        await _refresh();
        _emitChanged();
      } catch (e) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Retry";
      }
    });
  };

  const _deleteEntry = async (entry) => {
    try {
      const resp = await fetchFn(
        `/api/grow/units/${unit.id}/journal/${entry.id}`,
        { method: "DELETE" },
      );
      if (!resp.ok) throw new Error(`DELETE failed: ${resp.status}`);
      await _refresh();
      _emitChanged();
    } catch (_e) {
      // No global toast plumbing here yet — failure leaves the row in
      // place; the operator sees no change and can retry. A future
      // toast/banner would hook in at this catch.
    }
  };

  const _renderComposer = () => {
    if (!_canCreate(currentRole)) return;
    composerHost.textContent = "";
    const composer = doc.createElement("div");
    composer.className = "journal-composer";
    composer.dataset.testid = "journal-composer";

    const tsInput = doc.createElement("input");
    tsInput.type = "datetime-local";
    tsInput.className = "journal-composer-ts";
    tsInput.value = _nowIsoLocalForInput();

    const ta = doc.createElement("textarea");
    ta.className = "journal-composer-body";
    ta.placeholder = "Add a note…";

    const submit = doc.createElement("button");
    submit.className = "gu-btn journal-composer-submit";
    submit.textContent = "Add note";
    submit.disabled = false;
    submit.addEventListener("click", async () => {
      const noteBody = ta.value.trim();
      if (!noteBody) return;
      const tsIso = _localToIsoUtc(tsInput.value);
      if (!tsIso) return;
      submit.disabled = true;
      try {
        const resp = await fetchFn(`/api/grow/units/${unit.id}/journal`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timestamp_utc: tsIso, body: noteBody }),
        });
        if (!resp.ok) throw new Error(`POST failed: ${resp.status}`);
        ta.value = "";
        tsInput.value = _nowIsoLocalForInput();
        await _refresh();
        _emitChanged();
      } catch (_e) {
        // Failure: leave inputs alone so operator can retry.
      } finally {
        submit.disabled = false;
      }
    });

    composer.appendChild(tsInput);
    composer.appendChild(ta);
    composer.appendChild(submit);
    composerHost.appendChild(composer);
  };

  const _refresh = async () => {
    if (!fetchFn) {
      _renderEmpty();
      return;
    }
    let entries = [];
    try {
      const resp = await fetchFn(
        `/api/grow/units/${unit.id}/journal?range=${rangeStr}`,
      );
      if (resp.ok) {
        const payload = await resp.json();
        // Defensive: if the server responds with anything other than a
        // JSON array (auth-required endpoint returning {}, an error
        // wrapper, etc.) fall through to the empty state instead of
        // crashing on a non-iterable. Tests with broad fetch mocks rely
        // on this — they often return `{}` for unstubbed URLs.
        if (Array.isArray(payload)) {
          entries = payload;
        }
      }
    } catch (_e) {
      // Network error — render empty rather than throw; the user
      // will see "No notes yet" with a stale composer they can retry.
    }
    list.textContent = "";
    if (entries.length === 0) {
      _renderEmpty();
      return;
    }
    for (const e of entries) {
      list.appendChild(_renderEntry(e));
    }
  };

  _renderComposer();
  _refresh();

  return wrap;
}

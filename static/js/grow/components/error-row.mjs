/**
 * error-row — single row in the /grow/errors fleet-wide log.
 *
 * Layout:
 *   [severity icon] [unit_label · kind · timestamp]
 *                   [message]
 *                   [Resolve] [Snooze 1h] [Snooze 24h]   <- admin only
 *
 * Admin gating: reads document.body.dataset.role at render time. Tests
 * call setBodyRole() to flip it before mounting the component. The
 * server still enforces /api/grow/errors/<id> via @require_role("admin")
 * — the role check here is purely visual.
 *
 * Snooze rendering: rows with snoozed_until > now render with the
 * "snoozed" CSS class (greyed/muted). Server doesn't filter snoozed
 * rows out, so admins can still see + un-snooze them.
 *
 * Events: after every successful PATCH the row dispatches an
 * `error-updated` custom event (bubbles, composed) so the orchestrator
 * (errors.mjs) can refetch and rebuild the list.
 */

const SEVERITY_ICONS = {
  info: "ℹ",
  warning: "⚠",
  critical: "✕",
};

const SEVERITY_CLASSES = {
  info: "sev-info",
  warning: "sev-warning",
  critical: "sev-critical",
};


function _isAdmin(doc) {
  // body.dataset.role is set by the Jinja template from session["user_role"].
  // Falls back to "" (no admin actions) when missing — safer than truthy
  // default that might leak buttons in test environments.
  const body = doc && doc.body;
  return !!body && body.dataset && body.dataset.role === "admin";
}


function _isSnoozed(row, now = Date.now()) {
  if (!row.snoozed_until) return false;
  const t = new Date(row.snoozed_until).getTime();
  return Number.isFinite(t) && t > now;
}


async function _patchError(id, body) {
  const r = await fetch(`/api/grow/errors/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PATCH failed: ${r.status}`);
  return r.json();
}


/**
 * Render a single error row.
 *
 * @param {object} row     grow_errors row (server response shape)
 * @param {object} opts
 *   - ownerDocument: defaults to global document
 *   - now: optional clock thunk for tests (Date.now-shape)
 * @returns {HTMLElement}
 */
export function renderErrorRow(row, opts = {}) {
  const doc = opts.ownerDocument || (typeof document !== "undefined" ? document : null);
  const now = (opts.now || (() => Date.now()))();

  const wrap = doc.createElement("div");
  wrap.className = `error-row ${SEVERITY_CLASSES[row.severity] || ""}`;
  wrap.dataset.errorId = String(row.id);
  wrap.dataset.testid = "error-row";
  if (_isSnoozed(row, now)) wrap.classList.add("snoozed");
  if (row.resolved_at) wrap.classList.add("resolved");

  // Severity icon
  const icon = doc.createElement("span");
  icon.className = "error-row-sev";
  icon.dataset.testid = "error-row-sev-icon";
  icon.dataset.severity = row.severity || "";
  icon.textContent = SEVERITY_ICONS[row.severity] || "?";
  wrap.appendChild(icon);

  // Body (lines)
  const bodyCol = doc.createElement("div");
  bodyCol.className = "error-row-body";

  // Header line: unit_label · kind · timestamp
  const header = doc.createElement("div");
  header.className = "error-row-head";
  header.dataset.testid = "error-row-head";
  const parts = [
    row.unit_label || `Unit ${row.unit_id}`,
    row.kind || "",
    row.timestamp_utc || "",
  ].filter(Boolean);
  header.textContent = parts.join(" · ");
  bodyCol.appendChild(header);

  // Message line
  const msg = doc.createElement("div");
  msg.className = "error-row-msg";
  msg.dataset.testid = "error-row-msg";
  msg.textContent = row.message || "";
  bodyCol.appendChild(msg);

  // Admin actions
  if (_isAdmin(doc)) {
    const actions = doc.createElement("div");
    actions.className = "error-row-actions";
    actions.dataset.testid = "error-row-actions";

    const resolveBtn = doc.createElement("button");
    resolveBtn.type = "button";
    resolveBtn.className = "error-row-btn resolve";
    resolveBtn.dataset.testid = "error-row-resolve";
    resolveBtn.textContent = "Resolve";
    resolveBtn.addEventListener("click", async () => {
      try {
        await _patchError(row.id, { resolved_at: "now" });
        wrap.dispatchEvent(new doc.defaultView.CustomEvent("error-updated", {
          bubbles: true, composed: true, detail: { id: row.id, action: "resolve" },
        }));
      } catch (e) {
        console.error("resolve failed", e);
      }
    });
    actions.appendChild(resolveBtn);

    const snooze1hBtn = doc.createElement("button");
    snooze1hBtn.type = "button";
    snooze1hBtn.className = "error-row-btn snooze";
    snooze1hBtn.dataset.testid = "error-row-snooze-1h";
    snooze1hBtn.textContent = "Snooze 1h";
    snooze1hBtn.addEventListener("click", async () => {
      const until = new Date((opts.now || (() => Date.now()))() + 60 * 60 * 1000)
        .toISOString();
      try {
        await _patchError(row.id, { snoozed_until: until });
        wrap.dispatchEvent(new doc.defaultView.CustomEvent("error-updated", {
          bubbles: true, composed: true,
          detail: { id: row.id, action: "snooze_1h", until },
        }));
      } catch (e) {
        console.error("snooze 1h failed", e);
      }
    });
    actions.appendChild(snooze1hBtn);

    const snooze24hBtn = doc.createElement("button");
    snooze24hBtn.type = "button";
    snooze24hBtn.className = "error-row-btn snooze";
    snooze24hBtn.dataset.testid = "error-row-snooze-24h";
    snooze24hBtn.textContent = "Snooze 24h";
    snooze24hBtn.addEventListener("click", async () => {
      const until = new Date((opts.now || (() => Date.now()))() + 24 * 60 * 60 * 1000)
        .toISOString();
      try {
        await _patchError(row.id, { snoozed_until: until });
        wrap.dispatchEvent(new doc.defaultView.CustomEvent("error-updated", {
          bubbles: true, composed: true,
          detail: { id: row.id, action: "snooze_24h", until },
        }));
      } catch (e) {
        console.error("snooze 24h failed", e);
      }
    });
    actions.appendChild(snooze24hBtn);

    bodyCol.appendChild(actions);
  }

  wrap.appendChild(bodyCol);
  return wrap;
}

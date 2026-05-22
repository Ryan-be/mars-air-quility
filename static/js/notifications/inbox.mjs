/**
 * Notifications inbox — renders the last 30 days of notification
 * history (server-paginated via /api/notifications/history?days=30) and
 * sends a single mark-read POST after render so unread rows transition
 * to read on the next page load.
 *
 * Pattern matches other admin component modules: a single `render*`
 * export taking { fetchFn, ownerDocument } so jsdom tests can inject.
 */


const CATEGORY_LABELS = {
  air_quality:      "Air quality",
  grow_units:       "Grow units",
  system_health:    "System health",
  backup_pipeline:  "Backup pipeline",
};

const SEVERITY_GLYPH = {
  info:     "ⓘ",
  warning:  "⚠",
  critical: "⛔",
};


function _formatWhen(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60)        return "just now";
    if (diff < 3600)      return Math.floor(diff / 60) + " min ago";
    if (diff < 86400)     return Math.floor(diff / 3600) + " hr ago";
    if (diff < 604800)    return Math.floor(diff / 86400) + " d ago";
    return iso.slice(0, 10);
  } catch (e) {
    return iso.slice(0, 10);
  }
}


export function renderInbox(opts = {}) {
  const doc = opts.ownerDocument || document;
  const fetchFn = opts.fetchFn || ((u, o) => fetch(u, o));

  const wrap = doc.createElement("div");
  wrap.className = "inbox";
  wrap.dataset.testid = "inbox";

  const header = doc.createElement("div");
  header.className = "inbox-header";
  header.innerHTML = `
    <h2>Notifications</h2>
    <p class="inbox-subtitle">Last 30 days</p>
  `;
  wrap.appendChild(header);

  const list = doc.createElement("div");
  list.className = "inbox-list";
  wrap.appendChild(list);

  (async () => {
    let rows = [];
    try {
      const r = await fetchFn("/api/notifications/history?days=30");
      if (!r.ok) throw new Error("HTTP " + r.status);
      rows = await r.json();
    } catch (e) {
      list.innerHTML =
        `<p class="inbox-error">Failed to load notifications: ${e.message}</p>`;
      return;
    }

    if (!rows.length) {
      list.innerHTML =
        `<p class="inbox-empty" data-testid="inbox-empty">
           No notifications in the last 30 days. Configure preferences in
           <a href="/admin">Settings &rarr; Notifications</a>.
         </p>`;
      return;
    }

    list.innerHTML = rows.map(r => {
      const unread = !r.read_at;
      const sev = (r.severity || "info").toLowerCase();
      const cat = CATEGORY_LABELS[r.category] || r.category;
      const glyph = SEVERITY_GLYPH[sev] || "•";
      // Row is a div so test selector `[data-testid='inbox-row'] a` finds
      // the inner anchor. The anchor wraps the whole clickable area; the
      // CSS makes the row itself look like a tap target.
      return `
        <div class="inbox-row inbox-row--${unread ? 'unread' : 'read'}"
             data-testid="inbox-row">
          <a href="${r.deep_link || '/'}" class="inbox-row-link">
            <div class="inbox-row-icon">
              ${unread ? '<span class="inbox-dot inbox-dot--unread"></span>'
                       : '<span class="inbox-dot inbox-dot--read"></span>'}
            </div>
            <div class="inbox-row-body">
              <div class="inbox-row-top">
                <span class="inbox-severity inbox-severity--${sev}">${glyph} ${cat}</span>
                <span class="inbox-when">${_formatWhen(r.created_at)}</span>
              </div>
              <div class="inbox-title">${r.title || ""}</div>
              ${r.body ? `<div class="inbox-body">${r.body}</div>` : ""}
              ${r.event_count > 1
                ? `<div class="inbox-count">${r.event_count} events grouped</div>`
                : ""}
            </div>
          </a>
        </div>
      `;
    }).join("");

    // Mark-read POST if any unread present.
    if (rows.some(r => !r.read_at)) {
      try {
        await fetchFn("/api/notifications/history/mark-read",
                      { method: "POST" });
      } catch (e) { /* non-fatal -- the dot stays "unread" visually until reload */ }
    }
  })();

  return wrap;
}

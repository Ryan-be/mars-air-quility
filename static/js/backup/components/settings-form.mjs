/**
 * Settings form — renders the masked config returned by GET /config,
 * lets the operator edit it, and serialises a partial PUT body.
 *
 * Critical UX:
 *
 *   - Password fields NEVER reflect the actual password. The input
 *     value is always "", and the placeholder reads either
 *     "(unchanged)" — when the server reports password_set=true —
 *     or "(not set)" otherwise.
 *
 *   - On submit, an empty password field serialises as "" (not
 *     omitted) so the server explicitly sees a preserve-existing
 *     instruction (per the API contract documented in api_backup.py).
 *
 *   - `paused` and `*_set` booleans NEVER appear in the PUT payload.
 *     Paused is owned by the maintenance endpoint; *_set is a derived
 *     server-side flag.
 *
 * Two side-by-side cards: db + files. Each has its own Test Connection
 * and Initialise buttons that render result text inline.
 */


/** Helper: int-coerce a numeric input value, returning the default on NaN. */
function _intOr(input, fallback) {
  const v = parseInt(input.value, 10);
  return Number.isNaN(v) ? fallback : v;
}


/**
 * Build a single labelled text-input field row. Returns the wrapper
 * div with `<input data-field="<key>">` ready for query.
 */
function _field(doc, key, label, value, opts = {}) {
  const wrap = doc.createElement("div");
  wrap.className = "field";

  const lbl = doc.createElement("label");
  lbl.htmlFor = `bk-${key.replace(/\./g, "-")}`;
  lbl.textContent = label;
  wrap.appendChild(lbl);

  const input = doc.createElement("input");
  input.type = opts.type || "text";
  input.id = lbl.htmlFor;
  input.dataset.field = key;
  if (value != null) input.value = String(value);
  if (opts.placeholder) input.placeholder = opts.placeholder;
  if (opts.step != null) input.step = String(opts.step);
  if (opts.min  != null) input.min  = String(opts.min);
  wrap.appendChild(input);
  return wrap;
}


/**
 * Toggle row — a label + checkbox pair styled as a switch. We use a
 * plain <input type=checkbox> for cross-browser + JSDOM compatibility
 * (custom-element <rux-switch> doesn't expose .checked under JSDOM).
 * The template wraps it in a label that styles it as a switch.
 */
function _toggle(doc, key, labelText) {
  const wrap = doc.createElement("div");
  wrap.className = "toggle-row";

  const lbl = doc.createElement("span");
  lbl.textContent = labelText;
  wrap.appendChild(lbl);

  const cb = doc.createElement("input");
  cb.type = "checkbox";
  cb.dataset.field = key;
  cb.id = `bk-${key.replace(/\./g, "-")}`;
  cb.className = "bk-switch";
  wrap.appendChild(cb);

  return wrap;
}


/** Build the db pipeline card. */
function _renderDbCard(doc, dbCfg) {
  const card = doc.createElement("div");
  card.className = "card bk-settings-card";
  card.dataset.section = "db";
  card.innerHTML = `<h3>Database pipeline</h3>`;

  const enabled = _toggle(doc, "db.enabled", "Pipeline enabled");
  enabled.querySelector("input").checked = !!dbCfg.enabled;
  card.appendChild(enabled);

  const grid = doc.createElement("div");
  grid.className = "field-row";
  grid.appendChild(_field(doc, "db.host", "Host", dbCfg.host || "",
    { placeholder: "homeserver.lan" }));
  grid.appendChild(_field(doc, "db.port", "Port", dbCfg.port ?? 5432,
    { type: "number", min: 1, step: 1 }));
  card.appendChild(grid);

  const grid2 = doc.createElement("div");
  grid2.className = "field-row";
  grid2.appendChild(_field(doc, "db.database", "Database", dbCfg.database || "mlss"));
  grid2.appendChild(_field(doc, "db.user", "User", dbCfg.user || "mlss"));
  card.appendChild(grid2);

  // Password field — masked semantics.
  const pwWrap = doc.createElement("div");
  pwWrap.className = "field-row";
  pwWrap.appendChild(_field(
    doc, "db.password", "Password", "",
    {
      type: "password",
      placeholder: dbCfg.password_set ? "(unchanged)" : "(not set)",
    },
  ));
  card.appendChild(pwWrap);

  // Action buttons + inline result span.
  const actions = doc.createElement("div");
  actions.className = "bk-actions-row";
  actions.innerHTML = `
    <button type="button" class="btn-search" data-action="test-db">Test connection</button>
    <button type="button" class="btn-search" data-action="init-db">Initialise</button>
    <span class="bk-result" data-result="test-db"></span>
    <span class="bk-result" data-result="init-db"></span>
  `;
  card.appendChild(actions);
  return card;
}


/** Build the files pipeline card. */
function _renderFilesCard(doc, filesCfg) {
  const card = doc.createElement("div");
  card.className = "card bk-settings-card";
  card.dataset.section = "files";
  card.innerHTML = `<h3>Files pipeline (S3)</h3>`;

  const enabled = _toggle(doc, "files.enabled", "Pipeline enabled");
  enabled.querySelector("input").checked = !!filesCfg.enabled;
  card.appendChild(enabled);

  const grid = doc.createElement("div");
  grid.className = "field-row";
  grid.appendChild(_field(doc, "files.endpoint", "Endpoint",
    filesCfg.endpoint || "", { placeholder: "https://s3.example.com" }));
  grid.appendChild(_field(doc, "files.region", "Region",
    filesCfg.region || "auto"));
  card.appendChild(grid);

  const grid2 = doc.createElement("div");
  grid2.className = "field-row";
  grid2.appendChild(_field(doc, "files.access_key_id", "Access key ID",
    filesCfg.access_key_id || ""));
  grid2.appendChild(_field(doc, "files.bucket_prefix", "Bucket prefix",
    filesCfg.bucket_prefix || "mlss-"));
  card.appendChild(grid2);

  // Secret key — masked semantics.
  const skWrap = doc.createElement("div");
  skWrap.className = "field-row";
  skWrap.appendChild(_field(
    doc, "files.secret_key", "Secret access key", "",
    {
      type: "password",
      placeholder: filesCfg.secret_key_set ? "(unchanged)" : "(not set)",
    },
  ));
  card.appendChild(skWrap);

  const actions = doc.createElement("div");
  actions.className = "bk-actions-row";
  actions.innerHTML = `
    <button type="button" class="btn-search" data-action="test-files">Test connection</button>
    <button type="button" class="btn-search" data-action="init-files">Initialise</button>
    <span class="bk-result" data-result="test-files"></span>
    <span class="bk-result" data-result="init-files"></span>
  `;
  card.appendChild(actions);
  return card;
}


/**
 * Render the settings form. Returns the wrapping <form> element with
 * `.serialize()` attached so callers can read the partial PUT body
 * without re-implementing the form-walk.
 */
export function renderSettingsForm({ config, ownerDocument, fetchFn }) {
  const doc = ownerDocument || document;
  const f = fetchFn || ((u, o) => fetch(u, o));

  const form = doc.createElement("form");
  form.className = "bk-settings-form";

  // Master toggle row at the top.
  const master = _toggle(doc, "enabled", "Backup enabled (master switch)");
  master.querySelector("input").checked = !!config.enabled;
  form.appendChild(master);

  // The two side-by-side pipeline cards.
  const grid = doc.createElement("div");
  grid.className = "settings-grid bk-pipeline-grid";
  grid.appendChild(_renderDbCard(doc, config.db || {}));
  grid.appendChild(_renderFilesCard(doc, config.files || {}));
  form.appendChild(grid);

  // -- Inline button handlers --
  form.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (!/^(test|init)-(db|files)$/.test(action)) return;
    ev.preventDefault();

    const [verb, pipeline] = action.split("-");
    const url = `/api/admin/backup/${verb}?pipeline=${pipeline}`;
    const result = form.querySelector(`[data-result="${action}"]`);
    if (result) {
      result.textContent = "…";
      result.className = "bk-result";
    }

    try {
      const r = await f(url, { method: "POST" });
      const data = await r.json();
      if (data.ok === false) {
        if (result) {
          result.textContent = data.error || "Failed";
          result.className = "bk-result bk-result-err";
        }
        return;
      }
      if (result) {
        const okText = verb === "test"
          ? `Connected${data.version ? ` (${data.version})` : ""}`
          : (data.message || (data.buckets_created
              ? `Created: ${data.buckets_created.join(", ")}`
              : "OK"));
        result.textContent = okText;
        result.className = "bk-result bk-result-ok";
      }
    } catch (exc) {
      if (result) {
        result.textContent = `Failed: ${exc.message}`;
        result.className = "bk-result bk-result-err";
      }
    }
  });

  /**
   * Serialise the form into a partial PUT /config payload.
   *
   * Includes the master `enabled` flag, `db.*`, `files.*`, and
   * `advanced.*` (untouched in v1 — the current connection_timeout_s
   * lives in config so we preserve it by echoing). Explicitly omits
   * `paused`, `password_set`, and `secret_key_set`.
   */
  form.serialize = function () {
    const get = (k) => form.querySelector(`[data-field='${k}']`);
    const text = (k) => (get(k)?.value ?? "");
    const num = (k, fb) => _intOr(get(k), fb);
    const check = (k) => !!get(k)?.checked;

    return {
      enabled: check("enabled"),
      db: {
        enabled: check("db.enabled"),
        host: text("db.host"),
        port: num("db.port", 5432),
        database: text("db.database"),
        user: text("db.user"),
        // EMPTY = preserve existing on server side
        password: text("db.password"),
      },
      files: {
        enabled: check("files.enabled"),
        endpoint: text("files.endpoint"),
        region: text("files.region") || "auto",
        access_key_id: text("files.access_key_id"),
        bucket_prefix: text("files.bucket_prefix") || "mlss-",
        // EMPTY = preserve existing on server side
        secret_key: text("files.secret_key"),
      },
      advanced: {
        outbox_cap_mb: config.advanced?.outbox_cap_mb ?? 500,
        connection_timeout_s: config.advanced?.connection_timeout_s ?? 10,
      },
    };
  };

  return form;
}

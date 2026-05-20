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


/**
 * Card specs — single source of truth for the two pipeline cards. Each
 * row of `fields` becomes one `<div class="field-row">`; each `[key,
 * label, attrs?]` triple inside a row becomes one labelled input. The
 * `secret` entry adds a masked password-style input that reads its
 * "(unchanged)" / "(not set)" placeholder from `cfg[secret.set_key]`.
 *
 * Keeping the spec in one place means adding a field to "both"
 * pipelines is exactly one diff, not two.
 */
const CARDS = [
  {
    section: "db",
    title: "Database pipeline",
    fields: [
      [["host",     "Host",     { placeholder: "homeserver.lan" }],
       ["port",     "Port",     { type: "number", min: 1, step: 1, default: 5432 }]],
      [["database", "Database", { default: "mlss" }],
       ["user",     "User",     { default: "mlss" }]],
    ],
    secret: { key: "password",   label: "Password",          setKey: "password_set" },
  },
  {
    section: "files",
    title: "Files pipeline (S3)",
    fields: [
      [["endpoint",      "Endpoint",      { placeholder: "https://s3.example.com" }],
       ["region",        "Region",        { default: "auto" }]],
      [["access_key_id", "Access key ID"],
       ["bucket_prefix", "Bucket prefix", { default: "mlss-" }]],
    ],
    secret: { key: "secret_key", label: "Secret access key", setKey: "secret_key_set" },
  },
];


/** Build one pipeline card from a CARDS spec entry. */
function _renderCard(doc, cfg, spec) {
  const sect = spec.section;
  const card = doc.createElement("div");
  card.className = "card bk-settings-card";
  card.dataset.section = sect;
  card.innerHTML = `<h3>${spec.title}</h3>`;

  const enabled = _toggle(doc, `${sect}.enabled`, "Pipeline enabled");
  enabled.querySelector("input").checked = !!cfg.enabled;
  card.appendChild(enabled);

  // Field rows — each inner array of triples becomes one .field-row.
  for (const row of spec.fields) {
    const rowEl = doc.createElement("div");
    rowEl.className = "field-row";
    for (const [name, label, attrs = {}] of row) {
      const { default: dflt, ...inputAttrs } = attrs;
      const value = cfg[name] ?? dflt ?? "";
      rowEl.appendChild(_field(doc, `${sect}.${name}`, label, value, inputAttrs));
    }
    card.appendChild(rowEl);
  }

  // Secret field — masked semantics. Always its own row.
  const { key: sKey, label: sLabel, setKey } = spec.secret;
  const secretRow = doc.createElement("div");
  secretRow.className = "field-row";
  secretRow.appendChild(_field(
    doc, `${sect}.${sKey}`, sLabel, "",
    {
      type: "password",
      placeholder: cfg[setKey] ? "(unchanged)" : "(not set)",
    },
  ));
  card.appendChild(secretRow);

  // Action buttons + inline result spans.
  const actions = doc.createElement("div");
  actions.className = "bk-actions-row";
  actions.innerHTML = `
    <button type="button" class="btn-search" data-action="test-${sect}">Test connection</button>
    <button type="button" class="btn-search" data-action="init-${sect}">Initialise</button>
    <span class="bk-result" data-result="test-${sect}"></span>
    <span class="bk-result" data-result="init-${sect}"></span>
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
  for (const spec of CARDS) {
    grid.appendChild(_renderCard(doc, config[spec.section] || {}, spec));
  }
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

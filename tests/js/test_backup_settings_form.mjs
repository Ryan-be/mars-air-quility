/**
 * Tests for the admin backup Settings Form component.
 *
 * Two side-by-side cards (db + files). Each has a per-pipeline enabled
 * toggle, connection fields, a masked password field, plus
 * "Test connection" and "Initialise" buttons.
 *
 * Critical behaviour under test:
 *   - The password field NEVER reflects the actual password.
 *   - "(unchanged)" placeholder when password_set is true.
 *   - "(not set)" placeholder when password_set is false.
 *   - Submitting with a blank password preserves the existing one.
 *   - Submitting a partial config sends ONLY the fields the form covers
 *     (the API contract states missing fields are preserved server-side).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderSettingsForm }
  from "../../static/js/backup/components/settings-form.mjs";

const dom = new JSDOM();
global.document = dom.window.document;
global.window = dom.window;


/** Helper to build a masked config (as returned by GET /config). */
function _cfg(overrides = {}) {
  return {
    enabled: false,
    paused: false,
    db: {
      enabled: false,
      host: "",
      port: 5432,
      database: "mlss",
      user: "mlss",
      password_set: false,
      ...((overrides.db) || {}),
    },
    files: {
      enabled: false,
      endpoint: "",
      region: "auto",
      access_key_id: "",
      secret_key_set: false,
      bucket_prefix: "mlss-",
      ...((overrides.files) || {}),
    },
    advanced: {
      outbox_cap_mb: 500,
      connection_timeout_s: 10,
      ...((overrides.advanced) || {}),
    },
    ...Object.fromEntries(
      Object.entries(overrides).filter(([k]) =>
        !["db", "files", "advanced"].includes(k))),
  };
}


async function _flushMicro() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}


test("settings form: password_set=true shows '(unchanged)' placeholder", () => {
  const form = renderSettingsForm({
    config: _cfg({ db: { password_set: true, host: "homeserver.lan" } }),
    ownerDocument: document,
  });
  const pwInput = form.querySelector("[data-field='db.password']");
  assert.ok(pwInput, "password input must exist");
  assert.equal(pwInput.value, "",
    "password input value MUST be empty (no cleartext)");
  assert.match(pwInput.placeholder, /\(unchanged\)/i,
    "placeholder must read '(unchanged)' when password is set");
  assert.equal(pwInput.type, "password",
    "input must be type=password");
});


test("settings form: password_set=false shows '(not set)' placeholder", () => {
  const form = renderSettingsForm({
    config: _cfg({ db: { password_set: false } }),
    ownerDocument: document,
  });
  const pwInput = form.querySelector("[data-field='db.password']");
  assert.equal(pwInput.value, "");
  assert.match(pwInput.placeholder, /\(not set\)/i);
});


test("settings form: blank password in submit becomes empty string (preserve)", () => {
  // The API contract: "Empty-string passwords mean preserve existing".
  // So the form's serialiser must include the empty string, NOT omit
  // the field, so the server explicitly sees a no-op preserve.
  const form = renderSettingsForm({
    config: _cfg({ db: { password_set: true, host: "homeserver.lan" } }),
    ownerDocument: document,
  });
  const payload = form.serialize();
  assert.equal(payload.db.password, "",
    "blank password must serialise to '' (preserve semantic)");
});


test("settings form: non-blank password is sent verbatim", () => {
  const form = renderSettingsForm({
    config: _cfg({ db: { password_set: false } }),
    ownerDocument: document,
  });
  const pwInput = form.querySelector("[data-field='db.password']");
  pwInput.value = "newpassword123";
  const payload = form.serialize();
  assert.equal(payload.db.password, "newpassword123");
});


test("settings form: serialize includes db + files + advanced sections", () => {
  const form = renderSettingsForm({
    config: _cfg({
      enabled: true,
      db: { enabled: true, host: "h", database: "mlss", user: "u", port: 5432 },
      files: { enabled: true, endpoint: "e", access_key_id: "ak", bucket_prefix: "p-" },
    }),
    ownerDocument: document,
  });
  const payload = form.serialize();
  assert.equal(typeof payload.enabled, "boolean");
  assert.ok(payload.db);
  assert.ok(payload.files);
  assert.ok(payload.advanced);
  assert.equal(payload.db.host, "h");
  assert.equal(payload.files.endpoint, "e");
});


test("settings form: port is sent as integer not string", () => {
  // Server expects port: int; serialise must coerce so 5432 doesn't
  // arrive as "5432".
  const form = renderSettingsForm({
    config: _cfg({ db: { port: 5432 } }),
    ownerDocument: document,
  });
  const portInput = form.querySelector("[data-field='db.port']");
  portInput.value = "5433";
  const payload = form.serialize();
  assert.equal(payload.db.port, 5433);
  assert.equal(typeof payload.db.port, "number");
});


test("settings form: toggling pipeline enabled flips payload.{db,files}.enabled", () => {
  const form = renderSettingsForm({
    config: _cfg({
      db: { enabled: false },
      files: { enabled: false },
    }),
    ownerDocument: document,
  });
  const dbToggle = form.querySelector("[data-field='db.enabled']");
  // <rux-switch> uses a `checked` property — but we also support the
  // plain DOM `<input type=checkbox>` shim for JSDOM (which doesn't
  // know about custom elements).
  dbToggle.checked = true;
  const payload = form.serialize();
  assert.equal(payload.db.enabled, true);
  assert.equal(payload.files.enabled, false);
});


test("settings form: Test Connection button triggers API call", async () => {
  let testCalled = null;
  const form = renderSettingsForm({
    config: _cfg({ db: { enabled: true, host: "h", password_set: true } }),
    ownerDocument: document,
    fetchFn: async (url, opts) => {
      testCalled = { url, opts };
      return new Response(
        JSON.stringify({ ok: true, version: "PostgreSQL 16.0" }),
        { status: 200 },
      );
    },
  });
  const btn = form.querySelector("[data-action='test-db']");
  assert.ok(btn, "test button must exist for db pipeline");
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.ok(testCalled);
  assert.match(testCalled.url, /\/api\/admin\/backup\/test\?pipeline=db/);
  assert.equal(testCalled.opts.method, "POST");
  // Result feedback rendered inline
  const result = form.querySelector("[data-result='test-db']");
  assert.ok(result);
  assert.match(result.textContent, /connected|ok|PostgreSQL/i);
});


test("settings form: Test Connection failure renders error", async () => {
  const form = renderSettingsForm({
    config: _cfg({ db: { enabled: true, host: "h" } }),
    ownerDocument: document,
    fetchFn: async () => new Response(
      JSON.stringify({ ok: false, error: "connection refused" }),
      { status: 200 },
    ),
  });
  form.querySelector("[data-action='test-db']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  const result = form.querySelector("[data-result='test-db']");
  assert.match(result.textContent, /connection refused|failed/i);
  assert.match(result.className, /bk-result-err|err/);
});


test("settings form: Initialise button triggers init API call", async () => {
  let captured = null;
  const form = renderSettingsForm({
    config: _cfg({ files: { enabled: true } }),
    ownerDocument: document,
    fetchFn: async (url, opts) => {
      captured = { url, opts };
      return new Response(
        JSON.stringify({ ok: true, buckets_created: ["mlss-photos"] }),
        { status: 200 },
      );
    },
  });
  form.querySelector("[data-action='init-files']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await _flushMicro();
  assert.ok(captured);
  assert.match(captured.url, /\/api\/admin\/backup\/init\?pipeline=files/);
  assert.equal(captured.opts.method, "POST");
});


test("settings form: secret_key field has same '(unchanged)' semantics", () => {
  const form = renderSettingsForm({
    config: _cfg({ files: { secret_key_set: true, access_key_id: "AKIA..." } }),
    ownerDocument: document,
  });
  const sk = form.querySelector("[data-field='files.secret_key']");
  assert.ok(sk);
  assert.equal(sk.value, "");
  assert.match(sk.placeholder, /\(unchanged\)/i);
  assert.equal(sk.type, "password");
});


test("settings form: master enabled toggle reflects + serialises", () => {
  const form = renderSettingsForm({
    config: _cfg({ enabled: false }),
    ownerDocument: document,
  });
  const masterToggle = form.querySelector("[data-field='enabled']");
  assert.ok(masterToggle, "master enabled toggle must exist");
  masterToggle.checked = true;
  const payload = form.serialize();
  assert.equal(payload.enabled, true);
});


test("settings form: never includes password_set in payload", () => {
  // password_set is a server-side derived field; submitting it back
  // would be confusing. Only `password` (the actual cleartext on
  // submit) belongs in PUT bodies.
  const form = renderSettingsForm({
    config: _cfg({ db: { password_set: true } }),
    ownerDocument: document,
  });
  const payload = form.serialize();
  assert.equal(payload.db.password_set, undefined,
    "password_set must NOT appear in PUT payload");
  assert.equal(payload.files.secret_key_set, undefined,
    "secret_key_set must NOT appear in PUT payload");
});


test("settings form: never includes paused in PUT payload", () => {
  // `paused` is owned by the maintenance endpoint, not the config PUT.
  // Avoid duplicate sources of truth.
  const form = renderSettingsForm({
    config: _cfg({ paused: false }),
    ownerDocument: document,
  });
  const payload = form.serialize();
  assert.equal(payload.paused, undefined,
    "paused must NOT appear in PUT /config payload");
});

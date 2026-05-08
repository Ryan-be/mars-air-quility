/**
 * Plant journal editor — Phase 4 #7 frontend.
 *
 * Tests:
 *   - List rendering, including "no notes" empty state
 *   - Composer visibility per role (viewer hides; controller/admin show)
 *   - Edit/delete buttons per (author, role) pair
 *   - POST flow + journal-changed CustomEvent emission
 *   - PATCH flow with edit form
 *   - DELETE flow
 *   - _localToIsoUtc helper round-trip
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderJournalEditor,
  _localToIsoUtc,
} from "../../static/js/grow/components/journal-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;
// Some renderers construct CustomEvent / Event from `globalThis`; expose
// JSDOM's CustomEvent as the global so dispatchEvent() works the same way
// it would in the browser.
global.CustomEvent = dom.window.CustomEvent;


function _flushMicro() {
  // 16 microtask ticks gives the editor's async _refresh + emit cycle
  // plenty of room to finish before assertions.
  return new Promise((resolve) => {
    let ticks = 16;
    const next = () => {
      if (--ticks <= 0) return resolve();
      Promise.resolve().then(next);
    };
    next();
  });
}


function _stubFetch(routes) {
  /** routes: { "GET:/api/...": (urlObj) => responseBody | Response }. */
  return async (url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const key = `${method}:${url}`;
    if (!(key in routes)) {
      return new Response(JSON.stringify({ error: "no stub" }), { status: 404 });
    }
    const handler = routes[key];
    const result = await handler(url, opts);
    if (result instanceof Response) return result;
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}


function _entry(overrides = {}) {
  return {
    id: 100,
    unit_id: 7,
    timestamp_utc: "2026-05-08T12:00:00",
    author: "alice",
    body: "started bloom nutrients",
    created_at: "2026-05-08T12:00:00",
    updated_at: null,
    ...overrides,
  };
}


// ─── Helper: _localToIsoUtc ─────────────────────────────────────────────


test("_localToIsoUtc converts a local datetime-string to ISO8601 UTC", () => {
  // Pick a value the JS Date constructor will parse the same way across
  // platforms — naive local strings respect the JS engine's TZ.
  const out = _localToIsoUtc("2026-05-08T12:00");
  // The output is whatever ISO toISOString returns for that local time;
  // the only thing we care about here is round-trippability.
  assert.match(out, /^2026-05-0[78]T\d\d:\d\d:00\.000Z$/);
});


test("_localToIsoUtc returns null on invalid input", () => {
  assert.equal(_localToIsoUtc("not a date"), null);
});


// ─── List rendering ────────────────────────────────────────────────────


test("empty journal shows 'No notes yet'", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();
  const list = el.querySelector("[data-testid='journal-list']");
  assert.match(list.textContent, /No notes yet/);
});


test("renders one entry per row with timestamp + author + body", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [
      _entry({ id: 100, body: "first" }),
      _entry({ id: 101, body: "second", author: "bob" }),
    ],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "admin",
    fetchFn,
  });
  await _flushMicro();
  const rows = el.querySelectorAll("[data-testid^='journal-entry-']");
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /first/);
  assert.match(rows[0].textContent, /alice/);
  assert.match(rows[1].textContent, /second/);
  assert.match(rows[1].textContent, /bob/);
});


test("(edited) suffix shows when updated_at is set", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [
      _entry({ updated_at: "2026-05-08T13:00:00" }),
    ],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();
  assert.match(
    el.querySelector(".journal-entry-edited")?.textContent || "",
    /edited/,
  );
});


// ─── Composer visibility per role ──────────────────────────────────────


test("composer hidden for viewer role", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentRole: "viewer", fetchFn,
  });
  await _flushMicro();
  assert.equal(el.querySelector("[data-testid='journal-composer']"), null);
});


test("composer visible for controller", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentRole: "controller", fetchFn,
  });
  await _flushMicro();
  assert.ok(el.querySelector("[data-testid='journal-composer']"));
});


// ─── Edit/delete buttons per (author, role) ────────────────────────────


test("controller sees edit/delete on their own row", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [_entry({ author: "alice" })],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();
  assert.ok(el.querySelector(".journal-edit-btn"));
  assert.ok(el.querySelector(".journal-delete-btn"));
});


test("controller does NOT see edit/delete on someone else's row", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [_entry({ author: "bob" })],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();
  assert.equal(el.querySelector(".journal-edit-btn"), null);
  assert.equal(el.querySelector(".journal-delete-btn"), null);
});


test("admin sees edit/delete on someone else's row", async () => {
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [_entry({ author: "bob" })],
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "admin",
    fetchFn,
  });
  await _flushMicro();
  assert.ok(el.querySelector(".journal-edit-btn"));
  assert.ok(el.querySelector(".journal-delete-btn"));
});


// ─── POST flow ─────────────────────────────────────────────────────────


test("submitting composer POSTs body + emits journal-changed", async () => {
  let postedBody = null;
  let postedTs = null;
  let getCount = 0;
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => { getCount++; return []; },
    "POST:/api/grow/units/7/journal": (_url, opts) => {
      const parsed = JSON.parse(opts.body);
      postedBody = parsed.body;
      postedTs = parsed.timestamp_utc;
      return new Response(JSON.stringify(_entry({ body: parsed.body })), {
        status: 201,
      });
    },
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();

  let changedFired = 0;
  el.addEventListener("journal-changed", () => { changedFired++; });

  const composer = el.querySelector("[data-testid='journal-composer']");
  composer.querySelector(".journal-composer-body").value = "watered today";
  composer.querySelector(".journal-composer-submit").click();
  await _flushMicro();

  assert.equal(postedBody, "watered today");
  assert.match(postedTs, /T\d\d:\d\d/);
  assert.ok(changedFired >= 1, "journal-changed must fire after a successful POST");
  assert.ok(getCount >= 2, "list must re-fetch after a successful POST");
});


test("submitting empty body does NOT POST", async () => {
  let postCount = 0;
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [],
    "POST:/api/grow/units/7/journal": () => { postCount++; return _entry(); },
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();

  const composer = el.querySelector("[data-testid='journal-composer']");
  // Body left blank
  composer.querySelector(".journal-composer-submit").click();
  await _flushMicro();
  assert.equal(postCount, 0);
});


// ─── DELETE flow ───────────────────────────────────────────────────────


test("clicking Delete sends DELETE + re-fetches + emits", async () => {
  let deleted = false;
  let entries = [_entry({ author: "alice" })];
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => entries,
    "DELETE:/api/grow/units/7/journal/100": () => {
      deleted = true;
      entries = [];
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    },
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();

  let changedFired = 0;
  el.addEventListener("journal-changed", () => { changedFired++; });

  el.querySelector(".journal-delete-btn").click();
  await _flushMicro();
  assert.equal(deleted, true);
  assert.ok(changedFired >= 1);
  assert.match(
    el.querySelector("[data-testid='journal-list']").textContent,
    /No notes yet/,
  );
});


// ─── PATCH (edit) flow ─────────────────────────────────────────────────


test("clicking Edit swaps in textarea; Save sends PATCH + emits", async () => {
  let patchedBody = null;
  let entries = [_entry({ author: "alice", body: "orig" })];
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => entries,
    "PATCH:/api/grow/units/7/journal/100": (_url, opts) => {
      patchedBody = JSON.parse(opts.body).body;
      entries = [_entry({ author: "alice", body: patchedBody, updated_at: "x" })];
      return new Response(JSON.stringify(entries[0]), { status: 200 });
    },
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();

  let changedFired = 0;
  el.addEventListener("journal-changed", () => { changedFired++; });

  el.querySelector(".journal-edit-btn").click();
  // textarea now in place
  const ta = el.querySelector(".journal-edit-textarea");
  assert.ok(ta, "edit-mode textarea should be visible");
  ta.value = "edited body";
  el.querySelector(".journal-edit-save").click();
  await _flushMicro();

  assert.equal(patchedBody, "edited body");
  assert.ok(changedFired >= 1);
  assert.match(
    el.querySelector("[data-testid='journal-list']").textContent,
    /edited body/,
  );
});


test("clicking Cancel during edit reverts to read-only without PATCH", async () => {
  let patchCount = 0;
  const fetchFn = _stubFetch({
    "GET:/api/grow/units/7/journal?range=7d": () => [
      _entry({ author: "alice", body: "orig" }),
    ],
    "PATCH:/api/grow/units/7/journal/100": () => {
      patchCount++;
      return _entry();
    },
  });
  const el = renderJournalEditor({ id: 7 }, {
    ownerDocument: document, currentUser: "alice", currentRole: "controller",
    fetchFn,
  });
  await _flushMicro();
  el.querySelector(".journal-edit-btn").click();
  el.querySelector(".journal-edit-cancel").click();
  await _flushMicro();
  assert.equal(patchCount, 0);
  // Read-only body comes back
  assert.match(
    el.querySelector("[data-testid='journal-list']").textContent,
    /orig/,
  );
});

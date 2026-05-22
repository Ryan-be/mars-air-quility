/**
 * Tests for the Notifications inbox component.
 * Renders into jsdom, mocks fetch, verifies render + mark-read flow.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderInbox } from "../../static/js/notifications/inbox.mjs";


function _setup() {
  const dom = new JSDOM(
    "<!DOCTYPE html><html><body><div id='host'></div></body></html>"
  );
  Object.defineProperty(globalThis, "document", {
    configurable: true, get: () => dom.window.document,
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true, get: () => dom.window,
  });
  return dom;
}

async function _flush() { for (let i = 0; i < 6; i++) await Promise.resolve(); }


const _SAMPLE = [
  { id: 1, category: "air_quality", severity: "warning",
    title: "TVOC spike", body: "Elevated on SGP30",
    deep_link: "/incidents", event_count: 2,
    delivered_count: 1, failed_count: 0,
    created_at: "2026-05-20T12:00:00", read_at: null },
  { id: 2, category: "backup_pipeline", severity: "warning",
    title: "Backup db: BACKOFF", body: "Backoff 120s, 47 pending",
    deep_link: "/admin/backup", event_count: 1,
    delivered_count: 1, failed_count: 0,
    created_at: "2026-05-19T11:00:00", read_at: "2026-05-19T11:30:00" },
];


test("inbox: renders rows from API", async () => {
  _setup();
  const fetchFn = async (url) => {
    if (url.startsWith("/api/notifications/history"))
      return { ok: true, json: async () => _SAMPLE };
    return { ok: true, json: async () => ({}) };
  };
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const rows = root.querySelectorAll("[data-testid='inbox-row']");
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /TVOC spike/);
  assert.match(rows[1].textContent, /BACKOFF/);
});


test("inbox: unread row marked with 'unread' class, read row with 'read'", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => _SAMPLE });
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const rows = root.querySelectorAll("[data-testid='inbox-row']");
  assert.ok(rows[0].classList.contains("inbox-row--unread"));
  assert.ok(rows[1].classList.contains("inbox-row--read"));
});


test("inbox: row anchor href is the deep_link", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => _SAMPLE });
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const link = root.querySelector("[data-testid='inbox-row'] a");
  assert.equal(link.getAttribute("href"), "/incidents");
});


test("inbox: POSTs mark-read after render when any unread present", async () => {
  _setup();
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET" });
    if (url.startsWith("/api/notifications/history") && !opts.method) {
      return { ok: true, json: async () => _SAMPLE };
    }
    return { ok: true, json: async () => ({ count: 1 }) };
  };
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const post = calls.find(c => c.method === "POST"
                            && c.url === "/api/notifications/history/mark-read");
  assert.ok(post, "POST to mark-read was sent");
});


test("inbox: does NOT POST mark-read when no unread rows", async () => {
  _setup();
  const allRead = _SAMPLE.map(r => ({ ...r, read_at: "2026-05-20T00:00:00" }));
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET" });
    if (url.startsWith("/api/notifications/history") && !opts.method) {
      return { ok: true, json: async () => allRead };
    }
    return { ok: true, json: async () => ({ count: 0 }) };
  };
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const post = calls.find(c => c.method === "POST");
  assert.equal(post, undefined);
});


test("inbox: empty state when no rows", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => [] });
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const empty = root.querySelector("[data-testid='inbox-empty']");
  assert.ok(empty);
  assert.match(empty.textContent.toLowerCase(), /no notifications/);
});


test("inbox: severity badge reflects severity class", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => _SAMPLE });
  const root = renderInbox({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(root);
  await _flush();
  const badge = root.querySelector("[data-testid='inbox-row'] .inbox-severity");
  assert.ok(badge);
  assert.ok(badge.classList.contains("inbox-severity--warning"));
});

/**
 * Tests for the Notifications settings card.
 * Renders the card body into a jsdom DOM, mocks fetch, verifies:
 *   - severity dropdowns load + reflect current values
 *   - Save preferences PATCHes to /api/notifications/preferences
 *   - subscription list renders, Remove sends DELETE
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderNotificationsCard } from
  "../../static/js/notifications/settings.mjs";

function _setup() {
  const dom = new JSDOM("<!DOCTYPE html><html><body><div id='host'></div></body></html>");
  global.document = dom.window.document;
  global.window = dom.window;
  global.HTMLElement = dom.window.HTMLElement;
  global.Event = dom.window.Event;
  return dom;
}

async function _flush() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}


test("notifications card: renders 4 category selects with current values", async () => {
  _setup();
  const fetchFn = async (url) => {
    if (url === "/api/notifications/preferences") {
      return { ok: true, json: async () => ({
        air_quality: "warning", grow_units: "off",
        system_health: "critical", backup_pipeline: "info",
      })};
    }
    if (url === "/api/notifications/subscriptions") {
      return { ok: true, json: async () => [] };
    }
    return { ok: true, json: async () => ({}) };
  };
  const card = renderNotificationsCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();

  const aq = card.querySelector("[data-pref='air_quality']");
  assert.ok(aq);
  assert.equal(aq.value, "warning");
  const gu = card.querySelector("[data-pref='grow_units']");
  assert.equal(gu.value, "off");
  assert.equal(card.querySelector("[data-pref='system_health']").value, "critical");
  assert.equal(card.querySelector("[data-pref='backup_pipeline']").value, "info");
});


test("notifications card: each select has all 4 options", async () => {
  _setup();
  const card = renderNotificationsCard({
    fetchFn: async () => ({ ok: true, json: async () => ({}) }),
    ownerDocument: document,
  });
  await _flush();
  const select = card.querySelector("[data-pref='air_quality']");
  const opts = [...select.querySelectorAll("option")].map(o => o.value);
  assert.deepStrictEqual(opts.sort(),
    ["critical", "info", "off", "warning"]);
});


test("notifications card: Save PATCHes new values", async () => {
  _setup();
  const calls = [];
  const fetchFn = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    if (url === "/api/notifications/preferences" && !opts.method) {
      return { ok: true, json: async () => ({
        air_quality: "warning", grow_units: "warning",
        system_health: "warning", backup_pipeline: "warning",
      })};
    }
    if (url === "/api/notifications/subscriptions" && !opts.method) {
      return { ok: true, json: async () => [] };
    }
    return { ok: true, json: async () => ({ message: "Preferences saved" }) };
  };
  const card = renderNotificationsCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();

  // Change one select
  const aq = card.querySelector("[data-pref='air_quality']");
  aq.value = "critical";
  aq.dispatchEvent(new Event("change", { bubbles: true }));

  // Click save
  const save = card.querySelector("[data-testid='notif-save']");
  assert.ok(save);
  save.click();
  await _flush();

  const patchCall = calls.find(c => c.method === "PATCH");
  assert.ok(patchCall, "PATCH was sent");
  const body = JSON.parse(patchCall.body);
  assert.equal(body.air_quality, "critical");
});


test("notifications card: subscription list renders + Remove DELETEs", async () => {
  _setup();
  const calls = [];
  let subs = [
    { id: 1, device_label: "Alice iPhone", created_at: "2026-05-20",
      last_used_at: "2026-05-20" },
    { id: 2, device_label: "", created_at: "2026-05-20",
      last_used_at: null },
  ];
  const fetchFn = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET" });
    if (url === "/api/notifications/preferences" && !opts.method) {
      return { ok: true, json: async () => ({}) };
    }
    if (url === "/api/notifications/subscriptions" && !opts.method) {
      return { ok: true, json: async () => subs };
    }
    if (url.startsWith("/api/notifications/subscriptions/") && opts.method === "DELETE") {
      const id = parseInt(url.split("/").pop());
      subs = subs.filter(s => s.id !== id);
      return { ok: true, json: async () => ({ message: "Unsubscribed" }) };
    }
    return { ok: true, json: async () => ({}) };
  };
  const card = renderNotificationsCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();

  const rows = card.querySelectorAll("[data-testid='notif-device-row']");
  assert.equal(rows.length, 2);

  const removeBtn = rows[0].querySelector("[data-testid='notif-device-remove']");
  assert.ok(removeBtn);
  removeBtn.click();
  await _flush();

  const deleteCall = calls.find(c => c.method === "DELETE");
  assert.ok(deleteCall, "DELETE was sent");
  assert.match(deleteCall.url, /\/subscriptions\/1$/);
});


test("notifications card: empty subscription list shows hint", async () => {
  _setup();
  const fetchFn = async (url) => {
    if (url === "/api/notifications/subscriptions") {
      return { ok: true, json: async () => [] };
    }
    return { ok: true, json: async () => ({}) };
  };
  const card = renderNotificationsCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();
  const empty = card.querySelector("[data-testid='notif-devices-empty']");
  assert.ok(empty);
  assert.match(empty.textContent.toLowerCase(), /no devices/);
});

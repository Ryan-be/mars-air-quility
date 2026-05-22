/**
 * Tests for the Mobile install settings card.
 * Renders the card body into a jsdom DOM, mocks fetch on /api/admin/tls/status,
 * verifies:
 *   - download anchors point to the iOS profile + CA cert endpoints
 *   - "View install instructions" toggles the instructions panel
 *   - TLS status line reflects ca_exists from the server
 *   - missing CA renders an actionable warning
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderMobileInstallCard } from
  "../../static/js/notifications/mobile-install.mjs";

function _setup() {
  const dom = new JSDOM("<!DOCTYPE html><html><body><div id='host'></div></body></html>");
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}

async function _flush() { for (let i = 0; i < 4; i++) await Promise.resolve(); }


test("mobile install card: download anchors point to right endpoints", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => ({
    ca_exists: true, cert_exists: true, cert_not_after: "2031-05-20T00:00:00",
  })});
  const card = renderMobileInstallCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();
  const profileLink = card.querySelector("[data-testid='mi-profile-link']");
  assert.equal(profileLink.getAttribute("href"),
               "/api/admin/tls/ios-profile.mobileconfig");
  const caLink = card.querySelector("[data-testid='mi-ca-link']");
  assert.equal(caLink.getAttribute("href"), "/api/admin/tls/ca.crt");
});


test("mobile install card: 'View instructions' toggles the panel", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => ({}) });
  const card = renderMobileInstallCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();
  const panel = card.querySelector("[data-testid='mi-instructions']");
  assert.ok(panel);
  assert.equal(panel.style.display, "none");
  const btn = card.querySelector("[data-testid='mi-instructions-btn']");
  btn.click();
  assert.notEqual(panel.style.display, "none");
});


test("mobile install card: shows TLS status from /api/admin/tls/status", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => ({
    ca_exists: true, cert_exists: true, cert_not_after: "2031-05-20T00:00:00",
  })});
  const card = renderMobileInstallCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();
  const status = card.querySelector("[data-testid='mi-status']");
  assert.ok(status);
  assert.match(status.textContent, /CA.*(present|OK|✓)/i);
});


test("mobile install card: warns when CA is missing", async () => {
  _setup();
  const fetchFn = async () => ({ ok: true, json: async () => ({
    ca_exists: false, cert_exists: false, cert_not_after: null,
  })});
  const card = renderMobileInstallCard({ fetchFn, ownerDocument: document });
  document.getElementById("host").appendChild(card);
  await _flush();
  const status = card.querySelector("[data-testid='mi-status']");
  assert.match(status.textContent.toLowerCase(), /run.*generate_local_ca|ca.*not found|missing/);
});

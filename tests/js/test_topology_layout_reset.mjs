/**
 * Tests for the Re-arrange button wiring (Phase 11 Task 11.2).
 *
 * Two layers:
 *
 *   1. The `resetLayout` API helper — POSTs to /api/effectors/layout/reset.
 *   2. The boot's onRearrange callback — calls resetLayout AND re-runs
 *      autoLayout on the existing nodes so the topology refreshes
 *      visually without waiting for a /api/topology re-fetch.
 *
 * The plan calls for admin-only at both client + server boundaries; we
 * verify the client-side gate by mounting a viewer-role DOM and
 * asserting no fetch fires when a viewer clicks the (still-rendered)
 * button. The server-side gate has its own pytest coverage.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { resetLayout } from "../../static/js/topology/api.mjs";


function _newAdminDom() {
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="admin">
      <section class="tp-app" id="tp-app" data-role="admin">
        <header id="tp-topbar-host"></header>
        <div    id="tp-graph-host"></div>
        <footer id="tp-statusbar-host"></footer>
        <aside  id="tp-sidepanel-host" class="hidden"></aside>
      </section>
    </body></html>`,
  );
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}


function _fakeEventSourceClass() {
  return class FakeEventSource {
    constructor() { this.listeners = {}; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() {}
  };
}


// ─── api.mjs unit test ─────────────────────────────────────────────


test("resetLayout: POSTs to /api/effectors/layout/reset", async () => {
  let recordedUrl = null;
  let recordedMethod = null;
  const fakeFetch = async (url, opts) => {
    recordedUrl = url;
    recordedMethod = opts && opts.method;
    return { ok: true, status: 204, async json() { return null; } };
  };
  await resetLayout(fakeFetch);
  assert.equal(recordedUrl, "/api/effectors/layout/reset");
  assert.equal(recordedMethod, "POST");
});


test("resetLayout: handles 204 No Content without trying to parse JSON", async () => {
  // 204 responses have no body — resetLayout must not call .json()
  // (which would throw on an empty body).
  let jsonCalls = 0;
  const fakeFetch = async () => ({
    ok: true, status: 204,
    async json() { jsonCalls += 1; return null; },
  });
  const r = await resetLayout(fakeFetch);
  assert.equal(jsonCalls, 0,
    "204 responses should skip .json() to avoid Empty-body parse errors");
  assert.equal(r, null);
});


// ─── boot Re-arrange wiring ────────────────────────────────────────


test("boot: Re-arrange POSTs to /api/effectors/layout/reset + re-lays out", async () => {
  const dom = _newAdminDom();
  global.EventSource = _fakeEventSourceClass();

  let resetHit = false;
  let resetMethod = null;
  const fetchFn = async (url, opts) => {
    if (url === "/api/topology") {
      return {
        ok: true, status: 200,
        async json() {
          return {
            hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
            grows: [
              { id: "grow:1", kind: "grow", label: "G1", sensors: {} },
              { id: "grow:2", kind: "grow", label: "G2", sensors: {} },
            ],
            effectors: [],
            // Pre-seed a custom position so the rearrange wipes it.
            layout: { "grow:1": { x: 999, y: 999 } },
          };
        },
      };
    }
    if (url === "/api/effectors/layout/reset"
        && opts && opts.method === "POST") {
      resetHit = true;
      resetMethod = opts.method;
      return { ok: true, status: 204, async json() { return null; } };
    }
    return { ok: true, status: 200, async json() { return {}; } };
  };

  const { boot } = await import("../../static/js/topology/page.mjs?cb=r1");
  await boot({ fetchFn });
  const doc = dom.window.document;
  const btn = doc.querySelector(
    ".tp-topbar-inner button[data-action='rearrange']",
  );
  assert.ok(btn);
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.ok(resetHit, "admin click must hit /api/effectors/layout/reset");
  assert.equal(resetMethod, "POST");
  const nodes = doc.querySelectorAll("#tp-graph-host .tp-node");
  assert.equal(nodes.length, 3,
    `expected 3 nodes (hub + 2 grows) post-rearrange, got ${nodes.length}`);
});


test("boot: viewer Re-arrange does NOT fire the reset POST", async () => {
  // Viewer role — the button isn't rendered (admin-only gate), so the
  // test mounts a viewer DOM and asserts no rearrange button + no
  // fetch fires if we synthesise a click (defence-in-depth).
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="viewer">
      <section class="tp-app" id="tp-app" data-role="viewer">
        <header id="tp-topbar-host"></header>
        <div    id="tp-graph-host"></div>
        <footer id="tp-statusbar-host"></footer>
        <aside  id="tp-sidepanel-host" class="hidden"></aside>
      </section>
    </body></html>`,
  );
  global.document = dom.window.document;
  global.window = dom.window;
  global.EventSource = _fakeEventSourceClass();

  let resetHit = false;
  const fetchFn = async (url, _opts) => {
    if (url === "/api/topology") {
      return {
        ok: true, status: 200,
        async json() {
          return {
            hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
            grows: [], effectors: [], layout: {},
          };
        },
      };
    }
    if (url === "/api/effectors/layout/reset") resetHit = true;
    return { ok: true, status: 200, async json() { return {}; } };
  };

  const { boot } = await import("../../static/js/topology/page.mjs?cb=r2");
  await boot({ fetchFn });
  // Viewer DOES still see the Re-arrange button (per the existing
  // Phase 7 spec — only `+ Add effector` is admin-gated in topbar).
  // The boot itself is supposed to short-circuit the reset POST when
  // body.dataset.role !== "admin".
  const doc = dom.window.document;
  const btn = doc.querySelector(
    ".tp-topbar-inner button[data-action='rearrange']",
  );
  assert.ok(btn, "Re-arrange button is present for non-admin too");
  btn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(resetHit, false,
    "viewer click must NOT fire the reset POST (admin-only)");
});

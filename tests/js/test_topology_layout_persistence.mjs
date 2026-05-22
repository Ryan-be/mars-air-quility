/**
 * Tests for the boot()-side debounced bulk-save (Phase 11 Task 11.1).
 *
 * The plan says drag-ends should be debounced 200ms and then sent as
 * a single `PATCH /api/effectors/layout` with the bundled positions.
 * Three rapid drag-ends inside the window should yield exactly ONE
 * fetch call.
 *
 * Re-arrange wiring tests live in `test_topology_layout_reset.mjs`
 * (Task 11.2) once the backend reset endpoint is in place.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { patchLayout } from "../../static/js/topology/api.mjs";


// ─── api.mjs unit tests ────────────────────────────────────────────


test("patchLayout: POSTs to /api/effectors/layout with the positions", async () => {
  let recordedUrl = null;
  let recordedOpts = null;
  const fakeFetch = async (url, opts) => {
    recordedUrl = url;
    recordedOpts = opts;
    return {
      ok: true, status: 200,
      async json() { return { saved: 2 }; },
    };
  };
  const positions = [
    { kind: "hub", id: "hub", x: 0, y: 0 },
    { kind: "effector", id: 1, x: 100, y: 200 },
  ];
  await patchLayout(positions, fakeFetch);
  assert.equal(recordedUrl, "/api/effectors/layout");
  assert.equal(recordedOpts.method, "PATCH");
  const body = JSON.parse(recordedOpts.body);
  assert.deepEqual(body.positions, positions);
});


// ─── boot debounced bulk-save tests ───────────────────────────────


function _newDom() {
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
    constructor() {
      this.listeners = {};
    }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    close() {}
  };
}


test("boot: three drag-ends inside 200ms fire ONE patchLayout fetch", async () => {
  const dom = _newDom();
  global.EventSource = _fakeEventSourceClass();

  // Track each fetch the boot makes after the initial topology fetch.
  const patchCalls = [];
  const fetchFn = async (url, opts) => {
    if (url === "/api/topology") {
      return {
        ok: true, status: 200,
        async json() {
          return {
            hub: { id: "hub", kind: "hub", label: "Hub", sensors: {} },
            grows: [],
            effectors: [{
              id: "effector:1", kind: "effector", parent: "hub",
              label: "Fan", effector_type: "fan",
              mode: "auto", current_state: "off", is_enabled: 1,
            }],
            layout: {},
          };
        },
      };
    }
    if (url === "/api/effectors/layout" && opts && opts.method === "PATCH") {
      patchCalls.push(JSON.parse(opts.body));
      return { ok: true, status: 200, async json() { return { saved: 1 }; } };
    }
    return { ok: true, status: 200, async json() { return {}; } };
  };

  // Patch setTimeout to capture scheduled flushes without waiting.
  const scheduled = [];
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = (fn, ms) => {
    scheduled.push({ fn, ms, cancelled: false });
    return scheduled.length;
  };
  globalThis.clearTimeout = (handle) => {
    const entry = scheduled[handle - 1];
    if (entry) entry.cancelled = true;
  };

  try {
    const { boot } = await import("../../static/js/topology/page.mjs?cb=l1");
    await boot({ fetchFn });

    const doc = dom.window.document;

    function _simDragEnd(dxStart, dyStart, dxEnd, dyEnd) {
      // Reuse the actual setupNodeDrag handler the boot wired in.
      // Re-query the node element between drags — the boot re-mounts
      // the graph on every onChange so the prior reference detaches.
      const nodeEl = doc.querySelector('[data-node-id="effector:1"]');
      assert.ok(nodeEl, "effector node element must be present");
      nodeEl.dispatchEvent(new dom.window.MouseEvent("mousedown", {
        bubbles: true, clientX: dxStart, clientY: dyStart, button: 0,
      }));
      dom.window.dispatchEvent(new dom.window.MouseEvent("mousemove", {
        bubbles: true, clientX: dxEnd, clientY: dyEnd,
      }));
      dom.window.dispatchEvent(new dom.window.MouseEvent("mouseup", {
        bubbles: true, clientX: dxEnd, clientY: dyEnd,
      }));
    }

    _simDragEnd(0, 0, 30, 30);
    _simDragEnd(0, 0, 40, 40);
    _simDragEnd(0, 0, 50, 50);

    // ZERO fetches fired so far — every drag-end scheduled the flush
    // but the timer hasn't fired.
    assert.equal(patchCalls.length, 0,
      "drag-ends should debounce; no fetch yet");

    // The plan calls for a 200ms debounce. Confirm at least one
    // scheduled callback uses 200ms, and that all but the most-recent
    // schedules were cancelled.
    const flushes = scheduled.filter((s) => s.ms === 200);
    assert.ok(flushes.length >= 1, "at least one 200ms flush scheduled");
    const liveFlushes = flushes.filter((s) => !s.cancelled);
    assert.equal(liveFlushes.length, 1,
      "expected exactly one live 200ms flush (others cancelled)");

    // Fire the live flush.
    liveFlushes[0].fn();
    for (let i = 0; i < 10; i++) await Promise.resolve();

    assert.equal(patchCalls.length, 1,
      `three rapid drag-ends → ONE fetch, got ${patchCalls.length}`);
    const body = patchCalls[0];
    assert.ok(Array.isArray(body.positions));
    const entry = body.positions.find((p) => String(p.id) === "1");
    assert.ok(entry, "the effector's drop should be in the payload");
    assert.equal(entry.kind, "effector");
    assert.equal(typeof entry.x, "number");
    assert.equal(typeof entry.y, "number");
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});

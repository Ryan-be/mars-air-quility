/**
 * Tests for the error-row component — single row in /grow/errors.
 *
 * Layout:
 *   [severity icon] [unit_label · kind · timestamp]
 *                   [message]
 *                   [Resolve] [Snooze 1h] [Snooze 24h]   <- admin only
 *
 * Admin gating: reads document.body.dataset.role at render time. Tests
 * flip body.dataset.role between "admin" and "viewer" to drive the
 * conditional UI surface area.
 *
 * After every successful PATCH the row dispatches an "error-updated"
 * custom event so the orchestrator can refetch + rebuild the list.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderErrorRow } from "../../static/js/grow/components/error-row.mjs";


function _newDom(role = "admin") {
  // Build a fresh JSDOM per test so body.dataset.role is isolated.
  const dom = new JSDOM(`<!doctype html><html><body data-role="${role}"></body></html>`);
  global.document = dom.window.document;
  global.window = dom.window;
  global.CustomEvent = dom.window.CustomEvent;
  return dom;
}


function _row(overrides = {}) {
  return {
    id: 42,
    unit_id: 1,
    unit_label: "Tomato 1",
    timestamp_utc: "2026-05-06T12:00:00",
    severity: "warning",
    kind: "sensor_degraded",
    message: "ambient_lux read failed",
    subject_sensor: "ambient_lux",
    details_json: null,
    resolved_at: null,
    snoozed_until: null,
    ...overrides,
  };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
function _flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }


// ─────────────────────────────────────────────────────────────────────
// 1. Renders severity icon
// ─────────────────────────────────────────────────────────────────────
test("error-row renders severity icon", () => {
  const dom = _newDom("admin");
  for (const sev of ["info", "warning", "critical"]) {
    const el = renderErrorRow(_row({ severity: sev }), {
      ownerDocument: dom.window.document,
    });
    const icon = el.querySelector("[data-testid='error-row-sev-icon']");
    assert.ok(icon, `icon present for ${sev}`);
    assert.equal(icon.dataset.severity, sev);
    // Class on the wrap surfaces the severity for CSS coloring
    assert.match(el.className, new RegExp(`sev-${sev}`));
  }
});


// ─────────────────────────────────────────────────────────────────────
// 2. Renders unit_label · kind · timestamp + message
// ─────────────────────────────────────────────────────────────────────
test("error-row renders unit_label, kind, timestamp, message", () => {
  const dom = _newDom("admin");
  const el = renderErrorRow(_row(), { ownerDocument: dom.window.document });
  const head = el.querySelector("[data-testid='error-row-head']");
  assert.ok(head);
  // Three pieces, separated by middot
  assert.match(head.textContent, /Tomato 1/);
  assert.match(head.textContent, /sensor_degraded/);
  assert.match(head.textContent, /2026-05-06T12:00:00/);
  assert.match(head.textContent, /·/);

  const msg = el.querySelector("[data-testid='error-row-msg']");
  assert.match(msg.textContent, /ambient_lux read failed/);
});


// ─────────────────────────────────────────────────────────────────────
// 3. Resolve button fires PATCH with {resolved_at:"now"}
// ─────────────────────────────────────────────────────────────────────
test("error-row resolve button PATCHes with {resolved_at:'now'}", async () => {
  const dom = _newDom("admin");
  let captured = null;
  const orig = _origFetch();
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderErrorRow(_row(), { ownerDocument: dom.window.document });
    el.querySelector("[data-testid='error-row-resolve']").click();
    await _flush();
    assert.ok(captured, "fetch was called");
    assert.equal(captured.opts.method, "PATCH");
    assert.equal(captured.url, "/api/grow/errors/42");
    const body = JSON.parse(captured.opts.body);
    assert.deepEqual(body, { resolved_at: "now" });
  } finally {
    _setMockFetch(orig);
  }
});


// ─────────────────────────────────────────────────────────────────────
// 4. Snooze 1h button fires PATCH with snoozed_until ~1h from now
// ─────────────────────────────────────────────────────────────────────
test("error-row snooze 1h button PATCHes with ~1h snoozed_until", async () => {
  const dom = _newDom("admin");
  let captured = null;
  const orig = _origFetch();
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const fixedNow = new Date("2026-05-06T12:00:00Z").getTime();
    const el = renderErrorRow(_row(), {
      ownerDocument: dom.window.document,
      now: () => fixedNow,
    });
    el.querySelector("[data-testid='error-row-snooze-1h']").click();
    await _flush();
    assert.ok(captured);
    const body = JSON.parse(captured.opts.body);
    const expected = new Date(fixedNow + 60 * 60 * 1000).toISOString();
    assert.equal(body.snoozed_until, expected);
  } finally {
    _setMockFetch(orig);
  }
});


// ─────────────────────────────────────────────────────────────────────
// 5. Snooze 24h button fires PATCH with snoozed_until ~24h from now
// ─────────────────────────────────────────────────────────────────────
test("error-row snooze 24h button PATCHes with ~24h snoozed_until", async () => {
  const dom = _newDom("admin");
  let captured = null;
  const orig = _origFetch();
  _setMockFetch(async (url, opts) => {
    captured = { url, opts };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const fixedNow = new Date("2026-05-06T12:00:00Z").getTime();
    const el = renderErrorRow(_row(), {
      ownerDocument: dom.window.document,
      now: () => fixedNow,
    });
    el.querySelector("[data-testid='error-row-snooze-24h']").click();
    await _flush();
    assert.ok(captured);
    const body = JSON.parse(captured.opts.body);
    const expected = new Date(fixedNow + 24 * 60 * 60 * 1000).toISOString();
    assert.equal(body.snoozed_until, expected);
  } finally {
    _setMockFetch(orig);
  }
});


// ─────────────────────────────────────────────────────────────────────
// 6. Hides admin actions when role=viewer
// ─────────────────────────────────────────────────────────────────────
test("error-row hides admin actions when role=viewer", () => {
  const dom = _newDom("viewer");
  const el = renderErrorRow(_row(), { ownerDocument: dom.window.document });
  // No Resolve / Snooze buttons
  assert.equal(el.querySelector("[data-testid='error-row-resolve']"), null);
  assert.equal(el.querySelector("[data-testid='error-row-snooze-1h']"), null);
  assert.equal(el.querySelector("[data-testid='error-row-snooze-24h']"), null);
  assert.equal(el.querySelector("[data-testid='error-row-actions']"), null);
});


test("error-row shows admin actions when role=admin", () => {
  const dom = _newDom("admin");
  const el = renderErrorRow(_row(), { ownerDocument: dom.window.document });
  assert.ok(el.querySelector("[data-testid='error-row-resolve']"));
  assert.ok(el.querySelector("[data-testid='error-row-snooze-1h']"));
  assert.ok(el.querySelector("[data-testid='error-row-snooze-24h']"));
});


// ─────────────────────────────────────────────────────────────────────
// 7. Emits "error-updated" custom event after successful PATCH
// ─────────────────────────────────────────────────────────────────────
test("error-row emits 'error-updated' after successful PATCH", async () => {
  const dom = _newDom("admin");
  const orig = _origFetch();
  _setMockFetch(async () => {
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const events = [];
    const el = renderErrorRow(_row(), { ownerDocument: dom.window.document });
    el.addEventListener("error-updated", (e) => events.push(e));
    // Mount the row so the event has a parent that could observe a bubble.
    dom.window.document.body.appendChild(el);
    el.querySelector("[data-testid='error-row-resolve']").click();
    await _flush();
    assert.equal(events.length, 1);
    assert.equal(events[0].detail.id, 42);
    assert.equal(events[0].detail.action, "resolve");
    assert.equal(events[0].bubbles, true);
  } finally {
    _setMockFetch(orig);
  }
});


// ─────────────────────────────────────────────────────────────────────
// 8. Snoozed rows render muted (extra coverage of snooze visual)
// ─────────────────────────────────────────────────────────────────────
test("error-row applies 'snoozed' class when snoozed_until > now", () => {
  const dom = _newDom("viewer");
  const fixedNow = new Date("2026-05-06T12:00:00Z").getTime();
  const future = new Date(fixedNow + 60 * 60 * 1000).toISOString();
  const el = renderErrorRow(_row({ snoozed_until: future }), {
    ownerDocument: dom.window.document,
    now: () => fixedNow,
  });
  assert.match(el.className, /snoozed/);
});


// ─── Snooze dropdown collapse (design-critique #20) ──────────────


test("admin actions: snooze options live inside a Snooze ▾ dropdown", () => {
  _newDom("admin");
  const el = renderErrorRow(
    _row({ id: 7 }),
    { ownerDocument: document },
  );
  // The dropdown summary is now the only visible "Snooze" trigger;
  // the inner buttons live inside the <details>.
  const summary = el.querySelector("[data-testid='error-row-snooze-summary']");
  assert.ok(summary, "Snooze ▾ summary rendered");
  assert.match(summary.textContent, /Snooze/);
  const menu = el.querySelector("[data-testid='error-row-snooze-menu']");
  assert.ok(menu, "snooze menu wraps the duration buttons");
  // Inner buttons are still present (testids unchanged) so existing
  // tests + automation keep working.
  assert.ok(el.querySelector("[data-testid='error-row-snooze-1h']"));
  assert.ok(el.querySelector("[data-testid='error-row-snooze-24h']"));
  // Inner button labels updated to clearer "1 hour" / "24 hours" copy
  // (the testid is the contract; the text is just chrome).
  const opt1h = el.querySelector("[data-testid='error-row-snooze-1h']");
  const opt24h = el.querySelector("[data-testid='error-row-snooze-24h']");
  assert.match(opt1h.textContent, /1 hour/);
  assert.match(opt24h.textContent, /24 hours/);
});

/**
 * Tests for the admin backup Status Panel component.
 *
 * The panel renders the per-pipeline state returned by
 * GET /api/admin/backup/status — colour-coded state chip + thread
 * liveness + pending counts + last attempt/success + collapsed error.
 *
 * It also exposes an `update(pipeline, snapshot)` method that the
 * page wires up to `backup_status_changed` SSE events, so live
 * pushes reflow the panel without a full re-render.
 *
 * The tests below are behavioural: we render to a detached node
 * with JSDOM and assert on textContent / querySelector results.
 * No browser. No network. No timers.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderStatusPanel }
  from "../../static/js/backup/components/status-panel.mjs";

const dom = new JSDOM();
global.document = dom.window.document;
global.window = dom.window;


/** Helper to build a /status response. Defaults to fully-disabled. */
function _status(overrides = {}) {
  const base = {
    enabled: false,
    paused: false,
    pipelines: {
      db: { enabled: false, thread_alive: false, snapshot: null },
      files: { enabled: false, thread_alive: false, snapshot: null },
    },
  };
  return { ...base, ...overrides };
}


/** Helper to build a snapshot dict (as published by _publish_status). */
function _snap(overrides = {}) {
  return {
    pipeline: "db",
    state: "idle",
    backoff_delay_s: 1.0,
    last_attempt_at: "2026-05-18T12:00:00",
    last_success_at: "2026-05-18T12:00:00",
    last_error: null,
    pending_rows: 0,
    pending_blobs: 0,
    pending_delete_scope: 0,
    ...overrides,
  };
}


test("status panel: disabled top-level shows empty-state copy", () => {
  const panel = renderStatusPanel({
    status: _status({ enabled: false }),
    ownerDocument: document,
  });
  assert.match(panel.textContent, /backup is disabled/i,
    "should display an explicit 'Backup is disabled' message when enabled=false");
});


test("status panel: enabled top-level renders per-pipeline cards", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap() },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  assert.ok(panel.querySelector("[data-pipeline='db']"),
    "db pipeline panel should be present");
  assert.ok(panel.querySelector("[data-pipeline='files']"),
    "files pipeline panel should be present");
});


test("status panel: idle state chip is green", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({ state: "idle" }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const chip = panel.querySelector("[data-pipeline='db'] .bk-state-chip");
  assert.ok(chip);
  assert.match(chip.className, /bk-state-idle/);
  assert.match(chip.textContent, /idle/i);
});


test("status panel: draining state chip is yellow", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({ state: "draining" }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const chip = panel.querySelector("[data-pipeline='db'] .bk-state-chip");
  assert.match(chip.className, /bk-state-draining/);
});


test("status panel: backoff state chip is red and shows error text", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({
          state: "backoff",
          last_error: "psycopg2.OperationalError: connection refused",
          backoff_delay_s: 32.0,
        }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const dbCard = panel.querySelector("[data-pipeline='db']");
  const chip = dbCard.querySelector(".bk-state-chip");
  assert.match(chip.className, /bk-state-backoff/);
  // Error is rendered (collapsed by default but in DOM)
  const err = dbCard.querySelector(".bk-error");
  assert.ok(err, "error element should exist when last_error is set");
  assert.match(err.textContent, /psycopg2\.OperationalError/);
});


test("status panel: paused state chip is blue", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      paused: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({ state: "paused" }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const chip = panel.querySelector("[data-pipeline='db'] .bk-state-chip");
  assert.match(chip.className, /bk-state-paused/);
});


test("status panel: disabled pipeline shows grey chip + clear empty state", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: false, thread_alive: false, snapshot: null },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const dbCard = panel.querySelector("[data-pipeline='db']");
  const chip = dbCard.querySelector(".bk-state-chip");
  assert.match(chip.className, /bk-state-disabled/);
  // Pipeline that's off should have explicit empty-state text, not a
  // partial blank panel.
  assert.match(dbCard.textContent, /pipeline disabled/i);
});


test("status panel: enabled but no snapshot shows 'starting…'", () => {
  // Worker hasn't run yet — thread_alive may be false even though
  // enabled=true. Operators need to know it's spinning up.
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: false, snapshot: null },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const dbCard = panel.querySelector("[data-pipeline='db']");
  assert.match(dbCard.textContent.toLowerCase(), /starting|waiting/);
});


test("status panel: shows pending counts (rows + blobs + delete_scope)", () => {
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({
          pending_rows: 12,
          pending_blobs: 3,
          pending_delete_scope: 4,
        }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const dbCard = panel.querySelector("[data-pipeline='db']");
  assert.match(dbCard.textContent, /12/);
  assert.match(dbCard.textContent, /3/);
  assert.match(dbCard.textContent, /4/);
});


test("status panel: thread_alive shows up as an indicator", () => {
  // The thread_alive truth value matters because workers crash silently
  // sometimes (e.g. on schema-migration startup races). Operators need
  // to see it.
  const dead = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: false, snapshot: _snap() },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const alive = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap() },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  const deadCard = dead.querySelector("[data-pipeline='db']");
  const aliveCard = alive.querySelector("[data-pipeline='db']");
  // Just assert different markup — the renderer is free to use icon /
  // text. The behavioural rule is "operators can distinguish".
  assert.notEqual(deadCard.innerHTML, aliveCard.innerHTML,
    "thread_alive truth must produce visibly-different markup");
});


test("status panel: update() refreshes a single pipeline without rebuild", () => {
  // The orchestrator listens for SSE backup_status_changed events and
  // calls panel.update(pipeline, snapshot). The other pipeline's DOM
  // node must remain intact (we use this to assert no double-render).
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({ state: "idle" }) },
        files: { enabled: true, thread_alive: true, snapshot: _snap({
          pipeline: "files",
          state: "idle",
        }) },
      },
    }),
    ownerDocument: document,
  });
  const filesCardBefore = panel.querySelector("[data-pipeline='files']");
  // Tag a unique attribute so we can prove the same node survives.
  filesCardBefore.dataset.tag = "preserve-me";

  panel.update("db", _snap({ state: "draining", pending_rows: 5 }));

  const filesCardAfter = panel.querySelector("[data-pipeline='files']");
  assert.equal(filesCardAfter.dataset.tag, "preserve-me",
    "files card must NOT be re-rendered when only db updates");

  const dbCard = panel.querySelector("[data-pipeline='db']");
  assert.match(
    dbCard.querySelector(".bk-state-chip").className,
    /bk-state-draining/,
  );
  assert.match(dbCard.textContent, /5/);
});


test("status panel: update() ignores unknown pipeline gracefully", () => {
  // Defensive: phase 9 may add new pipelines; an older browser opens
  // the page and gets a snapshot for a pipeline its panel doesn't know
  // about. Must not throw.
  const panel = renderStatusPanel({
    status: _status({ enabled: true }),
    ownerDocument: document,
  });
  // Should not throw
  panel.update("future-pipeline", _snap({ pipeline: "future-pipeline" }));
});


test("status panel: never displays cleartext password fragments", () => {
  // The status snapshot SHOULD NOT contain a password field, but as a
  // belt-and-braces check, ensure the renderer doesn't expose anything
  // that smells like a password if one slips through.
  const panel = renderStatusPanel({
    status: _status({
      enabled: true,
      pipelines: {
        db: { enabled: true, thread_alive: true, snapshot: _snap({
          // Hypothetically — should still be invisible in the panel.
          password: "supersecret",
          secret_key: "AKIA-secret",
        }) },
        files: { enabled: false, thread_alive: false, snapshot: null },
      },
    }),
    ownerDocument: document,
  });
  assert.doesNotMatch(panel.textContent, /supersecret/);
  assert.doesNotMatch(panel.textContent, /AKIA-secret/);
});

/**
 * Tests for the buffer-inspector card (Diagnostics tab section that
 * surfaces WHAT is queued in the firmware-side buffers, not just the
 * count).
 *
 * Focus areas:
 *   1. Empty / null state — renderer handles brand-new units that
 *      haven't received a piggyback summary yet.
 *   2. Populated state — size + bytes + age + per-kind breakdown
 *      render correctly.
 *   3. Photo buffer (no `kinds`) renders without the breakdown UL.
 *   4. _formatBytes helper boundary cases (B / KB / MB).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderBufferInspector, _formatBytes, _formatTs,
} from "../../static/js/grow/components/buffer-inspector.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _data(overrides = {}) {
  return {
    buffer_summary: null,
    photo_buffer_summary: null,
    ...overrides,
  };
}


// --------------------------------------------------------------------
// _formatBytes — pure helper, easier to pin in isolation than via the
// full card render. Three branches: bytes / KB / MB.
// --------------------------------------------------------------------


test("_formatBytes: under 1024 renders as bytes", () => {
  assert.equal(_formatBytes(0), "0 B");
  assert.equal(_formatBytes(500), "500 B");
  assert.equal(_formatBytes(1023), "1023 B");
});


test("_formatBytes: 1024 to 1MB renders as KB with one decimal", () => {
  assert.equal(_formatBytes(1024), "1.0 KB");
  assert.equal(_formatBytes(78423), "76.6 KB");
});


test("_formatBytes: above 1MB renders as MB", () => {
  assert.equal(_formatBytes(1024 * 1024), "1.0 MB");
  assert.equal(_formatBytes(4_800_000), "4.6 MB");
});


test("_formatBytes: null / invalid render as em-dash", () => {
  assert.equal(_formatBytes(null), "—");
  assert.equal(_formatBytes(undefined), "—");
  assert.equal(_formatBytes(-5), "—");
});


// --------------------------------------------------------------------
// Card rendering — empty/null state
// --------------------------------------------------------------------


test("buffer inspector: null buffer_summary renders 'no summary yet'", () => {
  const el = renderBufferInspector(_data(), { ownerDocument: document });
  assert.equal(el.dataset.testid, "diag-buffer-inspector");
  const text = el.querySelector("[data-testid='diag-buffer-text-empty']");
  assert.ok(text, "text-buffer empty state present");
  assert.match(text.textContent, /no summary yet/i);
  // No stats / window / kinds for the empty card
  assert.equal(el.querySelector("[data-testid='diag-buffer-text-stats']"), null);
});


test("buffer inspector: size=0 buffer renders 'empty' (distinct from 'no summary yet')", () => {
  /* When the firmware HAS sent a piggyback but the buffer is genuinely
   * empty, the operator should see "empty" — not "no summary yet"
   * which means "we don't even have data to show". The two states
   * are visually similar but mean different things. */
  const summary = {
    size: 0, total_bytes: 0,
    oldest_ts: null, newest_ts: null,
    kinds: {},
  };
  const el = renderBufferInspector(
    _data({ buffer_summary: summary }),
    { ownerDocument: document },
  );
  const empty = el.querySelector("[data-testid='diag-buffer-text-empty']");
  assert.ok(empty);
  assert.match(empty.textContent, /^empty$/i);
});


// --------------------------------------------------------------------
// Card rendering — populated state
// --------------------------------------------------------------------


test("buffer summary renders size and bytes", () => {
  const summary = {
    size: 247,
    total_bytes: 78423,
    oldest_ts: "2026-05-07T03:42:00",
    newest_ts: "2026-05-07T04:17:30",
    kinds: { telemetry: 240, event: 6, capabilities: 1 },
  };
  const el = renderBufferInspector(
    _data({ buffer_summary: summary }),
    { ownerDocument: document },
  );
  const stats = el.querySelector("[data-testid='diag-buffer-text-stats']");
  assert.ok(stats);
  // 78423 bytes → "76.6 KB"
  assert.match(stats.textContent, /247 items/);
  assert.match(stats.textContent, /76\.6 KB/);
});


test("buffer summary renders kinds breakdown for text buffer", () => {
  const summary = {
    size: 247, total_bytes: 78423,
    oldest_ts: "2026-05-07T03:42:00",
    newest_ts: "2026-05-07T04:17:30",
    kinds: { telemetry: 240, event: 6, capabilities: 1 },
  };
  const el = renderBufferInspector(
    _data({ buffer_summary: summary }),
    { ownerDocument: document },
  );
  const kinds = el.querySelector("[data-testid='diag-buffer-text-kinds']");
  assert.ok(kinds, "kinds breakdown present for text buffer");
  const items = [...kinds.querySelectorAll("li")].map((li) => li.textContent);
  assert.equal(items.length, 3);
  assert.ok(items.some((t) => t.includes("telemetry") && t.includes("240")));
  assert.ok(items.some((t) => t.includes("event") && t.includes("6")));
  assert.ok(items.some((t) => t.includes("capabilities") && t.includes("1")));
});


test("photo buffer summary omits kinds section", () => {
  /* Photos are all the same kind — there's no msg_type-equivalent for
   * the photo buffer, so the renderer must NOT draw a breakdown UL.
   * The branch is on `summary.kinds` presence, not the label string,
   * so future buffer types don't need to teach this file about
   * themselves. */
  const photoSummary = {
    size: 12,
    total_bytes: 4_800_000,
    oldest_ts: "2026-05-07T03:00:00Z",
    newest_ts: "2026-05-07T05:30:00Z",
    // no `kinds` field
  };
  const el = renderBufferInspector(
    _data({ photo_buffer_summary: photoSummary }),
    { ownerDocument: document },
  );
  const photoStats =
    el.querySelector("[data-testid='diag-buffer-photos-stats']");
  assert.ok(photoStats);
  assert.match(photoStats.textContent, /12 items/);
  assert.match(photoStats.textContent, /4\.6 MB/);
  // No kinds list for the photo card
  assert.equal(
    el.querySelector("[data-testid='diag-buffer-photos-kinds']"),
    null,
  );
});


test("buffer summary renders window line with both timestamps", () => {
  /* The "oldest / newest" window pins the time-range the buffer covers;
   * gives the operator a sense of whether the queued data is still
   * relevant or stale enough to clear. */
  const summary = {
    size: 5, total_bytes: 100,
    oldest_ts: "2026-05-07T03:42:00",
    newest_ts: "2026-05-07T04:17:30",
    kinds: { telemetry: 5 },
  };
  const el = renderBufferInspector(
    _data({ buffer_summary: summary }),
    { ownerDocument: document },
  );
  const window = el.querySelector("[data-testid='diag-buffer-text-window']");
  assert.ok(window);
  assert.match(window.textContent, /oldest/);
  assert.match(window.textContent, /newest/);
});


test("buffer inspector: both summaries present render side-by-side", () => {
  /* The most common populated state: text + photo buffers both have
   * data. The card body is a CSS grid; both summaries appear with
   * their own testids so the orchestrator doesn't have to know which
   * sub-card is where. */
  const el = renderBufferInspector(
    _data({
      buffer_summary: {
        size: 10, total_bytes: 1000,
        oldest_ts: "2026-05-07T03:00:00",
        newest_ts: "2026-05-07T03:30:00",
        kinds: { telemetry: 10 },
      },
      photo_buffer_summary: {
        size: 3, total_bytes: 300_000,
        oldest_ts: "2026-05-07T03:15:00Z",
        newest_ts: "2026-05-07T03:30:00Z",
      },
    }),
    { ownerDocument: document },
  );
  assert.ok(el.querySelector("[data-testid='diag-buffer-text']"),
    "text-buffer summary present");
  assert.ok(el.querySelector("[data-testid='diag-buffer-photos']"),
    "photo-buffer summary present");
});


test("_formatTs: null returns em-dash", () => {
  assert.equal(_formatTs(null), "—");
  assert.equal(_formatTs(undefined), "—");
});


test("_formatTs: invalid timestamp falls back to raw string", () => {
  /* Defensive: better to show the firmware's bad string than to crash
   * the render when something upstream sends junk. */
  assert.equal(_formatTs("garbage-not-a-date"), "garbage-not-a-date");
});

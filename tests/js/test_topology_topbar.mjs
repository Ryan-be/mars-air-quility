/**
 * Tests for the topology telemetry topbar component (Phase 7 Task 7.1).
 *
 * The topbar is the chrome row stamped into `#tp-topbar-host` at boot.
 * It carries three regions:
 *
 *   1. Left: a `tp-brand` cell — "MLSS · NODE MAP".
 *   2. Middle: 5 telemetry cells (Hub Status / Grows /
 *      Effectors / Active / Auto vs Forced) rendered as
 *      `<div class="tp-stat"><span class="tp-stat-label">…</span>
 *      <span class="tp-stat-value">…</span></div>`.
 *   3. Right: 2-3 action buttons — Re-arrange + Recenter, plus a
 *      `+ Add effector` button when the caller is admin.
 *
 * Click handlers come from the boot orchestrator
 * (`page.mjs::boot()`) via the `onRearrange`, `onRecenter`,
 * `onAddEffector` callbacks. The component itself is a pure DOM
 * renderer — no SSE wiring, no setInterval, no module-level state.
 *
 * Mission Time tries to slot a `<rux-clock>` web component into the
 * Mission Time cell first; if that constructor isn't on the page (e.g.
 * AstroUXDS hasn't loaded in a JSDOM test), the cell falls back to a
 * static `T+00:00:00` string that the caller can update via
 * setInterval. The test for that fallback drives the mission-time
 * cell shape regardless of whether rux-clock is registered.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderTopbar } from
  "../../static/js/topology/components/topbar.mjs";


function _newDom() {
  return new JSDOM(`<!doctype html><html><body></body></html>`);
}


function _stats(over = {}) {
  return {
    total: 7,
    active: 1,
    grows: 2,
    effectors: 4,
    auto: 3,
    forced: 1,
    ...over,
  };
}


test("renderTopbar: returns a header element with the tp-topbar-inner class", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  assert.equal(el.tagName, "HEADER");
  assert.ok(el.classList.contains("tp-topbar-inner"),
    "topbar root carries the tp-topbar-inner class");
});


test("renderTopbar: brand cell reads 'MLSS · NODE MAP'", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  const brand = el.querySelector(".tp-brand");
  assert.ok(brand, "brand cell present");
  // Allow rendering variations (e.g. middle-dot vs bullet) but the
  // canonical visible text is "MLSS · NODE MAP".
  assert.match(brand.textContent, /MLSS/);
  assert.match(brand.textContent, /NODE MAP/);
});


test("renderTopbar: renders five tp-stat cells with both label + value spans", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  const cells = el.querySelectorAll(".tp-stat");
  // Mission Time was removed per operator review (decorative noise).
  // The five remaining cells: Hub Status / Grows / Effectors / Active /
  // Auto vs Forced.
  assert.equal(cells.length, 5,
    `expected 5 telemetry cells, got ${cells.length}`);
  for (const cell of cells) {
    assert.ok(cell.querySelector(".tp-stat-label"),
      "each cell carries a tp-stat-label span");
    assert.ok(cell.querySelector(".tp-stat-value"),
      "each cell carries a tp-stat-value span");
  }
});


test("renderTopbar: telemetry cells carry the 5 canonical labels", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  const labels = Array.from(
    el.querySelectorAll(".tp-stat .tp-stat-label"),
  ).map((l) => l.textContent.trim().toLowerCase());
  // The cells appear in the order Hub Status / Grows / Effectors /
  // Active / Auto vs Forced. (Mission Time removed.)
  assert.match(labels[0], /hub/);
  assert.match(labels[1], /grow/);
  assert.match(labels[2], /effector/);
  assert.match(labels[3], /active/);
  assert.match(labels[4], /auto/);
});


test("renderTopbar: stat values are taken from the stats prop", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats({
      grows: 5,
      effectors: 9,
      active: 3,
      auto: 6,
      forced: 3,
    }),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  const values = Array.from(
    el.querySelectorAll(".tp-stat .tp-stat-value"),
  ).map((v) => v.textContent.trim());
  // Hub status is non-numeric and lives at index 0. The remaining four
  // cells (Grows / Effectors / Active / Auto vs Forced) surface the
  // stats prop verbatim. The "Auto vs Forced" cell renders both
  // numbers; the assertion is "contains 6 AND 3".
  assert.equal(values[1], "5",     `Grows cell, got ${values[1]}`);
  assert.equal(values[2], "9",     `Effectors cell, got ${values[2]}`);
  assert.equal(values[3], "3",     `Active cell, got ${values[3]}`);
  assert.match(values[4], /6/,     "Auto vs Forced cell shows auto count");
  assert.match(values[4], /3/,     "Auto vs Forced cell shows forced count");
});


test("renderTopbar: Re-arrange + Recenter buttons are always present", () => {
  const dom = _newDom();
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  const rearrange = el.querySelector("button[data-action='rearrange']");
  const recenter = el.querySelector("button[data-action='recenter']");
  assert.ok(rearrange, "rearrange button present");
  assert.ok(recenter, "recenter button present");
  // Visible label sanity-check — both spec-cited buttons render with
  // their AstroUXDS-style ⟲ / ⌖ glyphs.
  assert.match(rearrange.textContent, /Re-arrange/i);
  assert.match(recenter.textContent, /Recenter/i);
});


test("renderTopbar: + Add effector button visible only when isAdmin=true", () => {
  const dom = _newDom();
  const adminEl = renderTopbar({
    stats: _stats(),
    isAdmin: true,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  assert.ok(adminEl.querySelector("button[data-action='add-effector']"),
    "admin sees the + Add effector button");

  const viewerEl = renderTopbar({
    stats: _stats(),
    isAdmin: false,
    onRearrange: () => {},
    onRecenter: () => {},
    onAddEffector: () => {},
    doc: dom.window.document,
  });
  assert.equal(
    viewerEl.querySelector("button[data-action='add-effector']"),
    null,
    "non-admin viewers don't see the + Add effector button",
  );
});


test("renderTopbar: button clicks fire the supplied callbacks", () => {
  const dom = _newDom();
  let rearranged = 0;
  let recentred = 0;
  let added = 0;
  const el = renderTopbar({
    stats: _stats(),
    isAdmin: true,
    onRearrange: () => { rearranged += 1; },
    onRecenter: () => { recentred += 1; },
    onAddEffector: () => { added += 1; },
    doc: dom.window.document,
  });
  el.querySelector("button[data-action='rearrange']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("button[data-action='recenter']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  el.querySelector("button[data-action='add-effector']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(rearranged, 1, "rearrange fired once");
  assert.equal(recentred, 1, "recenter fired once");
  assert.equal(added, 1, "add-effector fired once");
});


// (Mission Time cell removed per operator feedback — no longer rendered
// in the topbar, so the data-role="mission-time" target assertion is
// no longer applicable.)

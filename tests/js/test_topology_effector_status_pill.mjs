/**
 * Tests for the topology effector status pill (Phase 6 Task 6.2).
 *
 * Distinct from `static/js/grow/components/status-pill.mjs` (grow-unit
 * online/stale/offline pill) — that lives in the grow/ namespace and
 * has different semantics. This one renders the on/off/auto/fault
 * indicator on the effector card.
 *
 * File name `effector-status-pill.mjs` to avoid name collision.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderEffectorStatusPill } from "../../static/js/topology/components/effector-status-pill.mjs";


function _newDom() {
  return new JSDOM("<!doctype html><html><body></body></html>");
}


test("status pill: on/solid renders with tp-pill-on class and 'ON' text", () => {
  const dom = _newDom();
  const pill = renderEffectorStatusPill({
    state: "on", label: "ON", solid: true,
    ownerDocument: dom.window.document,
  });
  assert.equal(pill.tagName.toLowerCase(), "span");
  assert.ok(pill.classList.contains("tp-pill"));
  assert.ok(pill.classList.contains("tp-pill-on"));
  assert.match(pill.textContent, /ON/);
});


test("status pill: off variant gets tp-pill-off class", () => {
  const dom = _newDom();
  const pill = renderEffectorStatusPill({
    state: "off", label: "OFF",
    ownerDocument: dom.window.document,
  });
  assert.ok(pill.classList.contains("tp-pill-off"));
  assert.match(pill.textContent, /OFF/);
});


test("status pill: solid flag adds tp-pill-solid class", () => {
  const dom = _newDom();
  const pill = renderEffectorStatusPill({
    state: "on", label: "ON", solid: true,
    ownerDocument: dom.window.document,
  });
  assert.ok(pill.classList.contains("tp-pill-solid"));
});


test("status pill: defaults label to the state value when label omitted", () => {
  const dom = _newDom();
  const pill = renderEffectorStatusPill({
    state: "fault",
    ownerDocument: dom.window.document,
  });
  // Falls back to the state name uppercased.
  assert.match(pill.textContent, /fault/i);
});

/**
 * Tests for the effector card (Phase 6 Tasks 6.5 + 6.6).
 *
 * The effector card is the most-interactive of the three node-card
 * variants: it owns the AUTO / ON / OFF segmented control that talks
 * to POST /api/effectors/<id>/state. Tests cover:
 *
 *   * Structure — div.tp-card.tp-card-effector with header, status
 *     pill row, mode-bar, and an admin cog (admin-only).
 *   * AUTO/ON/OFF — clicking each button fires `onMode(id, mode)`.
 *   * Drag-vs-click — the button's mousedown + click both call
 *     `stopPropagation` so the wrapping node-drag handler doesn't
 *     fire when the operator changes mode.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderEffectorCard } from "../../static/js/topology/components/effector-card.mjs";


function _newDom() {
  return new JSDOM("<!doctype html><html><body></body></html>");
}


const sampleEffector = {
  id: "effector:7",
  kind: "effector",
  parent: "hub",
  label: "Room fan",
  effector_type: "fan",
  mode: "auto",
  current_state: "off",
  is_enabled: 1,
};


test("effector card: returns div.tp-card.tp-card-effector", () => {
  const dom = _newDom();
  const card = renderEffectorCard(sampleEffector, dom.window.document, {});
  assert.ok(card.classList.contains("tp-card"));
  assert.ok(card.classList.contains("tp-card-effector"));
});


test("effector card: header shows label + effector_type sub-label", () => {
  const dom = _newDom();
  const card = renderEffectorCard(sampleEffector, dom.window.document, {});
  assert.match(card.textContent, /Room fan/);
  // effector_type is shown as the model/sub-label.
  assert.match(card.textContent, /fan/i);
});


test("effector card: includes a status pill row", () => {
  const dom = _newDom();
  const card = renderEffectorCard(sampleEffector, dom.window.document, {});
  // The status pill comes from effector-status-pill.mjs and gets a
  // .tp-pill class.
  assert.ok(card.querySelector(".tp-pill"),
    "status pill should be present");
});


test("effector card: AUTO/ON/OFF segmented bar exposes data-mode buttons", () => {
  const dom = _newDom();
  const card = renderEffectorCard(sampleEffector, dom.window.document, {});
  const auto = card.querySelector('[data-mode="auto"]');
  const on   = card.querySelector('[data-mode="on"]');
  const off  = card.querySelector('[data-mode="off"]');
  assert.ok(auto, "auto button present");
  assert.ok(on,   "on button present");
  assert.ok(off,  "off button present");
});


test("effector card: current mode button has aria-pressed=true and .active class", () => {
  const dom = _newDom();
  const card = renderEffectorCard(sampleEffector, dom.window.document, {});
  // sampleEffector.mode === "auto"
  const auto = card.querySelector('[data-mode="auto"]');
  assert.equal(auto.getAttribute("aria-pressed"), "true");
  assert.ok(auto.classList.contains("active"));
  // The other buttons should NOT be marked active.
  const on = card.querySelector('[data-mode="on"]');
  assert.notEqual(on.getAttribute("aria-pressed"), "true");
});


test("effector card: admin cog hidden by default, shown when isAdmin", () => {
  const dom = _newDom();
  const viewer = renderEffectorCard(sampleEffector, dom.window.document, {});
  assert.equal(
    viewer.querySelector('[data-action="open-config"]'),
    null,
    "viewer should see no admin cog",
  );
  const admin = renderEffectorCard(
    sampleEffector, dom.window.document, { isAdmin: true },
  );
  assert.ok(
    admin.querySelector('[data-action="open-config"]'),
    "admin should see the cog button",
  );
});


// ─── Task 6.6 — wired AUTO/ON/OFF + propagation guards ──────────────────


test("effector card: clicking ON fires onMode(id, 'on')", () => {
  const dom = _newDom();
  const calls = [];
  const card = renderEffectorCard(
    sampleEffector, dom.window.document,
    { onMode: (id, mode) => calls.push([id, mode]) },
  );
  const onBtn = card.querySelector('[data-mode="on"]');
  onBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(calls, [["effector:7", "on"]]);
});


test("effector card: clicking OFF fires onMode(id, 'off')", () => {
  const dom = _newDom();
  const calls = [];
  const card = renderEffectorCard(
    sampleEffector, dom.window.document,
    { onMode: (id, mode) => calls.push([id, mode]) },
  );
  card.querySelector('[data-mode="off"]')
    .dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(calls, [["effector:7", "off"]]);
});


test("effector card: button click + mousedown stop propagation (drag-safe)", () => {
  const dom = _newDom();
  const doc = dom.window.document;
  const wrap = doc.createElement("div");
  // Wrapping div mimics the .tp-node that would normally have a
  // mousedown drag handler attached.
  let wrapperFired = 0;
  wrap.addEventListener("mousedown", () => { wrapperFired += 1; });
  wrap.addEventListener("click", () => { wrapperFired += 1; });
  const card = renderEffectorCard(
    sampleEffector, doc, { onMode: () => {} },
  );
  wrap.appendChild(card);
  doc.body.appendChild(wrap);
  const btn = card.querySelector('[data-mode="on"]');
  btn.dispatchEvent(new dom.window.MouseEvent("mousedown", { bubbles: true }));
  btn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  assert.equal(wrapperFired, 0,
    "wrapper drag handler should NOT fire when the button is clicked");
});

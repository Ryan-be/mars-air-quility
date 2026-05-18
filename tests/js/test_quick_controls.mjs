import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderQuickControls, computeWaterLockedUntil,
} from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("computeWaterLockedUntil: returns Date when within soak window", () => {
  const lastPulse = new Date("2026-05-03T11:42:00Z");
  const soak = 30; // minutes
  const now = new Date("2026-05-03T12:00:00Z");
  const locked = computeWaterLockedUntil(lastPulse, soak, now);
  assert.ok(locked > now);
});


test("computeWaterLockedUntil: returns null when soak elapsed", () => {
  const lastPulse = new Date("2026-05-03T11:00:00Z");
  const soak = 30;
  const now = new Date("2026-05-03T12:00:00Z");
  assert.equal(computeWaterLockedUntil(lastPulse, soak, now), null);
});


test("computeWaterLockedUntil: returns null when never pulsed", () => {
  assert.equal(computeWaterLockedUntil(null, 30, new Date()), null);
});


test("renderQuickControls: identify always enabled", () => {
  const el = renderQuickControls({ id: 1 }, document);
  const btn = el.querySelector("[data-action='identify']");
  assert.equal(btn.disabled, false);
});


test("renderQuickControls: water-now disabled when locked", () => {
  const futureUnlock = new Date(Date.now() + 60 * 60 * 1000);
  const el = renderQuickControls({ id: 1, _waterLockedUntil: futureUnlock }, document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, true);
  assert.match(btn.textContent, /🔒|locked/i);
});


test("renderQuickControls: water-now enabled when not locked", () => {
  const el = renderQuickControls({ id: 1, _waterLockedUntil: null }, document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, false);
});


// ---------------------------------------------------------------------------
// Phase 2 — capability `health` field drives sense-only-mode UI degradation.
// First-deployment scenario: camera + soil moisture sensor wired but pump
// + grow light not yet powered. Buttons must visibly grey out without an
// explicit "disabled mode" toggle, so the user knows clicking water_now
// won't actually pulse the pump.
// ---------------------------------------------------------------------------


function unitWithCap(channel, health) {
  return {
    id: 1,
    capabilities: [
      { channel, hardware: "x", is_required: false, unit_label: "x", health },
    ],
  };
}


test("renderQuickControls: connected pump → normal styling on water-now", () => {
  const el = renderQuickControls(unitWithCap("pump", "connected"), document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, false);
  assert.doesNotMatch(btn.className, /greyed|unresponsive|no-hardware/);
});


test("renderQuickControls: untested pump → greyed but clickable", () => {
  const el = renderQuickControls(unitWithCap("pump", "untested"), document);
  const btn = el.querySelector("[data-action='water-now']");
  // Clickable: lets the user kick off the first test pulse once the PSU
  // is wired up.
  assert.equal(btn.disabled, false);
  assert.match(btn.className, /greyed/);
  assert.match(btn.title || "", /test|psu/i);
});


test("renderQuickControls: unresponsive pump → greyed AND disabled", () => {
  const el = renderQuickControls(unitWithCap("pump", "unresponsive"), document);
  const btn = el.querySelector("[data-action='water-now']");
  assert.equal(btn.disabled, true);
  assert.match(btn.className, /greyed/);
  assert.match(btn.className, /unresponsive/);
  assert.match(btn.title || "", /power|cabling|reach/i);
});


test("renderQuickControls: no_hardware light → light-toggle greyed AND disabled", () => {
  const el = renderQuickControls(unitWithCap("light", "no_hardware"), document);
  const btn = el.querySelector("[data-action='light-toggle']");
  assert.equal(btn.disabled, true);
  assert.match(btn.className, /greyed/);
  assert.match(btn.className, /no-hardware/);
  assert.match(btn.title || "", /not detected|hardware/i);
});

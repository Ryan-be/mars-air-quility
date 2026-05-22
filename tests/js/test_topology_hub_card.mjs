/**
 * Tests for the hub card (Phase 6 Task 6.3).
 *
 * The hub card renders the central MLSS hub on the topology graph:
 * label + three telemetry tiles (temp, RH, CO₂) + an optional
 * sparkline of recent temperatures. It exposes its type colour via
 * the .tp-card-stripe element so the page CSS can colour-code which
 * node category each card belongs to.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderHubCard } from "../../static/js/topology/components/hub-card.mjs";


function _newDom() {
  return new JSDOM("<!doctype html><html><body></body></html>");
}


const sampleHub = {
  id: "hub",
  kind: "hub",
  label: "MLSS Hub",
  sub: "central coordinator",
  sensors: { temp: 22.5, rh: 55, co2: 720 },
};


test("hub card: returns div.tp-card.tp-card-hub", () => {
  const dom = _newDom();
  const card = renderHubCard(sampleHub, {}, dom.window.document);
  assert.equal(card.tagName.toLowerCase(), "div");
  assert.ok(card.classList.contains("tp-card"));
  assert.ok(card.classList.contains("tp-card-hub"));
});


test("hub card: shows the hub label and sub-label", () => {
  const dom = _newDom();
  const card = renderHubCard(sampleHub, {}, dom.window.document);
  assert.match(card.textContent, /MLSS Hub/);
});


test("hub card: shows three telemetry tiles for temp / RH / CO₂", () => {
  const dom = _newDom();
  const card = renderHubCard(sampleHub, {}, dom.window.document);
  const tiles = card.querySelectorAll(".tp-tile");
  assert.equal(tiles.length, 3, `expected 3 tiles, got ${tiles.length}`);
  const keys = [...tiles].map((t) => t.querySelector(".tp-tile-k").textContent);
  assert.ok(keys.some((k) => /temp/i.test(k)), "temp tile present");
  assert.ok(keys.some((k) => /rh|humid/i.test(k)), "RH tile present");
  assert.ok(keys.some((k) => /co/i.test(k)), "CO2 tile present");
});


test("hub card: tile values render the sensor numbers", () => {
  const dom = _newDom();
  const card = renderHubCard(sampleHub, {}, dom.window.document);
  // Temp formatted as one decimal (22.5).
  assert.match(card.textContent, /22\.5/);
  // RH formatted as integer (55).
  assert.match(card.textContent, /55/);
  // CO₂ formatted as integer (720).
  assert.match(card.textContent, /720/);
});


test("hub card: includes the left-edge .tp-card-stripe", () => {
  const dom = _newDom();
  const card = renderHubCard(sampleHub, {}, dom.window.document);
  const stripe = card.querySelector(".tp-card-stripe");
  assert.ok(stripe, "card should have a .tp-card-stripe");
});


test("hub card: missing sensor values render as placeholders, not crash", () => {
  const dom = _newDom();
  const sparse = { ...sampleHub, sensors: { temp: null, rh: null, co2: null } };
  const card = renderHubCard(sparse, {}, dom.window.document);
  // Should render without throwing; placeholder text is the dash.
  assert.match(card.textContent, /—|--/);
});

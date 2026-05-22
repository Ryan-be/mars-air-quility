/**
 * Tests for the topology grow card (Phase 6 Task 6.4).
 *
 * Distinct from `static/js/grow/components/grow-card.mjs` — that one
 * is the fleet-view card on the /grow page, this one is the topology
 * graph card. Filename `topology-grow-card.mjs` to disambiguate.
 *
 * Renders the grow unit's label + plant type + phase chip + three
 * telemetry tiles (soil_moisture / soil_temp_c / air_temp_c) + an
 * optional sparkline of soil moisture history.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderTopologyGrowCard } from "../../static/js/topology/components/topology-grow-card.mjs";


function _newDom() {
  return new JSDOM("<!doctype html><html><body></body></html>");
}


const sampleGrow = {
  id: "grow:1",
  kind: "grow",
  label: "Grow #1",
  plant_type: "tomato",
  phase: "vegetative",
  sensors: { soil_moisture: 58, soil_temp_c: 21.2, air_temp_c: 22.6 },
};


test("topology grow card: returns div.tp-card.tp-card-grow", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(sampleGrow, {}, dom.window.document);
  assert.equal(card.tagName.toLowerCase(), "div");
  assert.ok(card.classList.contains("tp-card"));
  assert.ok(card.classList.contains("tp-card-grow"));
});


test("topology grow card: shows label + plant type", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(sampleGrow, {}, dom.window.document);
  assert.match(card.textContent, /Grow #1/);
  assert.match(card.textContent, /tomato/i);
});


test("topology grow card: shows phase chip", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(sampleGrow, {}, dom.window.document);
  const chip = card.querySelector(".tp-chip-phase");
  assert.ok(chip, "phase chip should be present");
  assert.match(chip.textContent, /vegetative/i);
});


test("topology grow card: three telemetry tiles for moisture + soil temp + air temp", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(sampleGrow, {}, dom.window.document);
  const tiles = card.querySelectorAll(".tp-tile");
  assert.equal(tiles.length, 3);
  const keys = [...tiles].map((t) => t.querySelector(".tp-tile-k").textContent);
  assert.ok(keys.some((k) => /soil|moist/i.test(k)));
  assert.ok(keys.some((k) => /soil.*temp|°c/i.test(k)));
  assert.ok(keys.some((k) => /air/i.test(k)));
});


test("topology grow card: renders soil moisture sparkline when history present", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(
    sampleGrow,
    { soil_moisture: [50, 55, 58, 60, 58] },
    dom.window.document,
  );
  const spark = card.querySelector("svg.tp-spark polyline");
  assert.ok(spark, "soil moisture sparkline should render");
});


test("topology grow card: includes left-edge stripe", () => {
  const dom = _newDom();
  const card = renderTopologyGrowCard(sampleGrow, {}, dom.window.document);
  const stripe = card.querySelector(".tp-card-stripe");
  assert.ok(stripe, "card should have a .tp-card-stripe");
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderLiveReadings } from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("renders one tile per capability", () => {
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
      { channel: "soil_temp_c", is_required: false, unit_label: "°C" },
      { channel: "ambient_lux", is_required: false, unit_label: "lux" },
    ],
    last_known_state: {
      soil_moisture_pct: 58, soil_temp_c: 21.4, ambient_lux: 15420,
      light_state: true,
    },
  };
  const el = renderLiveReadings(unit, document);
  // 3 capability tiles + 1 light state tile (always rendered for required channel)
  const tiles = el.querySelectorAll(".du-stat");
  assert.ok(tiles.length >= 3);
  assert.match(el.textContent, /58%/);
  assert.match(el.textContent, /21.4/);
  assert.match(el.textContent, /15420|15,420/);
});


test("absent capabilities = no tile rendered (not crossed out)", () => {
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
    ],
    last_known_state: { soil_moisture_pct: 58, light_state: true },
  };
  const el = renderLiveReadings(unit, document);
  // No air_temp tile, no ambient_lux tile
  assert.doesNotMatch(el.textContent, /Air temp/i);
  assert.doesNotMatch(el.textContent, /Ambient lux/i);
});


test("low moisture renders warn variant", () => {
  const unit = {
    capabilities: [{ channel: "soil_moisture", is_required: true, unit_label: "raw" }],
    last_known_state: { soil_moisture_pct: 28, light_state: false },
    plant_type: "tomato",
    current_phase: "vegetative",
  };
  const el = renderLiveReadings(unit, document);
  const moistTile = Array.from(el.querySelectorAll(".du-stat"))
    .find(t => /Moisture/i.test(t.textContent));
  assert.match(moistTile.querySelector(".v").className, /warn/);
});


test("empty unit (no capabilities, no telemetry) shows placeholder", () => {
  // Camera-only first-deployment posture (design-critique #9): the
  // panel previously rendered a header-only empty div which looked
  // broken / failed-to-load. Now it renders a friendly explainer.
  const unit = {
    capabilities: [],
    last_known_state: null,
  };
  const el = renderLiveReadings(unit, document);
  const empty = el.querySelector("[data-testid='live-readings-empty']");
  assert.ok(empty, "empty-state placeholder rendered");
  assert.match(empty.textContent, /no telemetry/i);
});


test("capabilities present but values null shows placeholder", () => {
  // Capabilities reported but no readings yet (e.g. sensor wired but
  // not initialised) — same UX: don't show an empty tile grid.
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
    ],
    last_known_state: { soil_moisture_pct: null, light_state: null },
  };
  const el = renderLiveReadings(unit, document);
  // No tiles rendered → empty state should appear
  const empty = el.querySelector("[data-testid='live-readings-empty']");
  assert.ok(empty);
});

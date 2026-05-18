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


// ---------------------------------------------------------------------------
// Plant-happiness wire-through (soil_temp + soil_moisture only).
// renderLiveReadings reads unit.happiness?.soil_temp_c +
// unit.happiness?.soil_moisture_pct (the API response shape) and
// passes the `zone` + `ideal_range` straight into the matching
// renderStatTile call. Other channels (light, lux, air temp, etc.)
// MUST NOT pick up any happiness state.
// ---------------------------------------------------------------------------

test("live readings passes happiness onto the soil_temp tile", () => {
  const unit = {
    capabilities: [
      { channel: "soil_temp_c", is_required: false, unit_label: "°C" },
    ],
    last_known_state: { soil_temp_c: 24, light_state: false },
    happiness: {
      soil_temp_c: {
        zone: "ideal",
        ideal_range: "21–27 °C",
        current: 24,
        thresholds: { critical_min: 13, ideal_min: 21,
                      ideal_max: 27, critical_max: 32 },
      },
    },
  };
  const el = renderLiveReadings(unit, document);
  const tile = el.querySelector(".du-stat");
  assert.match(tile.className, /happy-ideal/);
  const range = tile.querySelector("[data-testid='happy-range']");
  assert.ok(range);
  assert.match(range.textContent, /21–27 °C/);
});


test("live readings passes happiness onto the soil_moisture tile", () => {
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
    ],
    last_known_state: { soil_moisture_pct: 95, light_state: false },
    happiness: {
      soil_moisture_pct: {
        zone: "critical_high",
        ideal_range: "35–60 %",
        current: 95,
        thresholds: { critical_min: 20, ideal_min: 35,
                      ideal_max: 60, critical_max: 85 },
      },
    },
  };
  const el = renderLiveReadings(unit, document);
  const tile = Array.from(el.querySelectorAll(".du-stat"))
    .find(t => /Moisture/i.test(t.textContent));
  assert.match(tile.className, /happy-critical/);
});


test("live readings: other channel tiles do not get happiness props", () => {
  // Only the two wired dimensions read from unit.happiness. lux /
  // air temp / light tiles MUST stay unaffected even when a happiness
  // block is present (so a future shape that hands them happiness
  // data accidentally doesn't break their rendering).
  const unit = {
    capabilities: [
      { channel: "ambient_lux", is_required: false, unit_label: "lux" },
      { channel: "air_temp_c", is_required: false, unit_label: "°C" },
    ],
    last_known_state: {
      ambient_lux: 15420, air_temp_c: 22.5, light_state: false,
    },
    // Garbage happiness data that, if accidentally consumed by these
    // channels, would surface as visible CSS classes — letting the
    // test see the leak.
    happiness: {
      ambient_lux: { zone: "ideal", ideal_range: "0–999 lux" },
      air_temp_c: { zone: "critical_high", ideal_range: "0–999 °C" },
    },
  };
  const el = renderLiveReadings(unit, document);
  const tiles = Array.from(el.querySelectorAll(".du-stat"));
  for (const tile of tiles) {
    assert.doesNotMatch(tile.className, /happy-/,
      `tile got unexpected happy- class: ${tile.className}`);
  }
});


test("live readings: soil_temp tile renders normally when happiness missing", () => {
  // Backward compat: a unit response without a happiness block (older
  // server, or one whose plant_type/phase has no thresholds) must
  // still render the tile without crashing — happy- class absent.
  const unit = {
    capabilities: [
      { channel: "soil_temp_c", is_required: false, unit_label: "°C" },
    ],
    last_known_state: { soil_temp_c: 24, light_state: false },
    // No `happiness` key at all.
  };
  const el = renderLiveReadings(unit, document);
  const tile = el.querySelector(".du-stat");
  assert.doesNotMatch(tile.className, /happy-/);
});


test("live readings: soil_moisture tile keeps warn variant alongside happiness class", () => {
  // The legacy <35 % warn variant stays — it's orthogonal to the
  // happiness border colour (variant colours the .v text; happy-
  // colours the left border). Both signals can co-exist on the same
  // tile and an operator gets BOTH cues.
  const unit = {
    capabilities: [
      { channel: "soil_moisture", is_required: true, unit_label: "raw" },
    ],
    last_known_state: { soil_moisture_pct: 28, light_state: false },
    happiness: {
      soil_moisture_pct: {
        zone: "tolerated_low",
        ideal_range: "35–60 %",
      },
    },
  };
  const el = renderLiveReadings(unit, document);
  const tile = el.querySelector(".du-stat");
  // Border colour reflects zone:
  assert.match(tile.className, /happy-tolerated/);
  // Value text colour still uses the legacy heuristic:
  const v = tile.querySelector(".v");
  assert.match(v.className, /warn/);
});

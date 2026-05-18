/**
 * Tests for the fleet filter/sort row — Phase 2 Task 4.
 *
 * The component renders a row of filter chips (phase, status, plant_type)
 * and a sort dropdown (label / last_seen / moisture). All filtering and
 * sorting happens client-side over the existing fleet data — no new
 * backend calls.
 *
 * The module exports two functions:
 *   - renderFleetFilterRow({units, onChange, ownerDocument}) — the DOM
 *     component that owns filter state + emits onChange when toggled.
 *   - applyFilters(units, state, now=Date.now) — a pure function that
 *     applies the current state to a units list. Tests exercise it
 *     directly to avoid coupling the filter logic to DOM events.
 *
 * "Online" is derived from last_seen_at within the last 5 minutes.
 * Plant-type chips are derived dynamically from the units list — tests
 * verify the chip set is NOT a hardcoded full list.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import {
  renderFleetFilterRow,
  applyFilters,
} from "../../static/js/grow/components/fleet-filter-row.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _now() {
  return new Date("2026-05-06T12:00:00Z").getTime();
}


function _unit(overrides = {}) {
  // Reasonable default that lands "online" for _now() (last_seen_at < 5min ago)
  return {
    id: 1,
    label: "Unit 1",
    plant_type: "tomato",
    medium_type: "soil",
    current_phase: "vegetative",
    last_seen_at: new Date(_now() - 60_000).toISOString(), // 1 min ago
    status: "online",
    last_known_state: { soil_moisture_pct: 50 },
    ...overrides,
  };
}


function _click(el) {
  el.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
}


// ─────────────────────────────────────────────────────────────────────
// 1. Row renders all expected controls
// ─────────────────────────────────────────────────────────────────────
test("test_filter_row_renders_phase_chips_status_chips_sort_dropdown", () => {
  const units = [
    _unit({ id: 1, plant_type: "tomato" }),
    _unit({ id: 2, plant_type: "basil" }),
  ];
  const row = renderFleetFilterRow({
    units,
    onChange: () => {},
    ownerDocument: document,
  });
  // Phase chips: 5 phases
  for (const phase of ["seedling", "vegetative", "flowering", "fruiting", "dormant"]) {
    const chip = row.querySelector(`[data-phase='${phase}']`);
    assert.ok(chip, `phase chip for ${phase} present`);
  }
  // Status chips
  for (const s of ["online", "offline"]) {
    assert.ok(row.querySelector(`[data-status='${s}']`), `status chip ${s} present`);
  }
  // Sort dropdown
  const sortSel = row.querySelector("[data-testid='fleet-filter-sort']");
  assert.ok(sortSel, "sort dropdown present");
  const sortValues = Array.from(sortSel.querySelectorAll("option")).map((o) => o.value);
  for (const v of ["label", "last_seen", "moisture"]) {
    assert.ok(sortValues.includes(v), `sort option ${v} present`);
  }
});


// ─────────────────────────────────────────────────────────────────────
// 2. Phase chip click toggles
// ─────────────────────────────────────────────────────────────────────
test("test_phase_chip_click_toggles_filter", () => {
  let last = null;
  const row = renderFleetFilterRow({
    units: [_unit()],
    onChange: (s) => { last = s; },
    ownerDocument: document,
  });
  const veg = row.querySelector("[data-phase='vegetative']");
  _click(veg);
  assert.match(veg.className, /active/);
  assert.deepEqual(last.phases, ["vegetative"]);
  _click(veg);
  assert.doesNotMatch(veg.className, /active/);
  assert.deepEqual(last.phases, []);
});


// ─────────────────────────────────────────────────────────────────────
// 3. Status filter (online vs offline)
// ─────────────────────────────────────────────────────────────────────
test("test_status_chip_filters_by_online_offline", () => {
  const onlineUnit = _unit({
    id: 1,
    label: "online",
    last_seen_at: new Date(_now() - 60_000).toISOString(),     // 1 min ago
  });
  const offlineUnit = _unit({
    id: 2,
    label: "offline",
    last_seen_at: new Date(_now() - 30 * 60_000).toISOString(), // 30 min ago
  });
  const units = [onlineUnit, offlineUnit];

  const onlineFiltered = applyFilters(
    units, { phases: [], statuses: ["online"], plant_types: [], sort: "label" }, _now,
  );
  assert.deepEqual(onlineFiltered.map((u) => u.id), [1]);

  const offlineFiltered = applyFilters(
    units, { phases: [], statuses: ["offline"], plant_types: [], sort: "label" }, _now,
  );
  assert.deepEqual(offlineFiltered.map((u) => u.id), [2]);
});


// ─────────────────────────────────────────────────────────────────────
// 4. Plant-type chips are derived (NOT hardcoded)
// ─────────────────────────────────────────────────────────────────────
test("test_plant_type_chips_only_render_for_types_present_in_fleet", () => {
  const units = [
    _unit({ id: 1, plant_type: "tomato" }),
    _unit({ id: 2, plant_type: "basil" }),
    _unit({ id: 3, plant_type: "tomato" }),  // duplicate; should still produce only 1 chip
  ];
  const row = renderFleetFilterRow({
    units,
    onChange: () => {},
    ownerDocument: document,
  });
  const chips = Array.from(row.querySelectorAll("[data-plant-type]"));
  const types = chips.map((c) => c.dataset.plantType).sort();
  assert.deepEqual(types, ["basil", "tomato"]);
  // And explicitly: no "lettuce" / "generic" chip even though they're plausible
  assert.equal(row.querySelector("[data-plant-type='lettuce']"), null);
  assert.equal(row.querySelector("[data-plant-type='generic']"), null);
});


// ─────────────────────────────────────────────────────────────────────
// 5. Multi-select within a category = OR
// ─────────────────────────────────────────────────────────────────────
test("test_multiple_phase_filters_combine_with_OR", () => {
  const units = [
    _unit({ id: 1, current_phase: "seedling" }),
    _unit({ id: 2, current_phase: "vegetative" }),
    _unit({ id: 3, current_phase: "flowering" }),
    _unit({ id: 4, current_phase: "dormant" }),
  ];
  const filtered = applyFilters(
    units,
    { phases: ["vegetative", "flowering"], statuses: [], plant_types: [], sort: "label" },
    _now,
  );
  assert.deepEqual(filtered.map((u) => u.id).sort(), [2, 3]);
});


// ─────────────────────────────────────────────────────────────────────
// 6. Across-category = AND
// ─────────────────────────────────────────────────────────────────────
test("test_filters_across_categories_combine_with_AND", () => {
  const units = [
    // online + vegetative → keep
    _unit({
      id: 1,
      current_phase: "vegetative",
      last_seen_at: new Date(_now() - 60_000).toISOString(),
    }),
    // online + flowering → drop (phase mismatch)
    _unit({
      id: 2,
      current_phase: "flowering",
      last_seen_at: new Date(_now() - 60_000).toISOString(),
    }),
    // offline + vegetative → drop (status mismatch)
    _unit({
      id: 3,
      current_phase: "vegetative",
      last_seen_at: new Date(_now() - 30 * 60_000).toISOString(),
    }),
  ];
  const filtered = applyFilters(
    units,
    { phases: ["vegetative"], statuses: ["online"], plant_types: [], sort: "label" },
    _now,
  );
  assert.deepEqual(filtered.map((u) => u.id), [1]);
});


// ─────────────────────────────────────────────────────────────────────
// 7. Sort by label
// ─────────────────────────────────────────────────────────────────────
test("test_sort_by_label_orders_alphabetically", () => {
  const units = [
    _unit({ id: 1, label: "Charlie" }),
    _unit({ id: 2, label: "Alpha" }),
    _unit({ id: 3, label: "Bravo" }),
  ];
  const sorted = applyFilters(
    units,
    { phases: [], statuses: [], plant_types: [], sort: "label" },
    _now,
  );
  assert.deepEqual(sorted.map((u) => u.label), ["Alpha", "Bravo", "Charlie"]);
});


// ─────────────────────────────────────────────────────────────────────
// 8. Sort by last_seen (recent first)
// ─────────────────────────────────────────────────────────────────────
test("test_sort_by_last_seen_orders_most_recent_first", () => {
  const units = [
    _unit({ id: 1, last_seen_at: new Date(_now() - 10 * 60_000).toISOString() }),
    _unit({ id: 2, last_seen_at: new Date(_now() - 1 * 60_000).toISOString() }),
    _unit({ id: 3, last_seen_at: new Date(_now() - 5 * 60_000).toISOString() }),
  ];
  const sorted = applyFilters(
    units,
    { phases: [], statuses: [], plant_types: [], sort: "last_seen" },
    _now,
  );
  assert.deepEqual(sorted.map((u) => u.id), [2, 3, 1]);
});


// ─────────────────────────────────────────────────────────────────────
// 9. Sort by moisture (driest first)
// ─────────────────────────────────────────────────────────────────────
test("test_sort_by_moisture_orders_driest_first", () => {
  const units = [
    _unit({ id: 1, last_known_state: { soil_moisture_pct: 75 } }),
    _unit({ id: 2, last_known_state: { soil_moisture_pct: 22 } }),
    _unit({ id: 3, last_known_state: { soil_moisture_pct: 48 } }),
  ];
  const sorted = applyFilters(
    units,
    { phases: [], statuses: [], plant_types: [], sort: "moisture" },
    _now,
  );
  assert.deepEqual(sorted.map((u) => u.id), [2, 3, 1]);
});


// ─────────────────────────────────────────────────────────────────────
// 10. onChange callback
// ─────────────────────────────────────────────────────────────────────
test("test_filter_state_emits_change_event_to_parent", () => {
  const seen = [];
  const row = renderFleetFilterRow({
    units: [_unit()],
    onChange: (s) => { seen.push(s); },
    ownerDocument: document,
  });
  // Click a phase chip
  _click(row.querySelector("[data-phase='vegetative']"));
  // Click a status chip
  _click(row.querySelector("[data-status='online']"));
  // Change the sort
  const sortSel = row.querySelector("[data-testid='fleet-filter-sort']");
  sortSel.value = "moisture";
  sortSel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

  assert.equal(seen.length, 3);
  assert.deepEqual(seen[0].phases, ["vegetative"]);
  assert.deepEqual(seen[1].statuses, ["online"]);
  assert.equal(seen[2].sort, "moisture");
});


// ─────────────────────────────────────────────────────────────────────
// 11. Empty-set semantics + extras
// ─────────────────────────────────────────────────────────────────────
test("test_empty_filter_state_returns_all_units_unsorted_or_default_sorted", () => {
  const units = [
    _unit({ id: 1, label: "B" }),
    _unit({ id: 2, label: "A" }),
  ];
  const filtered = applyFilters(
    units,
    { phases: [], statuses: [], plant_types: [], sort: "label" },
    _now,
  );
  // No filter applied → all units returned (label sort still kicks in)
  assert.equal(filtered.length, 2);
});


test("test_apply_filters_handles_missing_last_known_state_for_moisture_sort", () => {
  // last_known_state may be null for never-seen units. The sort must not throw
  // and these units should land at the bottom of the "driest first" list (treated as
  // "fully wet / unknown moisture" = highest sort key).
  const units = [
    _unit({ id: 1, last_known_state: null }),
    _unit({ id: 2, last_known_state: { soil_moisture_pct: 30 } }),
  ];
  const sorted = applyFilters(
    units,
    { phases: [], statuses: [], plant_types: [], sort: "moisture" },
    _now,
  );
  assert.deepEqual(sorted.map((u) => u.id), [2, 1]);
});


// ─────────────────────────────────────────────────────────────────────
// 12. Integration with fleet.mjs
// ─────────────────────────────────────────────────────────────────────
test("test_fleet_renders_filter_row_and_filtered_cards", async () => {
  // Build a clean page with the host elements fleet.mjs expects. We
  // import the module fresh (cache-busted via query string) so its
  // top-level event-listener wiring binds to OUR JSDOM document, then
  // call its exported `renderFleet({units, container, filterContainer})`
  // helper to drive the rendering.
  const page = new JSDOM(`<!doctype html><html><body>
    <div id="grow-summary"></div>
    <div id="grow-filter"></div>
    <div id="grow-grid"></div>
  </body></html>`);
  global.document = page.window.document;

  const fleet = await import(
    "../../static/js/grow/fleet.mjs?bust=" + Math.random()
  );

  const units = [
    {
      id: 1, label: "Alpha", plant_type: "tomato", current_phase: "vegetative",
      medium_type: "soil",
      last_seen_at: new Date(_now() - 60_000).toISOString(),
      status: "online",
      last_known_state: { soil_moisture_pct: 50, light_state: false },
    },
    {
      id: 2, label: "Bravo", plant_type: "basil", current_phase: "flowering",
      medium_type: "soil",
      last_seen_at: new Date(_now() - 60_000).toISOString(),
      status: "online",
      last_known_state: { soil_moisture_pct: 40, light_state: false },
    },
  ];

  fleet.renderFleet({ units, ownerDocument: page.window.document });

  // Filter row mounted
  const row = page.window.document.querySelector("[data-testid='fleet-filter-row']");
  assert.ok(row, "filter row mounted");
  // Both cards visible initially
  assert.equal(page.window.document.querySelectorAll(".gu-card").length, 2);

  // Click "vegetative" — only Alpha should remain
  const vegChip = row.querySelector("[data-phase='vegetative']");
  vegChip.dispatchEvent(
    new page.window.Event("click", { bubbles: true, cancelable: true }),
  );
  const cards = page.window.document.querySelectorAll(".gu-card");
  assert.equal(cards.length, 1);
  assert.match(cards[0].textContent, /Alpha/);
});

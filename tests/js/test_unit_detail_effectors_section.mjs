/**
 * Tests for the "Effectors for this unit" Configure-tab section
 * (Phase 9 Task 9.3).
 *
 * When the operator opens a grow unit's Configure tab, a new section
 * appears below the existing Profile / PID / Light windows / Calibration
 * / Safety override panels:
 *
 *   ┌─ Effectors for this unit ──────────────────────────┐
 *   │ Heat pad  · on  · auto                             │
 *   │ Humidifier · off · forced                          │
 *   │                                            ┌────── │
 *   │                                            │ + Add │   ← admin
 *   │                                            └────── │
 *   └────────────────────────────────────────────────────┘
 *
 * The list rows are populated from `unit.effectors`, the new field on
 * the GET /api/grow/units/<id> response (added in the Python half of
 * Phase 9 Task 9.3).
 *
 * The "+ Add effector" button is admin-only (gated on
 * `document.body.dataset.role === "admin"`) and on click dynamically
 * imports the topology add-effector-modal component, opening it with
 * `defaultScope: "grow_unit"` and `defaultGrowUnitId: unit.id` so the
 * modal is pre-scoped to this unit.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import {
  renderSubTabs,
  switchSubtab,
} from "../../static/js/grow/unit_detail.mjs";


function _mountPage(role = "admin") {
  const dom = new JSDOM(`<!doctype html><html><body data-role="${role}">
    <div data-unit-id="3">
      <header id="du-header"></header>
      <nav id="du-tabs"></nav>
      <main id="du-body"></main>
    </div>
  </body></html>`);
  // Don't reassign global.document — mirror the existing
  // test_unit_detail_skeleton.mjs which passes the JSDOM document
  // directly to switchSubtab via the third argument. Reassigning the
  // global keeps JSDOM Socket handles alive and stops the test runner
  // exiting cleanly. The component reads body.dataset.role via the
  // passed-in doc parameter so the role gating still works.
  const doc = dom.window.document;
  doc.getElementById("du-tabs").appendChild(renderSubTabs("configure", doc));
  return { doc, dom };
}


function _unitWithEffectors(effs = []) {
  return {
    id: 3,
    label: "Tomato 3",
    current_phase: "vegetative",
    medium_type: "soil",
    plant_type: "tomato",
    sown_at: "2026-04-10T00:00:00Z",
    status: "online",
    last_seen_at: new Date().toISOString(),
    capabilities: [],
    last_known_state: {},
    overrides: { watering_target: null },
    // dry_raw + wet_raw populated so the calibration wizard renders
    // its summary/done mode instead of step1; that branch doesn't
    // start the 5-second polling loop and Node's --test runner can
    // exit cleanly when the file finishes.
    calibration: { dry_raw: 320, wet_raw: 920 },
    photo_schedule: { start_hour: null, end_hour: null },
    light_windows: {},
    effectors: effs,
  };
}


test("Configure tab: renders 'Effectors for this unit' section", async () => {
  const { doc } = _mountPage("admin");
  await switchSubtab(
    "configure",
    _unitWithEffectors([
      { id: 1, label: "Heat pad", effector_type: "heat_pad",
        current_state: "on", auto_mode: true },
    ]),
    doc,
  );
  const body = doc.getElementById("du-body");
  // The new section carries the data-testid for stable lookup across
  // future styling changes.
  const section = body.querySelector("[data-testid='unit-effectors-section']");
  assert.ok(section, "effectors section is rendered in the Configure tab");
  // Section header carries the canonical "Effectors" wording.
  assert.match(section.textContent.toLowerCase(), /effector/);
});


test("Configure tab: lists effectors from unit.effectors", async () => {
  const { doc } = _mountPage("admin");
  await switchSubtab(
    "configure",
    _unitWithEffectors([
      { id: 1, label: "Heat pad", effector_type: "heat_pad",
        current_state: "on", auto_mode: true },
      { id: 2, label: "Humidifier", effector_type: "humidifier",
        current_state: "off", auto_mode: false },
    ]),
    doc,
  );
  const section = doc.querySelector("[data-testid='unit-effectors-section']");
  const rows = section.querySelectorAll(
    "[data-testid='unit-effector-row']",
  );
  assert.equal(rows.length, 2, `expected 2 rows, got ${rows.length}`);
  // Row content surfaces the label + type + state at minimum so the
  // operator can identify the effector without opening the side panel.
  const texts = Array.from(rows).map((r) => r.textContent.toLowerCase());
  assert.match(texts[0], /heat pad/);
  assert.match(texts[0], /on/);
  assert.match(texts[1], /humidifier/);
  assert.match(texts[1], /off/);
});


test("Configure tab: + Add effector button visible only for admin", async () => {
  // Admin sees the button.
  const { doc: adminDoc } = _mountPage("admin");
  await switchSubtab("configure", _unitWithEffectors(), adminDoc);
  const section = adminDoc.querySelector(
    "[data-testid='unit-effectors-section']",
  );
  assert.ok(
    section.querySelector("[data-testid='unit-add-effector-btn']"),
    "admin sees the + Add effector button",
  );

  // Non-admin does not.
  const { doc: viewerDoc } = _mountPage("viewer");
  await switchSubtab("configure", _unitWithEffectors(), viewerDoc);
  const viewerSection = viewerDoc.querySelector(
    "[data-testid='unit-effectors-section']",
  );
  assert.equal(
    viewerSection.querySelector("[data-testid='unit-add-effector-btn']"),
    null,
    "non-admin viewers don't see the + Add effector button",
  );
});


test("Configure tab: empty effectors list still renders the section + button", async () => {
  const { doc } = _mountPage("admin");
  await switchSubtab("configure", _unitWithEffectors([]), doc);
  const section = doc.querySelector(
    "[data-testid='unit-effectors-section']",
  );
  assert.ok(section, "section renders even when unit has no effectors");
  // No rows expected.
  const rows = section.querySelectorAll(
    "[data-testid='unit-effector-row']",
  );
  assert.equal(rows.length, 0);
  // Button still present for admin.
  assert.ok(
    section.querySelector("[data-testid='unit-add-effector-btn']"),
  );
});

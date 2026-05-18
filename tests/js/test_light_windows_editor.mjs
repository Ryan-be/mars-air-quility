/**
 * Tests for the Light-windows editor panel — third Configure-tab panel
 * delivered in Task 7.
 *
 * Maps to LightWindowsUpdate from mlss_contracts.config_payloads. The
 * server schema replaces all windows for one (unit, phase) pair on each
 * PUT, so the UI saves one phase at a time. Phases the user didn't touch
 * must NOT trigger a PUT — the test_PUTs_per_phase_on_save guards against
 * that regression.
 *
 * Phases are the same _PHASE Literal as the contracts file:
 * seedling / vegetative / flowering / fruiting / dormant.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderLightWindowsEditor } from "../../static/js/grow/components/light-windows-editor.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const PHASES = ["seedling", "vegetative", "flowering", "fruiting", "dormant"];


function _unit(light_windows = {}) {
  return {
    id: 7,
    label: "Tom 1",
    light_windows,
  };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
function _flush() { return new Promise((resolve) => setTimeout(resolve, 0)); }


test("light-windows editor: renders five per-phase groups", () => {
  const el = renderLightWindowsEditor(_unit({
    vegetative: [{ start: "06:00", end: "22:00" }],
    flowering: [],
  }), { ownerDocument: document });
  for (const phase of PHASES) {
    const group = el.querySelector(`[data-testid='lw-phase-${phase}']`);
    assert.ok(group, `phase group for ${phase} present`);
  }
  // Vegetative has one populated row
  const vegRows = el.querySelectorAll(
    "[data-testid='lw-phase-vegetative'] [data-testid^='lw-row-']"
  );
  assert.equal(vegRows.length, 1, "one row in vegetative");
  const start = el.querySelector("[data-testid='lw-phase-vegetative'] [data-testid^='lw-start-']");
  const end = el.querySelector("[data-testid='lw-phase-vegetative'] [data-testid^='lw-end-']");
  assert.equal(start.value, "06:00");
  assert.equal(end.value, "22:00");
  // Flowering shows the empty-state placeholder
  const empty = el.querySelector("[data-testid='lw-empty-flowering']");
  assert.ok(empty, "empty-state placeholder present for flowering");
  assert.match(empty.textContent, /default|no windows/i);
});


test("light-windows editor: clicking + Add window adds a new empty row", () => {
  const el = renderLightWindowsEditor(_unit({}), { ownerDocument: document });
  const addBtn = el.querySelector("[data-testid='lw-add-vegetative']");
  assert.ok(addBtn);
  addBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const rows = el.querySelectorAll(
    "[data-testid='lw-phase-vegetative'] [data-testid^='lw-row-']"
  );
  assert.equal(rows.length, 1, "one row after Add");
  // Empty values
  const start = rows[0].querySelector("[data-testid^='lw-start-']");
  const end = rows[0].querySelector("[data-testid^='lw-end-']");
  assert.equal(start.value, "");
  assert.equal(end.value, "");
  // Remove button is present on the new row
  assert.ok(rows[0].querySelector("[data-testid^='lw-remove-']"));
});


test("light-windows editor: clicking remove deletes a row", () => {
  const el = renderLightWindowsEditor(_unit({
    vegetative: [{ start: "06:00", end: "22:00" }],
  }), { ownerDocument: document });
  const remove = el.querySelector(
    "[data-testid='lw-phase-vegetative'] [data-testid^='lw-remove-']"
  );
  assert.ok(remove);
  remove.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
  const rows = el.querySelectorAll(
    "[data-testid='lw-phase-vegetative'] [data-testid^='lw-row-']"
  );
  assert.equal(rows.length, 0, "row removed");
});


test("light-windows editor: PUTs per-phase on Save (no other-phase touches)", async () => {
  const orig = _origFetch();
  const captured = [];
  _setMockFetch(async (url, opts) => {
    captured.push({ url, opts });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderLightWindowsEditor(_unit({
      vegetative: [{ start: "06:00", end: "22:00" }],
      flowering: [{ start: "08:00", end: "20:00" }],
    }), { ownerDocument: document });

    // Edit only vegetative — change end to 21:00
    const vegEnd = el.querySelector(
      "[data-testid='lw-phase-vegetative'] [data-testid^='lw-end-']"
    );
    vegEnd.value = "21:00";
    vegEnd.dispatchEvent(new dom.window.Event("input", { bubbles: true }));

    const saveVeg = el.querySelector("[data-testid='lw-save-vegetative']");
    saveVeg.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flush();
    await _flush();

    // Exactly one PUT: vegetative
    assert.equal(captured.length, 1);
    const c = captured[0];
    assert.equal(c.url, "/api/grow/units/7/light_windows");
    assert.equal(c.opts.method, "PUT");
    const body = JSON.parse(c.opts.body);
    assert.equal(body.phase, "vegetative");
    assert.deepEqual(body.windows, [{ start: "06:00", end: "21:00" }]);
  } finally {
    _setMockFetch(orig);
  }
});


test("light-windows editor: rejects bad HH:MM client-side", async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => {
    called = true;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderLightWindowsEditor(_unit({
      vegetative: [{ start: "06:00", end: "22:00" }],
    }), { ownerDocument: document });
    const start = el.querySelector(
      "[data-testid='lw-phase-vegetative'] [data-testid^='lw-start-']"
    );
    start.value = "6am";
    start.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    el.querySelector("[data-testid='lw-save-vegetative']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    assert.equal(called, false, "fetch must not fire for invalid HH:MM");
    const status = el.querySelector("[data-testid='lw-status-vegetative']");
    assert.match(status.textContent, /invalid|hh:mm|format/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});


test("light-windows editor: rejects zero-length window client-side", async () => {
  const orig = _origFetch();
  let called = false;
  _setMockFetch(async () => {
    called = true;
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const el = renderLightWindowsEditor(_unit({
      vegetative: [{ start: "06:00", end: "22:00" }],
    }), { ownerDocument: document });
    const end = el.querySelector(
      "[data-testid='lw-phase-vegetative'] [data-testid^='lw-end-']"
    );
    end.value = "06:00";  // start === end
    end.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    el.querySelector("[data-testid='lw-save-vegetative']").dispatchEvent(
      new dom.window.Event("click", { bubbles: true, cancelable: true })
    );
    await _flush();
    assert.equal(called, false, "fetch must not fire for zero-length window");
    const status = el.querySelector("[data-testid='lw-status-vegetative']");
    assert.match(status.textContent, /differ|zero|same/i);
    assert.match(status.className, /err/);
  } finally {
    _setMockFetch(orig);
  }
});


test("light-windows editor: caps at 8 windows per phase (Add disables)", () => {
  const windows8 = Array.from({ length: 8 }, (_, i) => ({
    start: `0${i}:00`.slice(-5),
    end: `0${i}:30`.slice(-5),
  }));
  const el = renderLightWindowsEditor(_unit({ vegetative: windows8 }), {
    ownerDocument: document,
  });
  const addBtn = el.querySelector("[data-testid='lw-add-vegetative']");
  assert.ok(addBtn);
  assert.equal(addBtn.disabled, true, "Add disabled at 8 windows");
});


// ─── Accordion behaviour (design-critique #12) ────────────────────


test("accordion: current phase open by default, others collapsed", () => {
  const el = renderLightWindowsEditor({
    id: 7, light_windows: {}, current_phase: "vegetative",
  }, { ownerDocument: document });
  // The current phase's <details> has the `open` attribute
  const veg = el.querySelector("[data-testid='lw-phase-vegetative']");
  assert.ok(veg.hasAttribute("open"),
    "vegetative (current phase) should be open by default");
  // Other phases are NOT open
  for (const phase of ["seedling", "flowering", "fruiting", "dormant"]) {
    const group = el.querySelector(`[data-testid='lw-phase-${phase}']`);
    assert.ok(!group.hasAttribute("open"),
      `${phase} should be collapsed by default`);
  }
});


test("accordion: current phase shows 'current' tag", () => {
  const el = renderLightWindowsEditor({
    id: 7, light_windows: {}, current_phase: "flowering",
  }, { ownerDocument: document });
  const flowering = el.querySelector("[data-testid='lw-phase-flowering']");
  assert.match(flowering.querySelector("summary").textContent, /current/i);
  // Non-current phases don't carry the tag
  const seedling = el.querySelector("[data-testid='lw-phase-seedling']");
  assert.doesNotMatch(seedling.querySelector("summary").textContent, /current/i);
});


test("accordion: summary hint reports window count or 'profile default'", () => {
  const el = renderLightWindowsEditor({
    id: 7,
    light_windows: {
      vegetative: [
        { start: "06:00", end: "12:00" },
        { start: "14:00", end: "20:00" },
      ],
    },
    current_phase: "vegetative",
  }, { ownerDocument: document });
  const vegHint = el.querySelector(
    "[data-testid='lw-summary-hint-vegetative']"
  );
  assert.match(vegHint.textContent, /2 windows/);
  // Empty phase reports profile-default
  const seedlingHint = el.querySelector(
    "[data-testid='lw-summary-hint-seedling']"
  );
  assert.match(seedlingHint.textContent.toLowerCase(), /default|inheriting/);
});


test("accordion: defaults to vegetative when current_phase is missing", () => {
  // Backward-compat: tests that pre-date the accordion don't pass
  // current_phase. We default to 'vegetative' so behaviour is consistent.
  const el = renderLightWindowsEditor({
    id: 7, light_windows: {},
  }, { ownerDocument: document });
  const veg = el.querySelector("[data-testid='lw-phase-vegetative']");
  assert.ok(veg.hasAttribute("open"));
});

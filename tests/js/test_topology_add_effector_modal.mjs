/**
 * Tests for the add-effector modal (Phase 9 Task 9.1).
 *
 * Modal launched from two entry points:
 *   1. Topbar "+ Add effector" button (admin only) — defaultScope="hub".
 *   2. Grow-unit Configure tab "+ Add effector" button — defaultScope="grow_unit",
 *      defaultGrowUnitId=<unit.id>.
 *
 * Contents:
 *   * Header: "Add effector" + close × button
 *   * 11 effector_type radios: fan / fan_carbon_filter / circulation_fan /
 *     ac / whole_room_heater / humidifier / dehumidifier /
 *     light_supplementary / heat_pad / generic / co2_injector
 *   * Scope picker: Hub vs Grow unit
 *   * Grow-unit `<select>` (visible only when Grow scope picked)
 *   * Label + kasa_host inputs
 *   * Cancel / Submit buttons
 *
 * Validation: empty label OR empty kasa_host → inline error.
 *
 * Type ↔ scope compatibility matrix (mirrors
 * mlss_monitor.effectors.base.COMPATIBLE_SCOPES):
 *
 *   fan / fan_carbon_filter / circulation_fan / ac /
 *   whole_room_heater / dehumidifier / co2_injector → HUB only
 *   heat_pad                                        → GROW only
 *   humidifier / light_supplementary / generic      → both
 *
 * Selecting an effector type whose compat set excludes a scope must
 * disable the corresponding radio button (and switch the selection to
 * the only compatible scope if needed).
 *
 * Submit → POST /api/effectors with the body:
 *   {effector_type, scope, grow_unit_id, label, kasa_host,
 *    is_enabled:1, auto_mode:1, rules:{}}
 * 201 → onCreated(newEffector) + close.
 * 409 (duplicate kasa_host) → inline error, modal stays open.
 *
 * Dismissal: × button, ESC key, backdrop click — all close the modal.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { openAddEffectorModal } from
  "../../static/js/topology/components/add-effector-modal.mjs";


function _newDom() {
  const dom = new JSDOM(
    `<!doctype html><html><body data-role="admin"></body></html>`,
  );
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}


async function _flushMicro() {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}


function _baseOpts(dom, over = {}) {
  return {
    ownerDocument: dom.window.document,
    defaultScope: "hub",
    defaultGrowUnitId: null,
    onCreated: () => {},
    fetchFn: async () => new Response(JSON.stringify({}), { status: 200 }),
    ...over,
  };
}


test("add-effector modal: renders 11 effector_type radios", () => {
  const dom = _newDom();
  const { close, element } = openAddEffectorModal(_baseOpts(dom));
  try {
    const radios = element.querySelectorAll(
      "input[type='radio'][name='effector_type']",
    );
    assert.equal(radios.length, 11,
      `expected 11 effector_type radios, got ${radios.length}`);
    const values = new Set(Array.from(radios).map((r) => r.value));
    // Every type from database/effectors_schema._EFFECTOR_TYPES is
    // present. Mirrors the SQL CHECK constraint so the picker can't
    // surface a value the DB will reject.
    for (const t of [
      "fan", "fan_carbon_filter", "circulation_fan", "ac",
      "whole_room_heater", "humidifier", "dehumidifier",
      "light_supplementary", "heat_pad", "generic", "co2_injector",
    ]) {
      assert.ok(values.has(t), `effector_type radio missing: ${t}`);
    }
  } finally {
    close();
  }
});


test("add-effector modal: default scope from prop is pre-selected", () => {
  const dom = _newDom();
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    defaultScope: "grow_unit",
  }));
  try {
    const hub = element.querySelector(
      "input[type='radio'][name='scope'][value='hub']",
    );
    const grow = element.querySelector(
      "input[type='radio'][name='scope'][value='grow_unit']",
    );
    assert.ok(hub, "hub radio present");
    assert.ok(grow, "grow radio present");
    assert.equal(hub.checked, false);
    assert.equal(grow.checked, true);
  } finally {
    close();
  }
});


test("add-effector modal: default grow_unit_id pre-selects when passed", async () => {
  const dom = _newDom();
  // The modal fetches /api/grow/units to populate the dropdown; stub
  // it to return two units so the test can assert the pre-selection.
  const fetchFn = async (url) => {
    if (url === "/api/grow/units") {
      return new Response(JSON.stringify({
        units: [
          { id: 5, label: "Tomato 1" },
          { id: 9, label: "Basil 1" },
        ],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    defaultScope: "grow_unit",
    defaultGrowUnitId: 9,
    fetchFn,
  }));
  try {
    // Wait for the /api/grow/units fetch + options population.
    await _flushMicro();
    await _flushMicro();
    const sel = element.querySelector(
      "select[name='grow_unit_id']",
    );
    assert.ok(sel, "grow-unit select present");
    assert.equal(sel.value, "9",
      `expected unit 9 pre-selected, got ${sel.value}`);
  } finally {
    close();
  }
});


test("add-effector modal: empty label → inline error on submit", async () => {
  const dom = _newDom();
  let fetchCalls = 0;
  const fetchFn = async () => {
    fetchCalls += 1;
    return new Response(JSON.stringify({}), { status: 200 });
  };
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    fetchFn,
  }));
  try {
    await _flushMicro();
    // Leave label empty; fill kasa_host so we isolate the label
    // validation path.
    const host = element.querySelector("input[name='kasa_host']");
    host.value = "192.0.2.10";
    host.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    const submit = element.querySelector("button[data-action='submit']");
    submit.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    const err = element.querySelector("[data-testid='add-effector-error']");
    assert.ok(err, "inline error element present");
    assert.notEqual(err.style.display, "none",
      "error element is visible after a failed validation");
    // The error mentions the missing field so the operator knows
    // what to fix.
    assert.match(err.textContent.toLowerCase(), /label/);
    // No POST to /api/effectors — the validation gate ran client-side.
    assert.equal(fetchCalls, 1,
      "only the /api/grow/units bootstrap fetch ran, no POST yet");
  } finally {
    close();
  }
});


test("add-effector modal: empty kasa_host → inline error on submit", async () => {
  const dom = _newDom();
  const { close, element } = openAddEffectorModal(_baseOpts(dom));
  try {
    await _flushMicro();
    const label = element.querySelector("input[name='label']");
    label.value = "Cabinet fan";
    label.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    const submit = element.querySelector("button[data-action='submit']");
    submit.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    const err = element.querySelector("[data-testid='add-effector-error']");
    assert.ok(err);
    assert.notEqual(err.style.display, "none");
    assert.match(err.textContent.toLowerCase(), /host|address/);
  } finally {
    close();
  }
});


test("add-effector modal: selecting heat_pad disables the Hub radio", () => {
  const dom = _newDom();
  const { close, element } = openAddEffectorModal(_baseOpts(dom));
  try {
    const heatPad = element.querySelector(
      "input[type='radio'][name='effector_type'][value='heat_pad']",
    );
    heatPad.checked = true;
    heatPad.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    const hub = element.querySelector(
      "input[type='radio'][name='scope'][value='hub']",
    );
    assert.equal(hub.disabled, true,
      "Hub radio should be disabled when heat_pad is selected");
    const grow = element.querySelector(
      "input[type='radio'][name='scope'][value='grow_unit']",
    );
    assert.equal(grow.disabled, false);
    assert.equal(grow.checked, true,
      "Scope auto-switches to the only compatible option");
  } finally {
    close();
  }
});


test("add-effector modal: selecting fan disables the Grow radio", () => {
  const dom = _newDom();
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    // Start the modal on grow_unit so flipping to fan can be observed
    // switching back to hub.
    defaultScope: "grow_unit",
    defaultGrowUnitId: 1,
  }));
  try {
    const fan = element.querySelector(
      "input[type='radio'][name='effector_type'][value='fan']",
    );
    fan.checked = true;
    fan.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    const grow = element.querySelector(
      "input[type='radio'][name='scope'][value='grow_unit']",
    );
    assert.equal(grow.disabled, true,
      "Grow radio should be disabled when fan is selected");
    const hub = element.querySelector(
      "input[type='radio'][name='scope'][value='hub']",
    );
    assert.equal(hub.checked, true,
      "Scope auto-switches to hub when fan is selected");
  } finally {
    close();
  }
});


test("add-effector modal: submit POSTs to /api/effectors with the full body", async () => {
  const dom = _newDom();
  let posted = null;
  const fetchFn = async (url, opts) => {
    if (url === "/api/grow/units") {
      return new Response(JSON.stringify({ units: [] }), { status: 200 });
    }
    if (url === "/api/effectors" && opts && opts.method === "POST") {
      posted = { url, opts };
      return new Response(JSON.stringify({
        id: 42, label: "Cabinet fan", effector_type: "fan", scope: "hub",
      }), { status: 201 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };
  let created = null;
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    fetchFn,
    onCreated: (eff) => { created = eff; },
  }));
  try {
    await _flushMicro();
    element.querySelector("input[name='label']").value = "Cabinet fan";
    element.querySelector("input[name='kasa_host']").value = "192.0.2.30";
    // Pick fan (hub-only) so the scope gating is set up correctly.
    const fan = element.querySelector(
      "input[type='radio'][name='effector_type'][value='fan']",
    );
    fan.checked = true;
    fan.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    element.querySelector("button[data-action='submit']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    await _flushMicro();

    assert.ok(posted, "POST /api/effectors fired");
    assert.equal(posted.opts.method, "POST");
    const body = JSON.parse(posted.opts.body);
    assert.equal(body.effector_type, "fan");
    assert.equal(body.scope, "hub");
    assert.equal(body.label, "Cabinet fan");
    assert.equal(body.kasa_host, "192.0.2.30");
    assert.equal(body.is_enabled, 1);
    assert.equal(body.auto_mode, 1);
    // hub-scoped → grow_unit_id must be null
    assert.equal(body.grow_unit_id, null);
    // rules defaults to an empty dict so the server doesn't need to
    // special-case missing rules vs explicit {}.
    assert.deepEqual(body.rules, {});

    // onCreated invoked with the response body
    assert.ok(created);
    assert.equal(created.id, 42);

    // Modal closed after a 201
    assert.equal(
      dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
      null,
    );
  } finally {
    // close() is idempotent on an already-detached overlay.
    close();
  }
});


test("add-effector modal: 409 duplicate kasa_host surfaces inline error + stays open", async () => {
  const dom = _newDom();
  const fetchFn = async (url, opts) => {
    if (url === "/api/grow/units") {
      return new Response(JSON.stringify({ units: [] }), { status: 200 });
    }
    if (url === "/api/effectors" && opts && opts.method === "POST") {
      return new Response(JSON.stringify({
        error: "duplicate_kasa_host",
      }), { status: 409 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };
  let created = null;
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    fetchFn,
    onCreated: (eff) => { created = eff; },
  }));
  try {
    await _flushMicro();
    element.querySelector("input[name='label']").value = "X";
    element.querySelector("input[name='kasa_host']").value = "192.0.2.40";
    element.querySelector("button[data-action='submit']")
      .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
    await _flushMicro();
    await _flushMicro();

    const err = element.querySelector("[data-testid='add-effector-error']");
    assert.ok(err);
    assert.notEqual(err.style.display, "none",
      "error message is visible after a 409");
    assert.match(err.textContent.toLowerCase(),
      /duplicate|already|in use|exists/);
    // onCreated NOT called
    assert.equal(created, null);
    // Modal stays open so the operator can correct the host + retry
    assert.ok(
      dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
      "modal stays open after 409",
    );
  } finally {
    close();
  }
});


test("add-effector modal: ESC key closes the modal", () => {
  const dom = _newDom();
  openAddEffectorModal(_baseOpts(dom));
  assert.ok(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
  );
  const ev = new dom.window.KeyboardEvent("keydown", { key: "Escape" });
  dom.window.document.dispatchEvent(ev);
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
    null,
  );
});


test("add-effector modal: × button closes the modal", () => {
  const dom = _newDom();
  const { element } = openAddEffectorModal(_baseOpts(dom));
  element.querySelector("[data-testid='add-effector-close']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
    null,
  );
});


test("add-effector modal: backdrop click closes; box click doesn't", () => {
  const dom = _newDom();
  const { element } = openAddEffectorModal(_baseOpts(dom));

  // Click inside the box — modal stays open.
  element.querySelector("[data-testid='add-effector-box']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.ok(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
  );
  // Click on the overlay itself — closes.
  element.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
    null,
  );
});


test("add-effector modal: cancel button closes the modal", () => {
  const dom = _newDom();
  const { element } = openAddEffectorModal(_baseOpts(dom));
  element.querySelector("button[data-action='cancel']")
    .dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  assert.equal(
    dom.window.document.querySelector("[data-testid='add-effector-overlay']"),
    null,
  );
});


test("add-effector modal: grow-unit select shown only when scope=grow_unit", async () => {
  const dom = _newDom();
  const fetchFn = async (url) => {
    if (url === "/api/grow/units") {
      return new Response(JSON.stringify({
        units: [{ id: 1, label: "U" }],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  };
  const { close, element } = openAddEffectorModal(_baseOpts(dom, {
    defaultScope: "hub",
    fetchFn,
  }));
  try {
    await _flushMicro();
    await _flushMicro();
    const sel = element.querySelector("select[name='grow_unit_id']");
    assert.ok(sel, "grow-unit select element exists in the DOM");
    // Hidden when scope=hub
    const wrapper = sel.closest(".add-effector-grow-row")
      || sel.parentElement;
    assert.ok(wrapper.classList.contains("hidden")
      || wrapper.style.display === "none",
      "grow-unit row is hidden when scope=hub");

    // Flip scope to grow_unit
    const grow = element.querySelector(
      "input[type='radio'][name='scope'][value='grow_unit']",
    );
    grow.checked = true;
    grow.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    assert.ok(!wrapper.classList.contains("hidden")
      && wrapper.style.display !== "none",
      "grow-unit row is shown when scope=grow_unit");
  } finally {
    close();
  }
});

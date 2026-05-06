import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderDetailHeader, renderSubTabs } from "../../static/js/grow/unit_detail.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 3, label: "Tomato 3", current_phase: "vegetative",
  medium_type: "soil", sown_at: "2026-04-10T00:00:00Z",
  status: "online", last_seen_at: new Date().toISOString(),
  capabilities: [], last_known_state: {},
};


test("detail header renders title + phase + status pill", () => {
  const el = renderDetailHeader(sampleUnit, document);
  assert.match(el.textContent, /Tomato 3/);
  assert.match(el.textContent, /vegetative/i);
  assert.ok(el.querySelector(".gu-status"));
});

test("detail header includes back link to /grow", () => {
  const el = renderDetailHeader(sampleUnit, document);
  const back = el.querySelector("a.du-back");
  assert.ok(back);
  assert.equal(back.getAttribute("href"), "/grow");
});

test("sub-tabs: Live is the active tab; deferred phases marked disabled", () => {
  const el = renderSubTabs("live", document);
  const live = el.querySelector("[data-tab='live']");
  assert.match(live.className, /active/);
  // history + diagnostics are still deferred (Phase 2/3 in the plan).
  for (const tab of ["history", "diagnostics"]) {
    const t = el.querySelector(`[data-tab='${tab}']`);
    assert.ok(t.disabled || t.classList.contains("disabled"));
  }
});


test("sub-tabs: Configure is enabled (Task 6 of Configure-tab plan)", () => {
  const el = renderSubTabs("live", document);
  const configure = el.querySelector("[data-tab='configure']");
  assert.equal(configure.disabled, false);
  assert.ok(!configure.classList.contains("disabled"));
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderStatTile } from "../../static/js/grow/components/stat-tile.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("stat tile: required channel rendered with green left bar", () => {
  const el = renderStatTile({
    value: "58%", label: "Moisture", isRequired: true, ownerDocument: document,
  });
  assert.match(el.className, /required-marker/);
});

test("stat tile: optional channel rendered with blue left bar", () => {
  const el = renderStatTile({
    value: "21.4°C", label: "Soil temp", isRequired: false,
    ownerDocument: document,
  });
  assert.match(el.className, /optional-marker/);
});

test("stat tile: warn variant for low moisture", () => {
  const el = renderStatTile({
    value: "28%", label: "Moisture", isRequired: true, variant: "warn",
    ownerDocument: document,
  });
  const v = el.querySelector(".v");
  assert.match(v.className, /warn/);
});

test("stat tile: includes optional sub-text", () => {
  const el = renderStatTile({
    value: "58%", label: "Moisture", sub: "target 55%", isRequired: true,
    ownerDocument: document,
  });
  const sub = el.querySelector(".sub");
  assert.equal(sub.textContent, "target 55%");
});


// ---------------------------------------------------------------------------
// Plant-happiness overlay tests. The stat-tile component takes a
// `happiness` prop (one of: null, "ideal", "tolerated_low",
// "tolerated_high", "critical_low", "critical_high") and an
// `idealRange` text. The 5 zones map onto 3 colour buckets via the
// happy-ideal / happy-tolerated / happy-critical classes.
// ---------------------------------------------------------------------------

test("stat tile: happy-ideal class when happiness=ideal", () => {
  const el = renderStatTile({
    value: "24°C", label: "Soil temp",
    happiness: "ideal", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  assert.match(el.className, /happy-ideal/);
  // testid mirrors the class for easy querying.
  assert.equal(el.dataset.testid, "stat-tile-happy-ideal");
});

test("stat tile: happy-critical class when happiness=critical_high", () => {
  const el = renderStatTile({
    value: "40°C", label: "Soil temp",
    happiness: "critical_high", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  assert.match(el.className, /happy-critical/);
});

test("stat tile: happy-critical class when happiness=critical_low", () => {
  // Both critical_low and critical_high resolve to happy-critical —
  // colour-bucket count is 3 (good/warn/bad), not 5.
  const el = renderStatTile({
    value: "8°C", label: "Soil temp",
    happiness: "critical_low", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  assert.match(el.className, /happy-critical/);
});

test("stat tile: happy-tolerated class for tolerated_low and tolerated_high", () => {
  for (const zone of ["tolerated_low", "tolerated_high"]) {
    const el = renderStatTile({
      value: "x", label: "y", happiness: zone, idealRange: "21–27 °C",
      ownerDocument: document,
    });
    assert.match(el.className, /happy-tolerated/, `zone=${zone}`);
  }
});

test("stat tile: shows ideal_range subtext when supplied", () => {
  const el = renderStatTile({
    value: "24°C", label: "Soil temp",
    happiness: "ideal", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  const range = el.querySelector("[data-testid='happy-range']");
  assert.ok(range, "happy-range subtext rendered");
  assert.match(range.textContent, /21–27 °C/);
});

test("stat tile: idealRange also mirrored onto title attribute", () => {
  // Hover affordance — same text the operator sees in the subtext is
  // also surfaced via title= so they don't need to scan down.
  const el = renderStatTile({
    value: "24°C", label: "Soil temp",
    happiness: "ideal", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  assert.match(el.title, /21–27 °C/);
});

test("stat tile: no happy-* class when happiness=null (backward compat)", () => {
  // Existing tiles (lux, ambient, air temp, light) MUST NOT pick up
  // any happiness class. This guards the additive-only contract.
  const el = renderStatTile({
    value: "15420", label: "Ambient lux", isRequired: false,
    ownerDocument: document,
  });
  assert.doesNotMatch(el.className, /happy-/);
  assert.equal(el.dataset.testid, undefined);
});

test("stat tile: happiness class follows the required/optional-marker class", () => {
  // Source-order dependency: the happy-* CSS rule overrides the
  // border-left-color set on .required-marker / .optional-marker.
  // Both share specificity, so the happy- class MUST appear later
  // in the className string to win the cascade.
  const el = renderStatTile({
    value: "x", label: "y", isRequired: true,
    happiness: "ideal", idealRange: "21–27 °C",
    ownerDocument: document,
  });
  const idx_marker = el.className.indexOf("required-marker");
  const idx_happy = el.className.indexOf("happy-");
  assert.ok(idx_marker >= 0 && idx_happy >= 0);
  assert.ok(idx_happy > idx_marker,
    `happy- class must appear AFTER required-marker, got className=${el.className}`);
});

test("stat tile: no happy-range subtext when only happiness is supplied (no range)", () => {
  // Defensive: a caller passing happiness="ideal" but no idealRange
  // string should still render the colour class but skip the subtext.
  const el = renderStatTile({
    value: "x", label: "y", happiness: "ideal",
    ownerDocument: document,
  });
  assert.match(el.className, /happy-ideal/);
  assert.equal(el.querySelector("[data-testid='happy-range']"), null);
});

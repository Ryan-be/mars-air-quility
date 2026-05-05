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

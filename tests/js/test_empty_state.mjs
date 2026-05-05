import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderEmptyState } from "../../static/js/grow/components/empty-state.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("renders 5 numbered steps", () => {
  const el = renderEmptyState({ enrollmentKey: "test-key-123", mlssHost: "mlss.local" }, document);
  const steps = el.querySelectorAll(".step");
  assert.equal(steps.length, 5);
});


test("displays the enrollment key", () => {
  const el = renderEmptyState({ enrollmentKey: "test-key-123", mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /test-key-123/);
});


test("includes the install one-liner", () => {
  const el = renderEmptyState({ enrollmentKey: "x", mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /curl.*mlss\.local.*install\.sh/);
});


test("when no key (already revealed) shows rotation note", () => {
  const el = renderEmptyState({ enrollmentKey: null, mlssHost: "mlss.local" }, document);
  assert.match(el.textContent, /already revealed|rotate|Settings/i);
});

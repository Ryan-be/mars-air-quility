/**
 * Smoke test: mobile.css exists and contains the required @media block
 * plus the key rules. No JSDOM CSS engine is full-featured enough to
 * meaningfully test rendered layout; this is a structural sanity check.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const cssPath = resolve(__dirname, "..", "..", "static", "css", "mobile.css");


test("mobile.css exists", () => {
  assert.ok(existsSync(cssPath), "static/css/mobile.css must exist");
});


test("mobile.css contains the 768px media query", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /@media\s*\(\s*max-width:\s*768px\s*\)/);
});


test("mobile.css has bottom-nav transform", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /nav\.tab-nav[^{]*\{[^}]*position:\s*fixed/s);
  assert.match(css, /bottom:\s*0/);
});


test("mobile.css sets 44px min on inputs", () => {
  const css = readFileSync(cssPath, "utf8");
  // Apple HIG tap target — 44×44 minimum
  assert.match(css, /min-height:\s*44px/);
});


test("mobile.css makes settings-grid single column", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /\.settings-grid\s*\{[^}]*grid-template-columns:\s*1fr/s);
});


test("mobile.css makes tables horizontal-scroll", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /overflow-x:\s*auto/);
});


test("base.html links mobile.css after base.css", () => {
  const tplPath = resolve(__dirname, "..", "..", "templates", "base.html");
  const tpl = readFileSync(tplPath, "utf8");
  const baseIdx   = tpl.indexOf("css/base.css");
  const mobileIdx = tpl.indexOf("css/mobile.css");
  assert.ok(baseIdx   >= 0, "base.css must be linked");
  assert.ok(mobileIdx >= 0, "mobile.css must be linked");
  assert.ok(mobileIdx > baseIdx,
            "mobile.css must come AFTER base.css so it can override");
});

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


test("mobile.css has bottom-nav floating pill", () => {
  const css = readFileSync(cssPath, "utf8");
  assert.match(css, /nav\.tab-nav[^{]*\{[^}]*position:\s*fixed/s);
  // Floating pill — bottom/left/right are insets, not 0, so the nav
  // doesn't extend into the iPhone's curved corners or under the home
  // indicator. Uses max() to promote literal pixels to safe-area-inset
  // values on devices that report non-zero insets (landscape, etc.).
  assert.match(css, /bottom:\s*max\(\s*8px\s*,\s*env\(safe-area-inset-bottom/);
  assert.match(css, /left:\s*max\(\s*8px\s*,\s*env\(safe-area-inset-left/);
  assert.match(css, /right:\s*max\(\s*8px\s*,\s*env\(safe-area-inset-right/);
  assert.match(css, /border-radius:\s*\d+px/);
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

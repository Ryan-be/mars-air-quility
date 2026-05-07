/**
 * Tests for the firmware-info card — first section of the Diagnostics tab.
 *
 * Pure render component: takes the diagnostics response slice + returns a
 * DOM node. Tests:
 *   - renders version string + formatted uptime + buffer size
 *   - null fields render as em-dashes
 *   - _formatUptime helper boundary cases
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderFirmwareInfo, _formatUptime } from
  "../../static/js/grow/components/firmware-info.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


test("firmware-info: renders version + formatted uptime + buffer size", () => {
  const data = {
    firmware_version: "0.3.1",
    uptime_s: 192732,  // 2d 5h 32m
    buffer_size: 7,
  };
  const el = renderFirmwareInfo(data, { ownerDocument: document });
  const ver = el.querySelector("[data-testid='diag-firmware-version']");
  assert.equal(ver.textContent, "0.3.1");
  const up = el.querySelector("[data-testid='diag-firmware-uptime']");
  assert.match(up.textContent, /2d/);
  assert.match(up.textContent, /5h/);
  assert.match(up.textContent, /32m/);
  const buf = el.querySelector("[data-testid='diag-firmware-buffer']");
  assert.match(buf.textContent, /7/);
  assert.match(buf.textContent, /rows/);
});


test("firmware-info: null fields render as em-dash", () => {
  const data = {
    firmware_version: null,
    uptime_s: null,
    buffer_size: null,
  };
  const el = renderFirmwareInfo(data, { ownerDocument: document });
  const ver = el.querySelector("[data-testid='diag-firmware-version']");
  assert.equal(ver.textContent, "—");
  const up = el.querySelector("[data-testid='diag-firmware-uptime']");
  assert.equal(up.textContent, "—");
  const buf = el.querySelector("[data-testid='diag-firmware-buffer']");
  assert.equal(buf.textContent, "—");
});


test("firmware-info: zero buffer size renders explicitly (not as em-dash)", () => {
  // Distinct from null: 0 is a valid size meaning "buffer is empty".
  // The operator needs to see this to confirm e.g. a clear-buffer
  // command actually landed.
  const data = {
    firmware_version: "0.3.1",
    uptime_s: 60,
    buffer_size: 0,
  };
  const el = renderFirmwareInfo(data, { ownerDocument: document });
  const buf = el.querySelector("[data-testid='diag-firmware-buffer']");
  assert.match(buf.textContent, /0/);
  assert.notEqual(buf.textContent, "—");
});


test("_formatUptime: minutes-only when under 1 hour", () => {
  assert.equal(_formatUptime(0), "0m");
  assert.equal(_formatUptime(30), "0m");  // 30s rounds to 0m
  assert.equal(_formatUptime(59), "0m");
  assert.equal(_formatUptime(60), "1m");
  assert.equal(_formatUptime(3540), "59m");  // 59 minutes
});


test("_formatUptime: hours+minutes when 1h ≤ uptime < 1d", () => {
  assert.equal(_formatUptime(3600), "1h 0m");
  assert.equal(_formatUptime(3700), "1h 1m");
  assert.equal(_formatUptime(7200), "2h 0m");
  assert.equal(_formatUptime(86399), "23h 59m");
});


test("_formatUptime: days+hours+minutes when uptime ≥ 1d", () => {
  assert.equal(_formatUptime(86400), "1d 0h 0m");
  assert.equal(_formatUptime(90000), "1d 1h 0m");
  assert.equal(_formatUptime(192732), "2d 5h 32m");  // 2d 5h 32m 12s
});


test("_formatUptime: null / negative / NaN return em-dash", () => {
  assert.equal(_formatUptime(null), "—");
  assert.equal(_formatUptime(undefined), "—");
  assert.equal(_formatUptime(-1), "—");
  assert.equal(_formatUptime("not-a-number"), "—");
});

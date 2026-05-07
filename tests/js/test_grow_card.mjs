import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderGrowCard } from "../../static/js/grow/components/grow-card.mjs";

const dom = new JSDOM();
global.document = dom.window.document;

const sampleUnit = {
  id: 3,
  label: "Tomato 3",
  plant_type: "tomato",
  medium_type: "soil",
  current_phase: "vegetative",
  sown_at: "2026-04-10T00:00:00Z",
  last_seen_at: new Date().toISOString(),
  status: "online",
  last_known_state: {
    soil_moisture_pct: 58,
    light_state: true,
  },
};


test("grow card: shows label and phase + medium meta", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.match(card.textContent, /Tomato 3/);
  assert.match(card.textContent, /vegetative/);
  assert.match(card.textContent, /soil/i);
});

test("grow card: status pill present", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.ok(card.querySelector(".gu-status"));
});

test("grow card: identify button has data-action=identify", () => {
  const card = renderGrowCard(sampleUnit, document);
  const btn = card.querySelector("[data-action='identify']");
  assert.ok(btn);
});

test("grow card: open button links to /grow/<id>", () => {
  const card = renderGrowCard(sampleUnit, document);
  const openBtn = card.querySelector("[data-action='open']");
  assert.ok(openBtn);
  assert.match(openBtn.dataset.href || openBtn.href, /\/grow\/3/);
});

test("grow card: stale variant gets stale class", () => {
  const stale = { ...sampleUnit, status: "stale" };
  const card = renderGrowCard(stale, document);
  assert.match(card.className, /stale/);
});

test("grow card: offline variant gets offline class", () => {
  const offline = { ...sampleUnit, status: "offline" };
  const card = renderGrowCard(offline, document);
  assert.match(card.className, /offline/);
});

test("grow card: shows moisture % from last_known_state", () => {
  const card = renderGrowCard(sampleUnit, document);
  assert.match(card.textContent, /58%/);
});

test("grow card: shows 'No photo yet' when no recent photo", () => {
  const newUnit = { ...sampleUnit, last_known_state: { ...sampleUnit.last_known_state } };
  newUnit.last_known_state.last_photo_url = null;
  const card = renderGrowCard(newUnit, document);
  assert.match(card.textContent, /No photo|—/);
});

test("grow card: shows buffered badge when buffer_size positive", () => {
  // Phase 3 Task 6: surface last_buffer_size as a "📦 N buffered" badge
  // next to the status pill so operators see at-a-glance which units
  // had a recent connection drop and are still draining their queue.
  const buffered = { ...sampleUnit, last_buffer_size: 7 };
  const card = renderGrowCard(buffered, document);
  const badge = card.querySelector(".gu-card-buffered-badge");
  assert.ok(badge, "badge should render when buffer > 0");
  assert.match(badge.textContent, /7 buffered/);
});

test("grow card: omits buffered badge when zero or null", () => {
  // Healthy units (buffer=0) and pre-Phase-3 firmware (buffer=null)
  // get no badge — keeps cards uncluttered for the common case.
  for (const value of [0, null, undefined]) {
    const u = { ...sampleUnit, last_buffer_size: value };
    const card = renderGrowCard(u, document);
    assert.equal(
      card.querySelector(".gu-card-buffered-badge"), null,
      `badge should be absent for last_buffer_size=${value}`,
    );
  }
});

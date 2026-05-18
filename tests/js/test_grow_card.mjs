import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderGrowCard } from "../../static/js/grow/components/grow-card.mjs";

const dom = new JSDOM();
global.document = dom.window.document;
// Bug 1 added a card-level click handler that reads window.location.href
// — set up global.window so the renderer can resolve it. The tests that
// assert navigation behaviour replace window with a stub locally.
global.window = dom.window;

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

test("grow card: photo backgroundImage uses the server-provided URL verbatim", () => {
  // Bug 5: the server-side `_last_known_state` now returns the
  // `?size=thumb` variant directly in `last_photo_url` (centralised
  // server-side so any future consumer can't forget). The renderer
  // should just consume the URL as-is — no more client-side append.
  const withPhoto = {
    ...sampleUnit,
    last_known_state: {
      ...sampleUnit.last_known_state,
      last_photo_url: "/api/grow/units/3/photos/42?size=thumb",
    },
  };
  const card = renderGrowCard(withPhoto, document);
  const photoEl = card.querySelector(".gu-photo");
  assert.ok(photoEl, "photo element should be present");
  assert.match(
    photoEl.style.backgroundImage,
    /\/api\/grow\/units\/3\/photos\/42\?size=thumb/,
    "photo backgroundImage should use the server URL verbatim (incl ?size=thumb)",
  );
});


/**
 * Helper for Bug 1 navigation tests: swap global.window for a stub
 * that captures location.href writes. The grow-card click handler
 * reads `window.location.href` so this is the only seam we need to
 * intercept. Restores the original window on teardown.
 */
function _withCapturedNavigation(fn) {
  const captured = [];
  const realWindow = global.window;
  global.window = {
    location: {
      get href() { return ""; },
      set href(v) { captured.push(v); },
    },
    MouseEvent: realWindow.MouseEvent,
  };
  try {
    fn(captured);
  } finally {
    global.window = realWindow;
  }
}


test("grow card: whole-card click navigates to /grow/<id>", () => {
  // Bug 1: clicking anywhere on the card (except inner buttons /
  // links) navigates to the unit detail page. The Open → link's href
  // is the source of truth — copy from openBtn.href.
  const card = renderGrowCard(sampleUnit, document);
  _withCapturedNavigation((captured) => {
    // Click on the stats area (a safe non-button target inside the card)
    const stats = card.querySelector(".gu-stats");
    stats.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    assert.equal(captured.length, 1, "exactly one navigation triggered");
    assert.match(captured[0], /\/grow\/3$/, "navigates to /grow/<unit.id>");
  });
});

test("grow card: click on Identify button does not double-navigate", () => {
  // Bug 1 guard: clicking the Identify button must NOT also trigger
  // the whole-card navigation. We bail in the card handler via
  // `event.target.closest('button, a')`.
  const card = renderGrowCard(sampleUnit, document);
  _withCapturedNavigation((captured) => {
    const identify = card.querySelector("[data-action='identify']");
    identify.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    assert.equal(captured.length, 0,
      "Identify button click should NOT trigger card-level navigation");
  });
});

test("grow card: click on Open link does not double-navigate via card handler", () => {
  // Bug 1 guard: the Open → link is an <a> and handles its own
  // navigation. The card handler must bail out so we don't
  // double-assign location.href.
  const card = renderGrowCard(sampleUnit, document);
  _withCapturedNavigation((captured) => {
    const openLink = card.querySelector("[data-action='open']");
    // Suppress JSDOM's default <a> navigation so we can assert purely on
    // whether the card handler also fired.
    openLink.addEventListener("click", (ev) => ev.preventDefault());
    openLink.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    assert.equal(captured.length, 0,
      "click on Open <a> should NOT also trigger the card-level handler");
  });
});

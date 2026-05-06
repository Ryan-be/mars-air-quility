/**
 * Tests for the photo-timelapse component — second panel of the
 * History tab delivered in Task 4 of the History-tab plan.
 *
 * Backend contract (Task 2):
 *   GET /api/grow/units/<id>/photos?range=<r>
 *     -> [{id, taken_at, telemetry_id}, …] (metadata only — no JPEG bytes)
 *   GET /api/grow/units/<id>/photos/<photo_id>
 *     -> JPEG bytes (loaded by the browser when img.src is set)
 *
 * The widget renders a range selector (24h/7d/30d/90d/all), a hero
 * <img>, a slider with one position per photo, a play/pause button
 * and a caption with the current photo's taken_at.
 *
 * Lazy loading: only the visible photo is fetched. We don't preload
 * — setting img.src triggers the browser to fetch (and cache) on its
 * own. That's why we assert on src URLs rather than fetch calls when
 * the slider moves.
 *
 * Fetch is mocked the same way as test_moisture_history_chart.mjs.
 * For the autoplay timer, we use node:test's t.mock.timers — same
 * pattern as test_safety_override.mjs.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { renderPhotoTimelapse } from "../../static/js/grow/components/photo-timelapse.mjs";

const dom = new JSDOM();
global.document = dom.window.document;


function _unit() {
  return { id: 7, label: "Tom 1" };
}


function _origFetch() { return globalThis.fetch; }
function _setMockFetch(fn) { globalThis.fetch = fn; }
/** Microtask flush — settle the fetch promise + the await r.json() chain
 *  inside loadAndRender. Matches the pattern in test_moisture_history_chart.mjs. */
async function _flushMicro() {
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
}


/** Build a JSON Response wrapper for the mock fetch. */
function _ok(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


/** Helper: build N photo entries with monotonically increasing taken_at. */
function _photos(n, startId = 100) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({
      id: startId + i,
      taken_at: `2026-05-06T${String(10 + i).padStart(2, "0")}:00:00Z`,
      telemetry_id: 5000 + i,
    });
  }
  return out;
}


test("photo timelapse: renders range selector with 5 options", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok([]));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const selector = el.querySelector("[data-testid='tlapse-range-selector']");
    assert.ok(selector, "range selector container present");
    for (const r of ["24h", "7d", "30d", "90d", "all"]) {
      const btn = el.querySelector(`[data-testid='tlapse-range-${r}']`);
      assert.ok(btn, `range button for ${r} present`);
    }
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: default range is 24h and triggers initial fetch with ?range=24h", async () => {
  const orig = _origFetch();
  let captured = null;
  _setMockFetch(async (url) => {
    captured = url;
    return _ok([]);
  });
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    assert.ok(captured, "fetch was called on initial render");
    assert.match(String(captured), /\/api\/grow\/units\/7\/photos\?range=24h/);
    const active = el.querySelector("[data-testid='tlapse-range-24h']");
    assert.match(active.className, /active/, "the 24h button has the active class");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: scrubber max equals photos.length - 1 (one position per photo)", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok(_photos(10)));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.ok(slider, "slider rendered");
    assert.equal(slider.type, "range");
    assert.equal(slider.min, "0");
    assert.equal(slider.max, "9", "max is N-1 for N=10 photos");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: default position is the latest (rightmost) photo", async () => {
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "9", "default value points at the last photo");
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.ok(img, "img rendered");
    const lastId = photos[9].id;
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${lastId}$`),
      "img src points at the latest photo");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: changing scrubber position loads photo by id", async () => {
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    const img = el.querySelector("[data-testid='tlapse-img']");
    slider.value = "3";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    const targetId = photos[3].id;
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${targetId}$`),
      "img src updated to the photo at position 3");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: play button advances through timelapse on each tick", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  // Start at position 0 by giving few photos so we can reset position later
  // — actually, default = latest. We need to start before the end so play has
  // somewhere to advance to. Easiest path: many photos + manually slide back.
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    // Move to position 0 so we have room to play forward
    slider.value = "0";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    assert.equal(slider.value, "0");

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // Advance three intervals (3 × 500ms = 1500ms)
    t.mock.timers.tick(1500);
    assert.equal(slider.value, "3", "slider advanced 3 positions after 3 ticks");
    const img = el.querySelector("[data-testid='tlapse-img']");
    const targetId = photos[3].id;
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${targetId}$`),
      "img src advanced with the slider");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: play stops at end (no loop) and button reverts to ▶", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(10);  // max = 9
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    // Start at position 5
    slider.value = "5";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // Advance until we reach the end: 5 → 6 → 7 → 8 → 9 = 4 ticks (2000ms)
    // Then one more tick to confirm we don't blow past 9.
    t.mock.timers.tick(2000);
    assert.equal(slider.value, "9", "slider reached the last position");
    t.mock.timers.tick(500);
    assert.equal(slider.value, "9", "slider does NOT advance past max");
    // The play button label should have reverted to play ▶
    assert.match(play.textContent, /▶/, "play button label reverts to ▶ when at end");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: caption shows the current photo's taken_at", async () => {
  const orig = _origFetch();
  const photos = _photos(5);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const caption = el.querySelector("[data-testid='tlapse-caption']");
    assert.ok(caption, "caption present");
    // Default = latest (index 4)
    assert.ok(
      caption.textContent.includes(photos[4].taken_at) ||
      caption.textContent.includes("14:00"),
      `caption mentions latest taken_at (${photos[4].taken_at}); got: ${caption.textContent}`,
    );
    // Move scrubber → caption changes
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    slider.value = "1";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    assert.ok(
      caption.textContent.includes(photos[1].taken_at) ||
      caption.textContent.includes("11:00"),
      `caption updates to position 1's taken_at (${photos[1].taken_at}); got: ${caption.textContent}`,
    );
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: empty state when no photos in range", async () => {
  const orig = _origFetch();
  _setMockFetch(async () => _ok([]));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const empty = el.querySelector("[data-testid='tlapse-empty']");
    assert.ok(empty, "empty-state element rendered");
    assert.match(empty.textContent, /no photos/i, "empty copy mentions no photos");
    // Slider should be hidden / not rendered
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider, null, "no slider when zero photos");
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.equal(img, null, "no hero image when zero photos");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: changing range refetches and resets to latest", async () => {
  const orig = _origFetch();
  const calls = [];
  // First call: 10 photos (24h). Second call: 5 photos (7d).
  let n = 0;
  _setMockFetch(async (url) => {
    calls.push(String(url));
    n += 1;
    if (n === 1) return _ok(_photos(10, 100));
    return _ok(_photos(5, 200));
  });
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    assert.equal(calls.length, 1);
    assert.match(calls[0], /\?range=24h/);
    // Scrub away from the latest position
    let slider = el.querySelector("[data-testid='tlapse-slider']");
    slider.value = "2";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    assert.equal(slider.value, "2");

    // Click the 7d button
    const sevenDay = el.querySelector("[data-testid='tlapse-range-7d']");
    sevenDay.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    assert.equal(calls.length, 2, "second fetch fires on range change");
    assert.match(calls[1], /\?range=7d/);

    // Slider element is re-rendered after the new fetch — query again
    slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.ok(slider, "slider re-rendered after range change");
    assert.equal(slider.max, "4", "max is N-1 for N=5 photos in the new range");
    assert.equal(slider.value, "4", "scrubber resets to the latest position after range change");
  } finally {
    _setMockFetch(orig);
  }
});

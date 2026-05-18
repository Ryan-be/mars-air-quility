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
 * <img>, a slider with one position per photo, playback controls and
 * a caption with the current photo's taken_at.
 *
 * Lazy loading: only the visible photo is fetched. We don't preload
 * — setting img.src triggers the browser to fetch (and cache) on its
 * own. That's why we assert on src URLs rather than fetch calls when
 * the slider moves.
 *
 * Hero img + scrubber view request `?size=thumb` so scrubbing is
 * cheap. The lightbox handoff intentionally constructs full-res URLs.
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


test("photo timelapse: default range is 7d and triggers initial fetch with ?range=7d", async () => {
  // Bug 3(b): the default range used to be 24h. For plant-growth viewing
  // that's almost always too short to see meaningful change. 7d is a
  // much more useful starting point.
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
    assert.match(String(captured), /\/api\/grow\/units\/7\/photos\?range=7d/);
    const active = el.querySelector("[data-testid='tlapse-range-7d']");
    assert.match(active.className, /active/, "the 7d button has the active class");
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


test("photo timelapse: default position is the earliest (leftmost) photo", async () => {
  // Bug 3(a): used to default to the latest. For plant-growth viewing
  // you almost always want to start from the earliest and watch growth
  // play forward.
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "0", "default value points at the FIRST photo");
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.ok(img, "img rendered");
    const firstId = photos[0].id;
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${firstId}\\?size=thumb$`),
      "img src points at the earliest photo (using ?size=thumb)");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: hero img URL includes ?size=thumb", async () => {
  // Bug 5(b): the hero img + scrubber view both request the server-side
  // 320px ~30KB thumbnail variant. The lightbox handoff (next test)
  // is the only consumer that gets full-res URLs.
  const orig = _origFetch();
  const photos = _photos(5);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.match(img.src, /\?size=thumb$/,
      "hero img src must include ?size=thumb for the cheap-scrub case");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: changing scrubber position loads photo by id (thumb)", async () => {
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
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${targetId}\\?size=thumb$`),
      "img src updated to the (thumb) photo at position 3");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: play button advances through timelapse on each tick", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    // Default now starts at 0, so play can advance forward immediately.
    assert.equal(slider.value, "0");

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // Advance three intervals (3 × 500ms = 1500ms)
    t.mock.timers.tick(1500);
    assert.equal(slider.value, "3", "slider advanced 3 positions after 3 ticks at 1×");
    const img = el.querySelector("[data-testid='tlapse-img']");
    const targetId = photos[3].id;
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${targetId}\\?size=thumb$`),
      "img src advanced with the slider (thumb URL)");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: play stops at end (no loop) and button reverts to play", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(10);  // max = 9
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    // Start at position 5 (not the default 0) so we have less to walk through.
    slider.value = "5";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // Advance until we reach the end: 5 → 6 → 7 → 8 → 9 = 4 ticks (2000ms)
    // Then one more tick to confirm we don't blow past 9.
    t.mock.timers.tick(2000);
    assert.equal(slider.value, "9", "slider reached the last position");
    t.mock.timers.tick(500);
    assert.equal(slider.value, "9", "slider does NOT advance past max (loop=off default)");
    // The play button label should have reverted to play
    assert.match(play.textContent, /▶/, "play button reverts to ▶ when at end with loop off");
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
    // Default = earliest (index 0)
    assert.ok(
      caption.textContent.includes(photos[0].taken_at) ||
      caption.textContent.includes("10:00"),
      `caption mentions earliest taken_at (${photos[0].taken_at}); got: ${caption.textContent}`,
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


test("photo timelapse: changing range refetches and resets to earliest", async () => {
  const orig = _origFetch();
  const calls = [];
  // First call: 10 photos (default 7d). Second call: 5 photos (24h).
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
    assert.match(calls[0], /\?range=7d/);
    // Scrub forward from the default earliest position
    let slider = el.querySelector("[data-testid='tlapse-slider']");
    slider.value = "2";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    assert.equal(slider.value, "2");

    // Click the 24h button
    const oneDay = el.querySelector("[data-testid='tlapse-range-24h']");
    oneDay.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    await _flushMicro();
    assert.equal(calls.length, 2, "second fetch fires on range change");
    assert.match(calls[1], /\?range=24h/);

    // Slider element is re-rendered after the new fetch — query again
    slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.ok(slider, "slider re-rendered after range change");
    assert.equal(slider.max, "4", "max is N-1 for N=5 photos in the new range");
    assert.equal(slider.value, "0", "scrubber resets to the EARLIEST position after range change");
  } finally {
    _setMockFetch(orig);
  }
});


// ─── Bug 3 / Bug 5 — new playback controls + thumb wiring ───────────────

test("photo timelapse: skip-to-end button jumps to last position", async () => {
  // Bug 3(c): ⏭ sets position = photos.length - 1.
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const skipEnd = el.querySelector("[data-testid='tlapse-skip-end']");
    assert.ok(skipEnd, "skip-to-end button present");
    skipEnd.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "9", "scrubber jumps to last position");
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${photos[9].id}\\?size=thumb$`),
      "img updated to last photo (thumb)");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: skip-to-start button jumps to first position", async () => {
  // Bug 3(c): ⏮ sets position = 0. Test it after first scrubbing forward
  // so we know it's actually doing something.
  const orig = _origFetch();
  const photos = _photos(10);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    // Scrub forward first
    let slider = el.querySelector("[data-testid='tlapse-slider']");
    slider.value = "5";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true, cancelable: true }));
    assert.equal(slider.value, "5");
    // Now hit skip-to-start
    const skipStart = el.querySelector("[data-testid='tlapse-skip-start']");
    assert.ok(skipStart, "skip-to-start button present");
    skipStart.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "0", "scrubber jumps back to first position");
    const img = el.querySelector("[data-testid='tlapse-img']");
    assert.match(img.src, new RegExp(`/api/grow/units/7/photos/${photos[0].id}\\?size=thumb$`),
      "img updated to first photo (thumb)");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: speed dropdown changes autoplay tick interval", async (t) => {
  // Bug 3(d): 1× = 500ms, 2× = 250ms, 4× = 125ms, 8× = 62.5ms.
  // Verify by picking 2× and checking that 1000ms = 4 ticks (not 2).
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(20);  // plenty of room
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const speedSel = el.querySelector("[data-testid='tlapse-speed']");
    assert.ok(speedSel, "speed dropdown present");
    // Default 1× — confirm value (not asserting on label so we don't pin × glyph)
    assert.equal(speedSel.value, "1", "default speed is 1×");
    // Switch to 2× before play starts
    speedSel.value = "2";
    speedSel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // At 2× speed (250ms tick), 1000ms = 4 ticks
    t.mock.timers.tick(1000);
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "4", "1000ms at 2× should be 4 ticks");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: changing speed mid-play restarts the interval immediately", async (t) => {
  // Bug 3(d) refinement: changing speed during playback restarts the
  // interval so the new speed takes effect immediately (not after the
  // next play-toggle).
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(50);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));
    // Run 1 tick at 1× (500ms)
    t.mock.timers.tick(500);
    let slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "1");

    // Switch to 4× (125ms tick) mid-play
    const speedSel = el.querySelector("[data-testid='tlapse-speed']");
    speedSel.value = "4";
    speedSel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    // 500ms at 4× = 4 more ticks → position 5
    t.mock.timers.tick(500);
    slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "5", "post-speed-change ticks use the new interval");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: loop checkbox wraps autoplay end → start", async (t) => {
  // Bug 3(e): when Loop is checked, autoplay continues past the end by
  // wrapping back to position 0 rather than stopping.
  t.mock.timers.enable({ apis: ["setInterval"] });
  const orig = _origFetch();
  const photos = _photos(5);  // max = 4
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    // Enable loop
    const loopCb = el.querySelector("[data-testid='tlapse-loop']");
    assert.ok(loopCb, "loop checkbox present");
    assert.equal(loopCb.checked, false, "loop is off by default");
    loopCb.checked = true;
    loopCb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));

    const play = el.querySelector("[data-testid='tlapse-play']");
    play.dispatchEvent(new dom.window.Event("click", { bubbles: true, cancelable: true }));

    // 0 → 1 → 2 → 3 → 4 → (wrap) 0  = 5 ticks (2500ms) → position 0 again
    t.mock.timers.tick(2500);
    const slider = el.querySelector("[data-testid='tlapse-slider']");
    assert.equal(slider.value, "0", "loop wraps end → start");
    // Play button still shows pause (we kept playing)
    assert.match(play.textContent, /⏸/, "play button stays at ⏸ when looping");
  } finally {
    _setMockFetch(orig);
  }
});


test("photo timelapse: lightbox handoff URLs do NOT include ?size=thumb", async () => {
  // Bug 5(c): clicking the hero img opens the lightbox — the lightbox
  // IS the "give me the big version" affordance, so it must use the
  // full-res endpoint URLs, not the thumb variant.
  const orig = _origFetch();
  const photos = _photos(3);
  _setMockFetch(async () => _ok(photos));
  try {
    const el = renderPhotoTimelapse(_unit(), { ownerDocument: document });
    await _flushMicro();
    const img = el.querySelector("[data-testid='tlapse-img']");
    // Click the hero — this opens the lightbox in nav mode
    img.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
    // The lightbox attaches itself to document.body
    const lbImg = document.querySelector("[data-testid='lightbox-img']");
    assert.ok(lbImg, "lightbox img mounted");
    assert.ok(!lbImg.src.includes("size=thumb"),
      `lightbox img must NOT use the thumb variant; got: ${lbImg.src}`);
    assert.match(lbImg.src, new RegExp(`/api/grow/units/7/photos/${photos[0].id}$`),
      "lightbox img src is the full-res URL");
    // Close the lightbox for tests that come after
    const closeBtn = document.querySelector("[data-testid='lightbox-close']");
    closeBtn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  } finally {
    _setMockFetch(orig);
  }
});

/**
 * Tests for the photo-lightbox modal — Task 3 of the Phase 2 finisher
 * plan.
 *
 * Two render modes:
 *   - Single photo: { photoUrl } → image + close, no nav arrows
 *   - Nav context: { photos: [{id, taken_at, url}], currentIndex, unitId }
 *     → image + close + prev/next arrows + caption
 *
 * Dismiss surface: ESC, backdrop click, close button. Keyboard arrows
 * navigate when nav context is present. Prev disabled at index 0;
 * next disabled at the last index.
 *
 * Pattern follows test_photo_timelapse.mjs + test_safety_override.mjs:
 * node:test + JSDOM, data-testid selectors, JSDOM-constructed events.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { openLightbox } from "../../static/js/grow/components/photo-lightbox.mjs";


/** Fresh JSDOM per test — the lightbox attaches a global keydown listener
 *  to the document so we want isolation. */
function _makeDoc() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>");
  return { dom, doc: dom.window.document };
}


function _photos(n, startId = 100) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({
      id: startId + i,
      taken_at: `2026-05-06T${String(10 + i).padStart(2, "0")}:00:00Z`,
      url: `/api/grow/units/7/photos/${startId + i}`,
    });
  }
  return out;
}


test("test_lightbox_renders_with_photo_url", () => {
  const { doc } = _makeDoc();
  openLightbox({ photoUrl: "/api/grow/units/7/photo/latest?ts=1", ownerDocument: doc });
  const img = doc.querySelector("[data-testid='lightbox-img']");
  assert.ok(img, "lightbox img rendered");
  assert.equal(img.tagName, "IMG");
  assert.match(img.src, /\/api\/grow\/units\/7\/photo\/latest/);
});


test("test_lightbox_dismisses_on_escape_key", () => {
  const { dom, doc } = _makeDoc();
  openLightbox({ photoUrl: "/img.jpg", ownerDocument: doc });
  assert.ok(doc.querySelector("[data-testid='lightbox-overlay']"), "overlay present before ESC");
  const ev = new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true });
  doc.dispatchEvent(ev);
  assert.equal(doc.querySelector("[data-testid='lightbox-overlay']"), null,
    "overlay removed from DOM after ESC");
});


test("test_lightbox_dismisses_on_backdrop_click", () => {
  const { dom, doc } = _makeDoc();
  openLightbox({ photoUrl: "/img.jpg", ownerDocument: doc });
  const overlay = doc.querySelector("[data-testid='lightbox-overlay']");
  assert.ok(overlay, "overlay present");
  // Click on the backdrop (the overlay itself)
  overlay.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.equal(doc.querySelector("[data-testid='lightbox-overlay']"), null,
    "overlay removed on backdrop click");

  // Now reopen and click the photo — should NOT close
  openLightbox({ photoUrl: "/img.jpg", ownerDocument: doc });
  const img = doc.querySelector("[data-testid='lightbox-img']");
  img.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.ok(doc.querySelector("[data-testid='lightbox-overlay']"),
    "overlay still present after clicking the image itself");
});


test("test_lightbox_with_nav_context_shows_prev_next", () => {
  const { doc } = _makeDoc();
  openLightbox({
    photos: _photos(5),
    currentIndex: 2,
    unitId: 7,
    ownerDocument: doc,
  });
  assert.ok(doc.querySelector("[data-testid='lightbox-prev']"), "prev button present");
  assert.ok(doc.querySelector("[data-testid='lightbox-next']"), "next button present");
});


test("test_lightbox_without_nav_context_omits_prev_next", () => {
  const { doc } = _makeDoc();
  openLightbox({ photoUrl: "/img.jpg", ownerDocument: doc });
  assert.equal(doc.querySelector("[data-testid='lightbox-prev']"), null,
    "prev button absent in single-photo mode");
  assert.equal(doc.querySelector("[data-testid='lightbox-next']"), null,
    "next button absent in single-photo mode");
});


test("test_lightbox_prev_button_decrements_index_and_updates_src", () => {
  const { dom, doc } = _makeDoc();
  const photos = _photos(5);
  openLightbox({ photos, currentIndex: 2, unitId: 7, ownerDocument: doc });
  const img = doc.querySelector("[data-testid='lightbox-img']");
  assert.match(img.src, new RegExp(`${photos[2].id}$`), "starts at index 2");
  const prev = doc.querySelector("[data-testid='lightbox-prev']");
  prev.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.match(img.src, new RegExp(`${photos[1].id}$`),
    "src updated to photos[1] url after prev click");
});


test("test_lightbox_next_button_increments_index_and_updates_src", () => {
  const { dom, doc } = _makeDoc();
  const photos = _photos(5);
  openLightbox({ photos, currentIndex: 2, unitId: 7, ownerDocument: doc });
  const img = doc.querySelector("[data-testid='lightbox-img']");
  assert.match(img.src, new RegExp(`${photos[2].id}$`), "starts at index 2");
  const next = doc.querySelector("[data-testid='lightbox-next']");
  next.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.match(img.src, new RegExp(`${photos[3].id}$`),
    "src updated to photos[3] url after next click");
});


test("test_lightbox_arrow_keys_navigate_when_nav_present", () => {
  const { dom, doc } = _makeDoc();
  const photos = _photos(5);
  openLightbox({ photos, currentIndex: 2, unitId: 7, ownerDocument: doc });
  const img = doc.querySelector("[data-testid='lightbox-img']");
  // Right arrow → next
  doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.match(img.src, new RegExp(`${photos[3].id}$`),
    "right arrow advances to photos[3]");
  // Left arrow → prev (back to 2)
  doc.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
  assert.match(img.src, new RegExp(`${photos[2].id}$`),
    "left arrow goes back to photos[2]");
});


test("test_lightbox_disables_prev_at_start_and_next_at_end", () => {
  // At index 0 → prev disabled, next enabled
  const { dom: dom1, doc: doc1 } = _makeDoc();
  const photos1 = _photos(5);
  openLightbox({ photos: photos1, currentIndex: 0, unitId: 7, ownerDocument: doc1 });
  let prev = doc1.querySelector("[data-testid='lightbox-prev']");
  let next = doc1.querySelector("[data-testid='lightbox-next']");
  assert.equal(prev.disabled, true, "prev disabled at index 0");
  assert.equal(next.disabled, false, "next enabled at index 0");

  // At last index → prev enabled, next disabled
  const { doc: doc2 } = _makeDoc();
  const photos2 = _photos(5);
  openLightbox({ photos: photos2, currentIndex: 4, unitId: 7, ownerDocument: doc2 });
  prev = doc2.querySelector("[data-testid='lightbox-prev']");
  next = doc2.querySelector("[data-testid='lightbox-next']");
  assert.equal(prev.disabled, false, "prev enabled at last index");
  assert.equal(next.disabled, true, "next disabled at last index");

  // After clicking next from index 0 to 1, both should be enabled
  next = doc1.querySelector("[data-testid='lightbox-next']");
  next.dispatchEvent(new dom1.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  prev = doc1.querySelector("[data-testid='lightbox-prev']");
  next = doc1.querySelector("[data-testid='lightbox-next']");
  assert.equal(prev.disabled, false, "prev re-enabled after stepping forward");
  assert.equal(next.disabled, false, "next still enabled at index 1 of 5");
});

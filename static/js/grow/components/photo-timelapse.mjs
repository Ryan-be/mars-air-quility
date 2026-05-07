/**
 * Photo timelapse scrubber for the History tab — Task 4 of the
 * History-tab plan.
 *
 * Range selector: 24h / 7d / 30d / 90d / all. Default 24h. Same shape
 * and styling as the moisture-history-chart, so the two panels feel
 * like one widget when stacked.
 *
 * Backend contract:
 *   GET /api/grow/units/<id>/photos?range=<r>
 *     -> [{id, taken_at, telemetry_id}, …] (metadata only, no JPEG bytes)
 *   GET /api/grow/units/<id>/photos/<photo_id>
 *     -> JPEG bytes — the browser fetches these on demand when img.src
 *        is set, so we never bulk-preload.
 *
 * The scrubber has one position per photo in the range. Default position
 * is the rightmost (latest) so the panel opens on the most recent
 * snapshot — which is what users want 95% of the time. They scrub left
 * to go back in time.
 *
 * Play/pause autoplay: setInterval at PLAY_INTERVAL_MS, advancing the
 * scrubber by 1 each tick. Stops at the end (does not loop) and the
 * play button label flips back to ▶.
 *
 * On range change: stop autoplay, refetch the photo list, reset position
 * to the latest. The scrubber and hero img get re-rendered because
 * `slider.max` changes.
 *
 * Why we don't preload: a 90d range can be hundreds of photos at ~200KB
 * each — that's tens of MB the user may never see. The browser caches
 * the by-id endpoint anyway, so re-scrubbing is fast after the first
 * pass.
 */

import { openLightbox } from "./photo-lightbox.mjs";

const RANGES = ["24h", "7d", "30d", "90d", "all"];

const PLAY_INTERVAL_MS = 500;


/**
 * Build the photo timelapse panel.
 *
 * @param {object} unit  GET /api/grow/units/<id> response (must include `id`)
 * @param {object} opts  { ownerDocument? }
 * @returns {HTMLElement}
 */
export function renderPhotoTimelapse(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.className = "du-panel hist-tlapse";
  wrap.dataset.testid = "photo-timelapse";

  // ── header
  const head = doc.createElement("div");
  head.className = "du-panel-head";
  head.innerHTML = "<span>📷 Photo timelapse</span>";
  wrap.appendChild(head);

  // ── range selector
  let currentRange = "24h";
  const selector = doc.createElement("div");
  selector.className = "hist-range-selector";
  selector.dataset.testid = "tlapse-range-selector";
  for (const r of RANGES) {
    const btn = doc.createElement("button");
    btn.type = "button";
    btn.dataset.testid = `tlapse-range-${r}`;
    btn.dataset.range = r;
    btn.textContent = r;
    btn.className = r === currentRange ? "active" : "";
    selector.appendChild(btn);
  }
  wrap.appendChild(selector);

  // ── body — image + scrubber + controls + caption (or empty state)
  //    Re-rendered on every range change.
  const body = doc.createElement("div");
  body.className = "hist-tlapse-body";
  body.dataset.testid = "tlapse-body";
  wrap.appendChild(body);

  // ── component state
  let photos = [];
  let position = 0;
  let playInterval = null;

  // Cached element refs from the most recent render — autoplay tick
  // uses these to avoid a fresh querySelector every 500ms.
  let imgEl = null;
  let captionEl = null;
  let sliderEl = null;
  let playBtnEl = null;

  function _photoUrl(photoId) {
    return `/api/grow/units/${unit.id}/photos/${photoId}`;
  }

  /** Update img src, alt and caption for the current `position`. Pure
   *  side-effect — assumes elements exist. */
  function _syncDom() {
    const p = photos[position];
    if (imgEl) {
      imgEl.src = _photoUrl(p.id);
      imgEl.alt = `Photo ${position + 1} of ${photos.length}`;
    }
    if (captionEl) {
      captionEl.textContent = p.taken_at;
    }
    if (sliderEl) {
      sliderEl.value = String(position);
    }
  }

  /** Build the body DOM for a non-empty photo list. */
  function _renderBody() {
    body.innerHTML = "";  // clear placeholder / previous render
    if (photos.length === 0) {
      const empty = doc.createElement("div");
      empty.className = "hist-tlapse-empty";
      empty.dataset.testid = "tlapse-empty";
      empty.textContent = "No photos in this range";
      body.appendChild(empty);
      imgEl = captionEl = sliderEl = playBtnEl = null;
      return;
    }

    // Hero image — clickable to open the lightbox with the full photos
    // list as nav context. The pointer cursor signals the affordance.
    imgEl = doc.createElement("img");
    imgEl.className = "hist-tlapse-img";
    imgEl.dataset.testid = "tlapse-img";
    imgEl.src = _photoUrl(photos[position].id);
    imgEl.alt = `Photo ${position + 1} of ${photos.length}`;
    imgEl.style.cursor = "pointer";
    imgEl.addEventListener("click", () => {
      const photoList = photos.map(p => ({
        id: p.id,
        taken_at: p.taken_at,
        url: _photoUrl(p.id),
      }));
      openLightbox({
        photos: photoList,
        currentIndex: position,
        unitId: unit.id,
        ownerDocument: doc,
      });
    });
    body.appendChild(imgEl);

    // Caption (taken_at)
    captionEl = doc.createElement("div");
    captionEl.className = "hist-tlapse-caption";
    captionEl.dataset.testid = "tlapse-caption";
    captionEl.textContent = photos[position].taken_at;
    body.appendChild(captionEl);

    // Controls row (play + slider)
    const controls = doc.createElement("div");
    controls.className = "hist-tlapse-controls";

    playBtnEl = doc.createElement("button");
    playBtnEl.type = "button";
    playBtnEl.dataset.testid = "tlapse-play";
    playBtnEl.textContent = playInterval !== null ? "⏸" : "▶";
    playBtnEl.addEventListener("click", () => {
      if (playInterval !== null) {
        _stopPlay();
      } else {
        _startPlay();
      }
    });
    controls.appendChild(playBtnEl);

    sliderEl = doc.createElement("input");
    sliderEl.type = "range";
    sliderEl.dataset.testid = "tlapse-slider";
    sliderEl.min = "0";
    sliderEl.max = String(photos.length - 1);
    sliderEl.value = String(position);
    sliderEl.className = "hist-tlapse-slider";
    sliderEl.addEventListener("input", (ev) => {
      position = Number(ev.target.value);
      // Don't stop autoplay on user scrub — they may want to rewind a
      // bit and then keep watching from there.
      _syncDom();
    });
    controls.appendChild(sliderEl);

    body.appendChild(controls);
  }

  function _startPlay() {
    if (playInterval !== null) return;
    if (photos.length === 0 || position >= photos.length - 1) {
      // Already at the end (or no photos) — nothing to play.
      return;
    }
    playInterval = setInterval(() => {
      if (position >= photos.length - 1) {
        // Reached the end — stop and flip the button label back to ▶.
        _stopPlay();
        return;
      }
      position += 1;
      _syncDom();
    }, PLAY_INTERVAL_MS);
    if (playBtnEl) playBtnEl.textContent = "⏸";
  }

  function _stopPlay() {
    if (playInterval !== null) {
      clearInterval(playInterval);
      playInterval = null;
    }
    if (playBtnEl) playBtnEl.textContent = "▶";
  }

  /** Fetch the photo list for `range` and re-render the body. */
  async function _loadAndRender(range) {
    _stopPlay();
    body.innerHTML = "";
    const placeholder = doc.createElement("div");
    placeholder.className = "hist-tlapse-loading";
    placeholder.textContent = "Loading…";
    body.appendChild(placeholder);

    let r;
    try {
      r = await fetch(`/api/grow/units/${unit.id}/photos?range=${range}`);
    } catch (exc) {
      body.innerHTML = "";
      const err = doc.createElement("div");
      err.className = "hist-tlapse-error";
      err.textContent = "Network error";
      body.appendChild(err);
      return;
    }
    if (!r.ok) {
      body.innerHTML = "";
      const err = doc.createElement("div");
      err.className = "hist-tlapse-error";
      err.textContent = "Failed to load photos";
      body.appendChild(err);
      return;
    }
    photos = await r.json();
    position = Math.max(0, photos.length - 1);  // start at the latest
    _renderBody();
  }

  // ── range button click handler — event-delegated on the selector
  selector.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-range]");
    if (!btn || btn.dataset.range === currentRange) return;
    currentRange = btn.dataset.range;
    selector.querySelectorAll("button").forEach((b) => {
      b.className = b.dataset.range === currentRange ? "active" : "";
    });
    _loadAndRender(currentRange);
  });

  // Kick off initial load. Fire-and-forget — the panel mounts immediately
  // and the body fills in when the fetch resolves.
  _loadAndRender(currentRange);

  return wrap;
}

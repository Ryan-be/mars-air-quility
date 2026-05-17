/**
 * Photo timelapse scrubber for the History tab — Task 4 of the
 * History-tab plan.
 *
 * Range selector: 24h / 7d / 30d / 90d / all. Default 7d (was 24h:
 * for plant-growth viewing 24h almost never spans enough captures to
 * see meaningful change, so a 7d window is a more useful starting
 * point).
 *
 * Backend contract:
 *   GET /api/grow/units/<id>/photos?range=<r>
 *     -> [{id, taken_at, telemetry_id}, …] (metadata only, no JPEG bytes)
 *   GET /api/grow/units/<id>/photos/<photo_id>
 *     -> JPEG bytes — the browser fetches these on demand when img.src
 *        is set, so we never bulk-preload.
 *
 * The scrubber has one position per photo in the range. Default position
 * is the leftmost (earliest) so the panel opens on the FIRST snapshot —
 * for plant-growth viewing, you almost always want to watch growth play
 * forward from the start (and the skip-to-end button is one click away
 * if you want the latest). Earlier behaviour started at the rightmost
 * (latest); that was great for "what does it look like NOW" but useless
 * for the timelapse-playback use case the controls are designed for.
 *
 * Playback controls (left to right):
 *   ⏮  skip-to-start (position = 0)
 *   ▶/⏸ play / pause autoplay
 *   ⏭  skip-to-end (position = photos.length - 1)
 *   speed dropdown (1× / 2× / 4× / 8×) — multiplies the autoplay tick
 *   Loop checkbox — when checked, autoplay wraps end→start (default off,
 *                   matching pre-controls behaviour)
 *
 * Autoplay speed: setInterval at BASE_INTERVAL_MS / speed. 1× = 500ms
 * (one photo / half second — readable), 8× = 62.5ms (a smooth flip).
 * When the user changes speed mid-play we restart the interval so the
 * change takes effect immediately.
 *
 * On range change: stop autoplay, refetch the photo list, reset position
 * to the earliest. The scrubber and hero img get re-rendered because
 * `slider.max` changes.
 *
 * Why we don't preload: a 90d range can be hundreds of photos at ~200KB
 * each — that's tens of MB the user may never see. The browser caches
 * the by-id endpoint anyway, so re-scrubbing is fast after the first
 * pass. Hero img + scrubber view both request `?size=thumb` (320px ~30KB
 * variants) so even a fast scrub doesn't blow the network — the user
 * clicks through to the lightbox if they want full-res.
 */

import { openLightbox } from "./photo-lightbox.mjs";

const RANGES = ["24h", "7d", "30d", "90d", "all"];

const BASE_INTERVAL_MS = 500;

const SPEEDS = [1, 2, 4, 8];


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
  let currentRange = "7d";
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
  // Default 1× (500ms tick). Survives range changes so the user's
  // chosen speed isn't reset when they switch from 24h to 7d.
  let speed = 1;
  // Loop wraps end → start during autoplay. Off by default.
  let loop = false;

  // Cached element refs from the most recent render — autoplay tick
  // uses these to avoid a fresh querySelector every tick.
  let imgEl = null;
  let captionEl = null;
  let sliderEl = null;
  let playBtnEl = null;

  function _photoUrl(photoId) {
    // The hero img + scrubber view both consume the server-side
    // ~30KB thumbnail. Bug 5: previously we were fetching the full
    // ~2MB original on every scrubber tick — fine for one photo, but
    // an 8× autoplay through a 90d range would chew through hundreds
    // of MB. The lightbox handoff in _renderBody constructs its own
    // full-res URL list, so this thumb URL never leaks into the
    // "give me the big version" affordance.
    return `/api/grow/units/${unit.id}/photos/${photoId}?size=thumb`;
  }

  /** Build the full-res URL list passed to the lightbox. Hero img +
   *  scrubber both use thumbs (cheap to scrub through); the lightbox
   *  is the "give me the big version" affordance and intentionally
   *  fetches the originals. */
  function _fullResUrl(photoId) {
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
      // Lightbox gets the FULL-RES URLs — the user clicked "show me
      // the big version", so a thumb here would defeat the point.
      const photoList = photos.map(p => ({
        id: p.id,
        taken_at: p.taken_at,
        url: _fullResUrl(p.id),
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

    // Controls row: ⏮ play/pause ⏭ speed loop slider
    const controls = doc.createElement("div");
    controls.className = "hist-tlapse-controls";

    const skipStartBtn = doc.createElement("button");
    skipStartBtn.type = "button";
    skipStartBtn.dataset.testid = "tlapse-skip-start";
    skipStartBtn.textContent = "⏮";
    skipStartBtn.setAttribute("aria-label", "Skip to start");
    skipStartBtn.addEventListener("click", () => {
      position = 0;
      _syncDom();
    });
    controls.appendChild(skipStartBtn);

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

    const skipEndBtn = doc.createElement("button");
    skipEndBtn.type = "button";
    skipEndBtn.dataset.testid = "tlapse-skip-end";
    skipEndBtn.textContent = "⏭";
    skipEndBtn.setAttribute("aria-label", "Skip to end");
    skipEndBtn.addEventListener("click", () => {
      position = photos.length - 1;
      _syncDom();
    });
    controls.appendChild(skipEndBtn);

    // Speed dropdown (1× / 2× / 4× / 8×)
    const speedSel = doc.createElement("select");
    speedSel.dataset.testid = "tlapse-speed";
    speedSel.setAttribute("aria-label", "Playback speed");
    for (const s of SPEEDS) {
      const opt = doc.createElement("option");
      opt.value = String(s);
      opt.textContent = `${s}×`;
      if (s === speed) opt.selected = true;
      speedSel.appendChild(opt);
    }
    speedSel.addEventListener("change", (ev) => {
      speed = Number(ev.target.value);
      // If we're mid-play, restart the interval so the change takes
      // effect immediately rather than waiting for the next "play" toggle.
      if (playInterval !== null) {
        _stopPlay();
        _startPlay();
      }
    });
    controls.appendChild(speedSel);

    // Loop checkbox
    const loopWrap = doc.createElement("label");
    loopWrap.className = "hist-tlapse-loop";
    const loopCb = doc.createElement("input");
    loopCb.type = "checkbox";
    loopCb.dataset.testid = "tlapse-loop";
    loopCb.checked = loop;
    loopCb.addEventListener("change", (ev) => {
      loop = ev.target.checked;
    });
    loopWrap.appendChild(loopCb);
    const loopText = doc.createTextNode(" Loop");
    loopWrap.appendChild(loopText);
    controls.appendChild(loopWrap);

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

  function _intervalMs() {
    return BASE_INTERVAL_MS / speed;
  }

  function _startPlay() {
    if (playInterval !== null) return;
    if (photos.length === 0) return;
    // If we're at the end and not looping, there's nowhere to go.
    if (!loop && position >= photos.length - 1) return;
    playInterval = setInterval(() => {
      if (position >= photos.length - 1) {
        if (loop) {
          // Wrap to the start. Keep playing — the user opted in.
          position = 0;
          _syncDom();
          return;
        }
        // No loop: stop and flip the button label back to ▶.
        _stopPlay();
        return;
      }
      position += 1;
      _syncDom();
    }, _intervalMs());
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
    // Start at the FIRST photo (earliest in time). For plant-growth
    // viewing this is the natural starting point — autoplay then runs
    // forward through growth. Skip-to-end is one click away if the
    // user wants the latest snapshot.
    position = 0;
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

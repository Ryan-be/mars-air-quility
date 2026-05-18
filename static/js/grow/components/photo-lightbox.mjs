/**
 * Modal lightbox for photo viewing — Task 3 of the Phase 2 finisher
 * plan.
 *
 * Two render modes:
 *   - Single photo:  { photoUrl } → photo + close button only.
 *   - Nav context:   { photos: [{id, taken_at, url}], currentIndex, unitId }
 *                    → photo + close + prev/next + caption.
 *
 * Dismiss surface (any of):
 *   - ESC key
 *   - Click on the dim backdrop (the overlay element itself)
 *   - Click on the close button
 *
 * Keyboard navigation (only when photos[] is provided):
 *   - ArrowLeft  → prev (clamped at index 0)
 *   - ArrowRight → next (clamped at last index)
 *
 * The prev/next buttons mirror the keyboard behavior and are
 * `disabled` at the list endpoints so the visual state matches.
 *
 * Pure frontend — uses the photo URLs the caller provides. The
 * timelapse passes the same /photos/<id> endpoint URLs it already
 * uses for the hero img; the Live tab passes /photo/latest with a
 * cache-bust ts. No backend changes.
 *
 * Implementation notes:
 *   - The keydown listener attaches to `ownerDocument` (the JSDOM
 *     document in tests, the real document in production). Attaching
 *     to the overlay would only fire when the overlay had focus,
 *     which it doesn't by default, so ESC wouldn't work.
 *   - Photo click stops propagation so it doesn't bubble up to the
 *     overlay's click handler (which would dismiss).
 *   - The handler is removed on close — otherwise repeated open/close
 *     would leak a listener per cycle.
 *   - No focus trap. If users tab away they can still ESC; keeping
 *     things small.
 */

/**
 * Open a lightbox modal. Pass either `photoUrl` for single-photo mode
 * or `photos` + `currentIndex` for nav mode.
 *
 * @param {object} opts
 * @param {string} [opts.photoUrl]    Single-photo mode: full URL.
 * @param {Array}  [opts.photos]      Nav mode: list of {id, taken_at, url}.
 * @param {number} [opts.currentIndex] Nav mode: starting index (default 0).
 * @param {number|string} [opts.unitId] Nav mode: unit id (currently unused
 *                                      directly; photos already carry url).
 * @param {Document} [opts.ownerDocument] Document to mount into. Defaults
 *                                        to the global document.
 * @returns {{ close: () => void }}
 */
export function openLightbox(opts) {
  const doc = opts.ownerDocument || document;
  const hasNav = Array.isArray(opts.photos) && opts.photos.length > 0;
  const photos = hasNav ? opts.photos : null;
  let index = hasNav ? (opts.currentIndex ?? 0) : 0;

  // ── overlay (the dim backdrop)
  const overlay = doc.createElement("div");
  overlay.className = "lightbox-overlay";
  overlay.dataset.testid = "lightbox-overlay";

  // ── content wrapper (centred photo + chrome)
  const content = doc.createElement("div");
  content.className = "lightbox-content";
  overlay.appendChild(content);

  // ── close button (top-right)
  const closeBtn = doc.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "lightbox-close";
  closeBtn.dataset.testid = "lightbox-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.textContent = "×";
  content.appendChild(closeBtn);

  // ── photo
  const img = doc.createElement("img");
  img.className = "lightbox-img";
  img.dataset.testid = "lightbox-img";
  img.src = hasNav ? photos[index].url : opts.photoUrl;
  img.alt = hasNav ? `Photo ${index + 1} of ${photos.length}` : "Photo";
  content.appendChild(img);

  // ── caption (nav mode only — single photo has no useful caption)
  let captionEl = null;
  if (hasNav) {
    captionEl = doc.createElement("div");
    captionEl.className = "lightbox-caption";
    captionEl.dataset.testid = "lightbox-caption";
    captionEl.textContent = photos[index].taken_at || "";
    content.appendChild(captionEl);
  }

  // ── prev/next buttons (nav mode only)
  let prevBtn = null;
  let nextBtn = null;
  if (hasNav) {
    prevBtn = doc.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "lightbox-prev";
    prevBtn.dataset.testid = "lightbox-prev";
    prevBtn.setAttribute("aria-label", "Previous photo");
    prevBtn.textContent = "‹";
    content.appendChild(prevBtn);

    nextBtn = doc.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "lightbox-next";
    nextBtn.dataset.testid = "lightbox-next";
    nextBtn.setAttribute("aria-label", "Next photo");
    nextBtn.textContent = "›";
    content.appendChild(nextBtn);
  }

  /** Refresh the image src + caption + button disabled-state from `index`. */
  function _syncDom() {
    if (!hasNav) return;
    const p = photos[index];
    img.src = p.url;
    img.alt = `Photo ${index + 1} of ${photos.length}`;
    if (captionEl) captionEl.textContent = p.taken_at || "";
    if (prevBtn) prevBtn.disabled = index <= 0;
    if (nextBtn) nextBtn.disabled = index >= photos.length - 1;
  }

  function _goPrev() {
    if (!hasNav || index <= 0) return;
    index -= 1;
    _syncDom();
  }

  function _goNext() {
    if (!hasNav || index >= photos.length - 1) return;
    index += 1;
    _syncDom();
  }

  // Initial endpoint disabled-state
  _syncDom();

  // ── keyboard handler (registered on the document so it fires regardless
  //    of focus; removed on close to avoid leaks)
  function _onKey(ev) {
    if (ev.key === "Escape") {
      close();
    } else if (hasNav && ev.key === "ArrowLeft") {
      _goPrev();
    } else if (hasNav && ev.key === "ArrowRight") {
      _goNext();
    }
  }
  doc.addEventListener("keydown", _onKey);

  // ── click handlers
  // Close on backdrop click — but only when the click target is the overlay
  // itself, not bubbled from a child. Using event.target check keeps this
  // robust: if the user clicks the photo, target === img, not overlay,
  // so we don't close.
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) {
      close();
    }
  });

  // The image must NOT bubble its click up to the overlay (which would
  // close), even though target-check above already handles this — being
  // explicit costs nothing and protects against future overlay-target
  // refactors.
  img.addEventListener("click", (ev) => {
    ev.stopPropagation();
  });

  closeBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();  // don't double-fire via overlay click
    close();
  });

  if (prevBtn) {
    prevBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _goPrev();
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _goNext();
    });
  }

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    doc.removeEventListener("keydown", _onKey);
    overlay.remove();
  }

  doc.body.appendChild(overlay);
  return { close };
}

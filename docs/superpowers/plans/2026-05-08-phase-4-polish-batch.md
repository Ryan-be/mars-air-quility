# Phase 4 polish batch — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement each item task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Items are independent — execute in the order below for least dependency risk.

**Goal:** Ship the seven polish items from the Phase 4 backlog that aren't the local-on-Pi UI (#6 is being designed separately as a larger SDUI architecture refactor).

**Architecture:** Each item is independently scoped. Some are docs, some are server code, some are firmware/install. Test with the project's existing test conventions (`pytest tests/grow_server tests/grow_unit tests/contracts` for Python, `node --test tests/js/*.mjs` for JS).

**Tech Stack:** Existing — Flask + gunicorn server, Pi Zero W firmware, JS modules + JSDOM tests.

**Items in scope** (numbering matches the Phase 4 backlog):
1. Server-side photo thumbnail endpoint
2. USB SSD boot guide
3. Custom Pi SD-card .img for one-step provisioning
4. Public PyPI release of `mlss-grow`
5. Mobile-optimised fleet view
7. Plant journal / annotations on the History tab
8. Time-lapse video generation

**Items NOT in scope (covered elsewhere):**
- #6 Local read-only status UI on the grow unit — being redesigned as a full SDUI architecture refactor in a separate plan

**Recommended execution order** (least-dependency first):

| Order | Item | Reason |
|---|---|---|
| 1 | #2 USB SSD boot guide | Pure docs, immediate operator value, blocks nothing |
| 2 | #1 Photo thumbnails | Server feature, used by #5 + #8 |
| 3 | #5 Mobile fleet view | CSS-only, low risk, benefits from #1 |
| 4 | #4 PyPI release | Release infra, enables #3 |
| 5 | #7 Plant journal | Self-contained feature |
| 6 | #8 Timelapse video | Self-contained feature, server-side ffmpeg |
| 7 | #3 Pi SD-card image | Largest scope, depends on #4 |

---

## Item #2: USB SSD boot guide (start here)

**Files:**
- Create: `docs/USB_SSD_BOOT_GUIDE.md`

**Why first:** Pure docs, no code risk. Captures real operational knowledge from the deployment-night SD failure.

**Steps:**

- [ ] **2.1: Write the doc**

  Cover:
  - Why migrate (SD wear: sqlite WAL on the MLSS server burns through cycles in months)
  - When to migrate (signs: I/O errors in dmesg, fs corruption on boot, slow systemctl restart)
  - Hardware shopping list (USB 3.0 SSD enclosure, recommended drives, cable considerations)
  - Step 1: flash blank SSD via `rpi-imager` to same OS as current SD
  - Step 2: `raspi-config` → Advanced → Boot Order → USB Boot first
  - Step 3: live migration recipe — `rsync -aAXv --exclude=/dev --exclude=/proc --exclude=/sys --exclude=/tmp --exclude=/run / /mnt/ssd/` then `sudo nano /mnt/ssd/etc/fstab` to update partition UUIDs (or use PARTUUID)
  - Step 4: shutdown, swap drive, boot
  - Validation: `df -h` shows root on `/dev/sda*` not `/dev/mmcblk*`; `dmesg | grep -i usb` clean
  - Rollback: keep the SD card; if SSD fails, swap back
  - Brief note: Pi Zero W grow units (2 photos/min, occasional sensor writes) are write-light enough that SD is probably fine. Don't bother migrating those unless one shows wear.

- [ ] **2.2: Cross-link from existing docs**

  Add a one-liner pointer in `readme.md` near the deploy section: "Server running on SD card? See [`docs/USB_SSD_BOOT_GUIDE.md`](docs/USB_SSD_BOOT_GUIDE.md) — recommended for 24/7 stability."

- [ ] **2.3: Commit**

  Single commit covering both files.

---

## Item #1: Server-side photo thumbnail endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_grow_photos.py`
- Modify: `mlss_monitor/routes/api_grow_units.py` (clear-photos endpoint should also clear thumbnails)
- Create: `tests/grow_server/test_grow_photo_thumbnails.py`
- Modify: `static/js/grow/components/grow-card.mjs` (use `?size=thumb`)
- Modify: `tests/js/test_grow_card.mjs` (assert URL has size param)

**Why second:** Fleet view performance win. Photos go from ~2MB to ~20-50KB per card.

**Steps:**

- [ ] **1.1: Add thumbnail generation helper**

  In `mlss_monitor/grow/photo_storage.py`, add:

  ```python
  THUMB_DIR_NAME = "grow_thumbnails"
  THUMB_WIDTHS = (320,)  # only one size for now; extensible

  def _resolve_thumbnails_dir() -> str:
      """Mirrors _resolve_images_dir but for thumbnail cache."""
      # ... use the same project-relative default + override pattern

  def get_or_create_thumbnail(photo_relpath: str, width: int) -> str:
      """Return absolute path to a cached thumbnail at the given width.
      Generate on first request via Pillow; cache to disk.
      Idempotent — repeated calls re-use the cached file.
      """
      if width not in THUMB_WIDTHS:
          raise ValueError(f"unsupported width {width}; allowed: {THUMB_WIDTHS}")
      # ... implementation
  ```

- [ ] **1.2: Add the endpoints**

  Two new routes (both serve the cached thumbnail, generate on miss):
  ```python
  GET /api/grow/units/<id>/photo/latest?size=thumb
  GET /api/grow/units/<id>/photos/<photo_id>?size=thumb
  ```
  Reuse the existing endpoint logic; if `?size=thumb` is set, route to thumbnail dir. `Cache-Control: public, max-age=31536000, immutable` (same as full /photos/<id>).

- [ ] **1.3: Wire thumbnail invalidation**

  In `api_grow_units.py::clear_photos`, also delete `data/grow_thumbnails/<unit>/` directory tree (best-effort; same FileNotFoundError-tolerant pattern).

- [ ] **1.4: Add Pillow to dependencies**

  Pillow may already be installed (used by camera). Verify it's in `pyproject.toml` server-side; add if missing.

- [ ] **1.5: Tests**

  ```python
  def test_thumbnail_generated_on_first_request(setup):
      # Hit /photo/latest?size=thumb, assert 200 + body is JPEG + ~320px wide

  def test_thumbnail_cached_on_second_request(setup):
      # Two GETs; assert second is faster (mtime not bumped)

  def test_thumbnail_cache_cleared_on_clear_photos(photos_client):
      # Seed a thumbnail, call DELETE /photos, assert cache dir empty

  def test_thumbnail_unknown_size_400(setup):
      # ?size=large → 400
  ```

- [ ] **1.6: Update `grow-card.mjs` to use ?size=thumb**

  In the photoUrl construction, append `?size=thumb` to the URL.

- [ ] **1.7: Run all tests + commit**

  ```bash
  python -m pytest tests/grow_server/test_grow_photo_thumbnails.py -x
  node --test tests/js/test_grow_card.mjs
  ```

---

## Item #5: Mobile-optimised fleet view

**Files:**
- Modify: `static/css/grow.css`
- Test manually with Chrome DevTools mobile sim (no automated test for visual layout)

**Why third:** CSS-only, low risk, big perceived improvement on phones. Benefits from thumbnails (#1) being shipped already.

**Steps:**

- [ ] **5.1: Audit existing breakpoints**

  Grep `static/css/grow.css` for `@media`. There's already `@media (max-width: 540px)` for `.cfg-row`. Confirm.

- [ ] **5.2: Make `.grow-grid` responsive**

  Current: `grid-template-columns: repeat(auto-fit, minmax(280px, 360px))`. On phones (~ 375px) this gives 1 column with ~340px-wide cards. Verify this works; add `padding: var(--grow-space-3)` instead of `var(--grow-space-5)` on narrow viewports for more card breathing room.

- [ ] **5.3: Make `.grow-pageheader` flex-wrap properly**

  On narrow viewports, the summary + Add Unit button should stack vertically. Add `flex-wrap: wrap` (already there) + adjust `gap` and `flex-direction` in the media query.

- [ ] **5.4: Touch-friendly button sizes**

  All buttons should have `min-height: 44px` on touch devices (iOS HIG). Apply via `@media (hover: none) and (pointer: coarse)` to `.px-btn`, `.gu-btn`, `.du-act-btn`.

- [ ] **5.5: Make `.fleet-filter-row` chips wrap and scroll on narrow viewports**

  Currently they wrap; that's probably fine. Verify on a 375px viewport.

- [ ] **5.6: Make `.du-tabs` scrollable horizontally on narrow viewports**

  Currently 4 tabs at ~120px each = 480px. On a 375px screen, they'll cram. Add `overflow-x: auto; -webkit-overflow-scrolling: touch` + `scrollbar-width: none` for a clean horizontal scroll.

- [ ] **5.7: Test in Chrome DevTools**

  Pick: iPhone SE (375x667), iPhone 14 Pro Max (430x932), Pixel 7 (412x915). Verify fleet view + unit detail render acceptably.

- [ ] **5.8: Commit**

  Single commit. CSS changes only.

---

## Item #4: Public PyPI release of `mlss-grow`

**Files:**
- Modify: `grow_unit/pyproject.toml` (classifiers, keywords, license)
- Create: `grow_unit/LICENSE` (if not already present)
- Modify: `grow_unit/README.md` (add a "Install from PyPI" section)
- Create: `.github/workflows/publish-mlss-grow.yml` (auto-publish on tag)
- Create: `docs/RELEASE_PROCESS.md`

**Why fourth:** Enables item #3 (the SD-card .img can `pip install mlss-grow` from PyPI rather than fetching wheels from MLSS).

**Steps:**

- [ ] **4.1: Verify `grow_unit/pyproject.toml` is PyPI-publishable**

  Required fields: `name`, `version`, `description`, `authors`, `readme`, `license`, `classifiers`, `keywords`. Add the following classifiers:
  - `"Development Status :: 4 - Beta"`
  - `"Operating System :: POSIX :: Linux"`
  - `"Programming Language :: Python :: 3.11"`
  - `"Programming Language :: Python :: 3.13"`
  - `"Topic :: Home Automation"`
  - `"Topic :: System :: Hardware"`
  - `"License :: OSI Approved :: MIT License"`

- [ ] **4.2: Add a LICENSE file** (assume MIT unless you want something else)

- [ ] **4.3: Strip the path-dep from the published wheel**

  Currently the wheel embeds `mlss-contracts @ file://...`. For PyPI, replace with a versioned dep on `mlss-contracts>=0.1.0`. Either:
  - Publish `mlss-contracts` to PyPI first (recommended; cleaner)
  - OR vendor mlss-contracts into mlss-grow at build time

  Ship the cleaner option: publish `mlss-contracts` to PyPI as a separate tiny package.

- [ ] **4.4: Set up the GitHub Actions workflow**

  Trigger: on tag matching `mlss-grow-v*` (e.g. `mlss-grow-v0.1.0`). Steps:
  1. Checkout
  2. Build wheel via `poetry build`
  3. `pip install twine`
  4. `twine upload dist/* -u __token__ -p ${{ secrets.PYPI_API_TOKEN }}`
  5. Upload wheel as a GitHub release asset too (for `install.sh` fallback)

- [ ] **4.5: Set up the same for mlss-contracts**

  Tag pattern: `mlss-contracts-v*`. Same workflow.

- [ ] **4.6: Document the release process**

  `docs/RELEASE_PROCESS.md`:
  - When to bump versions (semver)
  - Tag command: `git tag mlss-grow-v0.1.0 && git push --tags`
  - What the workflow does
  - Manual fallback if the workflow fails
  - How to test on a fresh Pi: `pip install mlss-grow` (no MLSS dependency)

- [ ] **4.7: Cut the first release**

  Tag both `mlss-contracts-v0.1.0` and `mlss-grow-v0.1.0`. Verify both land on PyPI. Verify install on a fresh Pi or a test Linux box: `pip install mlss-grow` works.

- [ ] **4.8: Commit**

  Workflow + docs in one commit.

---

## Item #7: Plant journal / annotations on the History tab

**Files:**
- Modify: `database/grow_schema.py` (new table)
- Modify: `database/init_db.py` (migration)
- Create: `mlss_monitor/routes/api_grow_journal.py`
- Create: `static/js/grow/components/journal-editor.mjs`
- Modify: `static/js/grow/components/history-panel.mjs` (mount journal alongside chart + timelapse)
- Modify: `static/js/grow/components/moisture-history-chart.mjs` (overlay annotation markers)
- Modify: `static/js/grow/components/photo-timelapse.mjs` (annotation markers on the scrubber)
- Create: `tests/grow_server/test_api_grow_journal.py`
- Create: `tests/js/test_journal_editor.mjs`

**Why fifth:** Substantial feature. Operator notes pinned to timestamps that overlay on existing time-axis charts.

**Steps:**

- [ ] **7.1: Schema design**

  ```sql
  CREATE TABLE grow_journal_entries (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id INTEGER NOT NULL REFERENCES grow_units(id),
      timestamp_utc DATETIME NOT NULL,  -- the ts the entry pertains to
      author TEXT NOT NULL,
      body TEXT NOT NULL,
      created_at DATETIME NOT NULL,
      updated_at DATETIME
  );
  CREATE INDEX idx_grow_journal_unit_ts ON grow_journal_entries(unit_id, timestamp_utc DESC);
  ```

  Also add ALTER TABLE migration in `init_db.py`.

- [ ] **7.2: API endpoints**

  ```
  GET    /api/grow/units/<id>/journal?range=24h  → list (range filter same vocab as /history)
  POST   /api/grow/units/<id>/journal             → create. Body: {timestamp_utc, body}. Author = session user.
  PATCH  /api/grow/units/<id>/journal/<entry_id>  → edit body. Only the original author OR admin.
  DELETE /api/grow/units/<id>/journal/<entry_id>  → delete. Same auth.
  ```

  RBAC: `GET` is viewer+. `POST/PATCH/DELETE` is controller+.

- [ ] **7.3: UI: journal editor component**

  `static/js/grow/components/journal-editor.mjs`:
  - Lists existing entries (most-recent first)
  - "Add note" button → text area + datetime picker (defaults to now)
  - Each entry: timestamp, body, author, edit/delete buttons (gated by author/role)
  - Calls the new API
  - Emits `journal-changed` event so chart + timelapse can re-render markers

- [ ] **7.4: Chart annotation markers**

  In `moisture-history-chart.mjs`, after rendering the SVG, overlay vertical line markers at each entry's timestamp_utc position. Hover shows the body in a tooltip.

- [ ] **7.5: Timelapse annotation markers**

  Similar — small dots above the scrubber slider at each entry's timestamp.

- [ ] **7.6: History tab integration**

  In `history-panel.mjs`, mount the journal editor below the photo timelapse. Wire the `journal-changed` event to re-fetch the journal in chart + timelapse.

- [ ] **7.7: Tests**

  Backend: CRUD + RBAC + range filter (~12 tests)
  Frontend: render with seeded entries, add/edit/delete flows, marker overlay (~8 tests)

- [ ] **7.8: Commit**

  Three logical commits: schema + API, UI editor, chart/timelapse marker overlay.

---

## Item #8: Time-lapse video generation

**Files:**
- Create: `mlss_monitor/grow/timelapse_jobs.py`
- Create: `mlss_monitor/routes/api_grow_timelapse.py`
- Modify: `database/grow_schema.py` (new table for jobs)
- Create: `static/js/grow/components/timelapse-generator.mjs`
- Modify: `static/js/grow/components/history-panel.mjs`
- Create: `tests/grow_server/test_api_grow_timelapse.py`
- Create: `tests/js/test_timelapse_generator.mjs`

**Why sixth:** Server-side ffmpeg job. Async — operator submits a request, gets back a job ID, polls for completion.

**Steps:**

- [ ] **8.1: Schema for the job table**

  ```sql
  CREATE TABLE grow_timelapse_jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_id INTEGER NOT NULL REFERENCES grow_units(id),
      requested_by TEXT NOT NULL,
      requested_at DATETIME NOT NULL,
      range TEXT NOT NULL,  -- '24h' / '7d' / '30d' / '90d' / 'all'
      fps INTEGER NOT NULL DEFAULT 10,
      status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed')),
      output_path TEXT,  -- relative to data/timelapses/
      error_message TEXT,
      started_at DATETIME,
      completed_at DATETIME
  );
  ```

- [ ] **8.2: Job runner**

  Background worker that pulls `status=queued` rows, builds the ffmpeg command from photos in the unit's grow_photos table, writes MP4 to `data/timelapses/<unit>/<job_id>.mp4`, updates row.

  Use `subprocess.run` for ffmpeg. Command:
  ```
  ffmpeg -framerate 10 -i 'photos/%04d.jpg' -c:v libx264 -pix_fmt yuv420p output.mp4
  ```
  Need to symlink/copy photos in date order to a sequential temp dir first (ffmpeg's pattern matcher is finicky with non-sequential filenames).

  Scheduling: simple — invoke worker on a 30s timer. For Phase 4 polish, no Celery/RQ; just an in-process timer. Document this as a known limitation.

- [ ] **8.3: API endpoints**

  ```
  POST /api/grow/units/<id>/timelapse        → create job. Body: {range, fps?}. Returns job_id.
  GET  /api/grow/units/<id>/timelapse        → list jobs for the unit.
  GET  /api/grow/timelapse/<job_id>          → job status + output URL when complete.
  GET  /api/grow/timelapse/<job_id>/video    → serve the MP4 (Cache-Control: long).
  ```

  RBAC: viewer+ for GET, controller+ for POST.

- [ ] **8.4: UI: timelapse generator component**

  `static/js/grow/components/timelapse-generator.mjs`:
  - Range selector (24h / 7d / 30d / 90d / all)
  - FPS selector (5 / 10 / 24)
  - "Generate" button
  - Polls job status every 2s
  - Inline video player when complete
  - Download link

- [ ] **8.5: History tab integration**

  Mount the timelapse generator below the photo scrubber.

- [ ] **8.6: ffmpeg dependency**

  Add to README server-install section: `apt install ffmpeg`.
  Detect at startup; log a clear warning if missing; the endpoint returns 503 with a clear error.

- [ ] **8.7: Tests**

  Backend: job creation, status polling, ffmpeg-missing fallback, RBAC (~10 tests).
  Frontend: render, generate flow, poll, video player (~6 tests).

- [ ] **8.8: Commit**

  Two logical commits: backend job runner + API, then frontend.

---

## Item #3: Custom Pi SD-card .img for one-step provisioning

**Files:**
- Create: `scripts/build_pi_image.sh`
- Create: `scripts/firstboot.sh`
- Create: `docs/PI_IMAGE_BUILD.md`
- Modify: `grow_unit/install.sh` (likely simplified or removed for image-flashed units)

**Why last:** Largest scope. Depends on #4 (PyPI) being live so the image can `pip install mlss-grow` cleanly.

**Steps:**

- [ ] **3.1: Choose base image + customisation tool**

  Options:
  - `pi-gen` (official Raspberry Pi tool — most thorough, slowest build)
  - `rpi-image-gen` (Yocto-style, complex)
  - Manual: `dd` Pi OS Lite, mount, chroot, customise, repack

  Recommend `pi-gen`. Has a `stage` system; we add a stage `stage-mlss-grow` that installs the firmware.

- [ ] **3.2: Write `scripts/build_pi_image.sh`**

  Wrapper that:
  1. Clones `pi-gen` if not present
  2. Drops in `stage-mlss-grow/` with package list + post-install scripts
  3. Runs `./build.sh`
  4. Output: `mlss-pi-os-<version>.img.xz` in `dist/`

- [ ] **3.3: stage-mlss-grow contents**

  - Pre-install: `python3-pip`, `python3-picamera2`, `i2c-tools`, mlss-grow deps
  - Run-as-root post-install: `pip install mlss-grow` (from PyPI, item #4)
  - Drop systemd unit into `/etc/systemd/system/mlss-grow.service` (don't enable; firstboot enables it)
  - Drop `/boot/mlss-grow.yaml.template` (operator copies + edits)
  - Drop `firstboot.sh` to `/usr/local/sbin/`, hooked to `rc.local` for one-time run

- [ ] **3.4: Write `firstboot.sh`**

  Runs once on first boot:
  1. If `/boot/mlss-grow.yaml` exists, proceed
  2. If not, print: "Drop your `mlss-grow.yaml` onto the boot partition then reboot"
  3. Once yaml exists: enable I2C (`raspi-config nonint do_i2c 0`), enable + start `mlss-grow.service`, mark itself done

- [ ] **3.5: Test the image**

  - Build the .img.xz
  - Flash to a real SD card
  - Boot a Pi Zero W
  - Verify: connects to MLSS, enrols, starts emitting telemetry

- [ ] **3.6: Document**

  `docs/PI_IMAGE_BUILD.md`:
  - How to build (`bash scripts/build_pi_image.sh`)
  - Where the output lands
  - How to publish (GitHub Releases, attach the .img.xz)
  - How operators flash + first-boot

- [ ] **3.7: Cut the first image release**

  Tag `pi-image-v0.1.0` → workflow uploads to GitHub Releases.

- [ ] **3.8: Commit**

  All artifacts in one commit per logical sub-task (build script, firstboot, doc).

---

## Cross-cutting acceptance criteria

For each item, before marking complete:
- [ ] All tests pass: `python -m pytest tests/grow_server tests/grow_unit tests/contracts -q && node --test tests/js/*.mjs`
- [ ] Pushed to `feature/plant-grow-units`
- [ ] If the item adds a user-facing feature, also update `docs/Bugs_Improvements_and_Roadmap.md` to remove the entry from Phase 4 polish

## Done criteria for this batch

- [ ] All 7 items merged to `feature/plant-grow-units`
- [ ] PyPI has `mlss-contracts` + `mlss-grow` published
- [ ] GitHub Releases has at least one Pi image
- [ ] All tests pass
- [ ] Phase 4 polish backlog has only item #6 remaining (which is being designed separately)

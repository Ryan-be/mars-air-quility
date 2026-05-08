# 🛠️ Bugs, Improvements & Learning Roadmap

This section tracks known issues, UX limitations, and planned enhancements to the MLSS Monitor system, particularly around inference accuracy, visualisation, and adaptive learning.

---

## 🧠 Feature: Event Tagging & Learning System

### Summary

The current attribution system (source fingerprints) is heuristic-based and occasionally misclassifies events. Introduce a **user-driven tagging system** to label events with their true source and enable **incremental learning** over time.

---

### Goals

* Allow users to tag:

  * Inference events (primary)
  * Raw time ranges (future extension)
* Persist tags in the database
* Use tagged data to:

  * Improve attribution accuracy
  * Train lightweight online ML models

---

### Proposed Design

#### 1. Data Model

```sql
CREATE TABLE event_tags (
    id INTEGER PRIMARY KEY,
    inference_id INTEGER,
    tag TEXT,
    confidence REAL DEFAULT 1.0,
    created_at DATETIME,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
);
```

Optional future:

* Add `sensor_data_start_id`, `sensor_data_end_id` for manual window tagging

---

#### 2. UI Integration

* Add to inference card:

  * Dropdown: **“What caused this?”**
  * Options + free text
* Display:

  * User tag
  * Model attribution (side-by-side)

---

#### 3. Learning Strategy

##### Phase 1 — Assisted Attribution

* Use tags to:

  * Evaluate fingerprint accuracy
  * Build confusion matrix
  * Adjust heuristic weights

---

##### Phase 2 — Online Supervised Learning

Train a classifier:

* Input: `FeatureVector`
* Output: `source_tag`

Suggested models:

* `HoeffdingTreeClassifier`
* `LogisticRegression`

---

##### Phase 3 — Hybrid Attribution

Combine heuristic + ML:

```
final_score = 0.6 * fingerprint + 0.4 * ML
```

---

### Challenges

* Cold start (no labels)
* Label quality (user input errors)
* Class imbalance (many “normal” cases)

---

### Notes

Online learning is a strong fit:

* No retraining cycles needed
* Updates per event
* Works naturally with streaming data

---

## 🐛 Bug: Inference Card Plot UX & Rendering Issues

### Symptoms

* Plot appears cut off
* Low usefulness / unclear meaning
* Poor scaling and layout
* Minimal or confusing data shown

---

### Likely Root Causes

* Fixed container height / CSS overflow
* Plotly not resizing correctly
* Weak data selection (wrong window or signals)
* No contextual framing (baseline vs spike)

---

### Problem

The plot currently shows “sensor activity” but lacks:

* Context
* Focus
* Interpretability

---

### Proposed Improvements

#### 1. Redefine Purpose

**Option A — Key Signal Focus**

* Show only relevant sensors per inference
* Highlight trigger + supporting signals

**Option B — Before/After View**

* Show:

  * Baseline
  * Peak
  * Recovery

**Option C — Normalised Signals**

```
(value - baseline) / baseline
```

---

#### 2. UI Fixes

* Ensure responsive sizing:

  * `responsive: true`
  * `autosize: true`
* Increase minimum height (~250px)
* Add:

  * Axis labels
  * Legend
  * Tooltips

---

#### 3. Add View Modes

* Raw
* Normalised
* Single Sensor
* Multi-Sensor

---

#### 4. Event Context Enhancements

* Vertical event marker
* Highlight detection window
* Emphasise peak values

---

## 🐛 Bug: Correlation Plot Scaling & Interpretability

### Symptoms

* Sensor values not comparable
* Large values dominate (eCO2 vs PM)
* Hard to see relationships

---

### Root Cause

Different scales:

* eCO2: 100–2000+
* TVOC: 10–100s
* PM: 1–50

Raw plotting makes comparison meaningless.

---

### Core Insight

Correlation ≠ absolute values
It’s about:

* Direction
* Magnitude of change
* Co-movement

---

### Proposed Solutions

#### Option 1 — Normalised Overlay (Recommended)

Z-score:

```
z = (x - mean) / std
```

Or baseline ratio:

```
ratio = x / baseline
```

---

#### Option 2 — Indexed Time Series

```
index = value / value_at_start * 100
```

---

#### Option 3 — Small Multiples

* Separate plots per sensor
* Shared time axis

---

#### Option 4 — Dual Axis (Not Recommended)

* Complex and confusing at scale

---

### Recommended Implementation

Default:

* Normalised (z-score or ratio)

Add toggles:

* Raw
* Normalised
* % change

Enhance hover:

* Show raw + transformed values

---

### Bonus: Correlation Insights

Extend existing calculations:

* Compute correlation matrix over selected window
* Display:

  * Strongest relationships
  * Correlation strength (r)

---

## 🔭 Future Direction: Explainable Events

Combine:

* Tagged data
* Feature vectors
* Correlation signals

To generate explanations like:

> “This event was likely caused by cooking because PM2.5 and TVOC rose together by 2.3× baseline, matching previous tagged cooking events.”


---

## Plant Grow Unit roadmap

### Phase 2 (next)
- Filter / sort row on Grow tab fleet view
- Per-unit Configure tab (light windows editor, plant profile picker, PID tunables, calibration two-step, soak-window override, intentional-friction safety override)
- Per-unit History tab (long-range moisture chart, photo timelapse scrubber)
- Settings → Grow page (enrollment key rotation UI, default tunables, holiday mode)
- Photo lightbox on click

### Phase 3
- Per-unit Diagnostics tab (WS connection log, sensor sanity, firmware version, danger zone)
- grow_errors UI surfacing (separate from the air-quality Incidents tab)
- Buffered-message replay UI
- Storage warning UI

### Phase 4 (polish)

> **Reordered from Phase 5 → Phase 4.** First physical deployment surfaced enough rough edges (SD-card failure mid-deploy, opaque deploy command, no-thumbnail fleet view, "wall wart" terminology, no on-Pi diagnostics) that polish should land before any ML work. Smarts moved to Phase 5.
>
> **Already landed in this overnight session:** `bin/deploy` script + readme; "wall wart" → "USB power adapter" sweep across `PLANT_GROW_UNIT_HARDWARE.md` and `PLANT_GROW_UNIT_SETUP.md`.

- ~~**Server-side photo thumbnail/resize endpoint**~~ — **shipped:** `GET /api/grow/units/<id>/photo/latest?size=thumb` and `GET /api/grow/units/<id>/photos/<photo_id>?size=thumb`. Pillow downscales to 320px on first request, caches to `data/grow_thumbnails/<unit_id>/<...>_w320.jpg`, reuses on subsequent hits. Fleet view (`grow-card.mjs`) now requests `?size=thumb` instead of the full ~2MB capture. `DELETE /api/grow/units/<id>/photos` invalidates the per-unit thumbnail cache.
- ~~**USB SSD boot guide for MLSS server and grow units**~~ — **shipped:** [`docs/USB_SSD_BOOT_GUIDE.md`](USB_SSD_BOOT_GUIDE.md). Hardware list, live `rsync` migration recipe with `fstab` / `cmdline.txt` PARTUUID fix-up, validation, rollback, troubleshooting. Notes that Pi Zero W grow units don't need this.
- Custom Pi SD-card .img for one-step provisioning
- ~~Public PyPI release of `mlss-grow`~~ — **infrastructure shipped:** classifiers/license/keywords/readme on `grow_unit/pyproject.toml` + `contracts/pyproject.toml`, root MIT `LICENSE`, `scripts/prepare_pypi_release.py` (path-dep → versioned dep rewriter, with restore-on-completion), `.github/workflows/publish-mlss-grow.yml` + `publish-mlss-contracts.yml` triggered by `mlss-{pkg}-v*` tag pushes, `docs/RELEASE_PROCESS.md`. **Maintainer to-do** before first release: create the projects on PyPI (manual upload), mint project-scoped API tokens, add `PYPI_API_TOKEN` repo secret, then tag `mlss-contracts-v0.1.0` followed by `mlss-grow-v0.1.0`.
- ~~Mobile-optimised fleet view~~ — **shipped** in `static/css/grow.css`. New `@media (max-width: 540px)` rules narrow grid padding, single-column cards, stacked page-header (full-width Add Unit button), horizontal-scrolling unit-detail tabs. New `@media (hover: none) and (pointer: coarse)` block enforces 44px minimum touch-target on `.px-btn`, `.gu-btn`, `.du-act-btn`, filter chips, and tabs.
- **Local read-only status UI on the grow unit itself** — tiny Flask app on a separate port (e.g. `http://<pi-ip>:8080/`) so an operator can SSH-free check the unit's health when MLSS is unreachable. Surfaces: live sensor readings, buffered-message + buffered-photo counts, last successful WS connect time, last 50 log lines, WiFi RSSI. **Read-only — no actuator controls** (those route via MLSS so audit/RBAC stays consistent). No auth (LAN-only by definition; same trust model as MLSS). Particularly useful for diagnosing "is the Pi alive when MLSS is down?" scenarios — the firmware design tolerates MLSS outages (buffer + replay) but currently you need SSH + journalctl to verify. Discovered as a real gap during the first physical deployment when the MLSS server's SD card failed mid-deployment and the operator had no quick way to verify the Pi was still capturing.
- Plant journal / annotations on the History tab
- Time-lapse video generation

### Phase 5 (smarts)
- Image-based phase classifier
- Plant-stage-aware PID adjustments
- Cross-unit anomaly detection
- Reservoir / water budget tracking

### Hardware/reliability deferred
- **Hardware watchdog (`/dev/watchdog`)** on Pi Zero — designed in but not wired up due to risk of misconfigured timer rebooting healthy Pi mid-write. Re-evaluate if a unit silently wedges in production despite systemd watchdog.


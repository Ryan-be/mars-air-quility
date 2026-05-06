# Plant Grow Unit — History Tab (Phase 2.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-unit History subtab with long-range moisture chart + photo timelapse scrubber.

**Architecture:** The existing `GET /api/grow/units/<id>/history` already returns moisture + watering events for `24h`/`7d`/`30d`. Extends it with longer ranges (`90d`, `all`) and downsampling for charts that would otherwise return millions of points. A NEW `GET /api/grow/units/<id>/photos?range=…` lists photos in a range as `{id, taken_at, telemetry_id}` so the scrubber can fetch them by ID via a NEW `GET /api/grow/units/<id>/photos/<photo_id>` route. Frontend renders a range-selector + line chart (vanilla SVG via existing patterns) + a scrubber widget that loads photos lazily on hover/scrub.

**Tech Stack:** Flask + SQLite + vanilla ES modules. Reuses `sensor-event-chart.mjs` for the chart base (with extension for longer ranges).

---

## File Structure

**Create:**
- `mlss_monitor/routes/api_grow_photos_list.py` — new `/photos` list + `/photos/<id>` fetch endpoints. (Or extend `api_grow_photos.py` — read it first; if it's small, add to it.)
- `static/js/grow/components/moisture-history-chart.mjs` — long-range chart with range selector
- `static/js/grow/components/photo-timelapse.mjs` — scrubber widget
- `static/js/grow/components/history-panel.mjs` — orchestrator that mounts the two children
- `tests/grow_server/test_api_grow_history.py` — extend (or create if missing) with new range tests + downsample test
- `tests/grow_server/test_api_grow_photos_list.py` — new endpoint tests
- `tests/js/test_moisture_history_chart.mjs`
- `tests/js/test_photo_timelapse.mjs`

**Modify:**
- `mlss_monitor/routes/api_grow_history.py` — add `90d`, `all` ranges + downsampling
- `mlss_monitor/routes/api_grow_photos.py` — keep `latest_photo`; add list-by-range + by-id (or split into new file)
- `mlss_monitor/routes/__init__.py` — register new blueprint if split
- `static/js/grow/unit_detail.mjs` — flip `history` subtab to `enabled: true`, mount on tab click
- `tests/grow_server/test_grow_photos_api.py` — extend if existing tests are there

---

## Task 1: Extend `/history` with longer ranges + downsampling

**Files:**
- Modify: `mlss_monitor/routes/api_grow_history.py`
- Test: `tests/grow_server/test_api_grow_history.py` (create or extend)

**Why downsample:** A unit logging once per minute for 90 days = 130k points. The browser SVG can't render that and the wire payload is wasteful. Bucket into N buckets where N = max chart pixel width (~600). Use `(MIN, AVG, MAX)` per bucket so the chart can show a band instead of a single line at long ranges.

- [ ] **Step 1: Write failing tests**

```python
# tests/grow_server/test_api_grow_history.py
import sqlite3
from datetime import datetime, timedelta


def _seed_telemetry(db_path, unit_id, count, start_ts, interval_s=60):
    """Insert `count` telemetry rows starting from start_ts, each interval_s apart."""
    conn = sqlite3.connect(db_path)
    for i in range(count):
        ts = start_ts + timedelta(seconds=i * interval_s)
        pct = 50 + (i % 30) - 15  # oscillates 35-65
        raw = 600 + i % 200
        conn.execute(
            "INSERT INTO grow_telemetry "
            "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
            " light_state, pump_state) VALUES (?, ?, ?, ?, 1, 0)",
            (unit_id, ts, raw, pct),
        )
    conn.commit()
    conn.close()


def test_history_accepts_90d_range(app_client, db):
    client, _ = app_client
    # Seed unit
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO grow_units (id, hardware_serial, label, "
                 "enrolled_at, bearer_token_hash, phase_set_at) "
                 "VALUES (1, 'hw1', 'X', ?, 'h', ?)",
                 (datetime.utcnow(), datetime.utcnow()))
    conn.commit()
    conn.close()
    r = client.get("/api/grow/units/1/history?range=90d")
    assert r.status_code == 200


def test_history_accepts_all_range(app_client, db):
    # ... similar, range=all
    pass


def test_history_downsamples_when_over_threshold(app_client, db):
    """Long ranges with many points should bucket into <=600 points,
    each with min/avg/max for the bucket."""
    # Seed 1000 telemetry points, request 30d range
    # Assert response moisture array length <= 600
    # Assert each entry has {ts, pct_min, pct_avg, pct_max} (extended shape)
    pass


def test_history_short_range_no_downsample_keeps_raw_shape(app_client, db):
    """24h range with 100 points returns those 100 points individually
    (existing shape: {ts, pct, raw}) — no downsample."""
    pass


def test_history_includes_phase_changes(app_client, db):
    """The frontend chart annotates phase transitions. Add a phase_changes
    array to the response: [{ts, from_phase, to_phase}, ...] derived from
    grow_units history (TODO: requires phase audit table — for Task 1 just
    return [] if no audit infrastructure exists yet)."""
    # Skipped — see note above. Test asserts the key exists with [] value.
    r = ...
    assert "phase_changes" in r.json
    assert r.json["phase_changes"] == []
```

- [ ] **Step 2: Implement**

Add to `_RANGE_TO_HOURS`:
```python
_RANGE_TO_HOURS = {"24h": 24, "7d": 168, "30d": 720, "90d": 2160, "all": None}
```

For `all`, omit the cutoff filter. For ranges where the SELECT row count exceeds `_DOWNSAMPLE_THRESHOLD = 600`, bucket the data:

```python
_DOWNSAMPLE_THRESHOLD = 600

def _maybe_downsample(rows, target=_DOWNSAMPLE_THRESHOLD):
    """If rows > target, bucket into target buckets and return min/avg/max per bucket.
    
    Returns either the raw rows (untouched) OR a downsampled list with the
    extended shape: [{ts, pct_min, pct_avg, pct_max, raw_avg}, ...]"""
    if len(rows) <= target:
        return [{"ts": r["timestamp_utc"], "pct": r["soil_moisture_pct"],
                 "raw": r["soil_moisture_raw"]} for r in rows]
    # Bucket
    bucket_size = len(rows) / target
    buckets = []
    for i in range(target):
        start = int(i * bucket_size)
        end = int((i + 1) * bucket_size)
        slice_rows = rows[start:end]
        if not slice_rows:
            continue
        pcts = [r["soil_moisture_pct"] for r in slice_rows if r["soil_moisture_pct"] is not None]
        raws = [r["soil_moisture_raw"] for r in slice_rows]
        if not pcts:
            continue
        buckets.append({
            "ts": slice_rows[len(slice_rows)//2]["timestamp_utc"],  # midpoint
            "pct_min": min(pcts),
            "pct_avg": sum(pcts) / len(pcts),
            "pct_max": max(pcts),
            "raw_avg": sum(raws) / len(raws),
        })
    return buckets
```

Frontend will detect downsampled-vs-raw shape by checking for `pct_avg` key.

- [ ] **Step 3: Run + commit**

Commit: `Extend /history with 90d/all + downsampling for long ranges (Task 1)`

---

## Task 2: New `/photos` list endpoint

**Files:**
- Modify: `mlss_monitor/routes/api_grow_photos.py` (add list + by-id endpoints)
- Test: `tests/grow_server/test_api_grow_photos_list.py` (new)

- [ ] **Step 1: Write failing tests**

- `test_photos_list_returns_all_photos_in_range` — seed 5 photos at known timestamps; GET `?range=24h`; assert exactly the photos within 24h are returned, sorted by `taken_at ASC`
- `test_photos_list_returns_id_and_taken_at_only` — response shape: `[{id, taken_at, telemetry_id}, …]` (no file paths in the listing — frontend uses the by-id route to fetch the JPEG)
- `test_photos_list_supports_90d_and_all` — range parsing matches `/history`
- `test_photos_list_invalid_range_400`
- `test_photos_list_returns_empty_array_for_unit_with_no_photos`
- `test_photo_by_id_serves_jpeg` — GET `/api/grow/units/<id>/photos/<photo_id>` → 200, content-type image/jpeg, body matches the file
- `test_photo_by_id_404_for_unknown_id`
- `test_photo_by_id_404_when_photo_belongs_to_different_unit` — security: photo IDs are scoped to their unit; unit A can't fetch unit B's photo via path manipulation

- [ ] **Step 2: Implement**

```python
@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photos", methods=["GET"])
def list_photos(unit_id):
    range_str = request.args.get("range", "24h")
    if range_str not in _RANGE_TO_HOURS:  # share with api_grow_history if possible
        return jsonify({"error": "invalid_range"}), 400
    hours = _RANGE_TO_HOURS[range_str]
    cutoff = datetime.utcnow() - timedelta(hours=hours) if hours else None
    
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if cutoff is not None:
            rows = conn.execute(
                "SELECT id, taken_at, telemetry_id FROM grow_photos "
                "WHERE unit_id=? AND taken_at >= ? ORDER BY taken_at ASC",
                (unit_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, taken_at, telemetry_id FROM grow_photos "
                "WHERE unit_id=? ORDER BY taken_at ASC",
                (unit_id,),
            ).fetchall()
    finally:
        conn.close()
    return jsonify([
        {"id": r["id"], "taken_at": r["taken_at"], "telemetry_id": r["telemetry_id"]}
        for r in rows
    ])


@api_grow_photos_bp.route("/api/grow/units/<int:unit_id>/photos/<int:photo_id>",
                          methods=["GET"])
def photo_by_id(unit_id, photo_id):
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT file_path FROM grow_photos WHERE id=? AND unit_id=?",
            (photo_id, unit_id),  # cross-check — security
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    abs_path = os.path.join(_resolve_images_dir(), row[0])
    if not os.path.exists(abs_path):
        abort(404)
    directory, filename = os.path.split(abs_path)
    return send_from_directory(directory, filename, mimetype="image/jpeg")
```

Use `_resolve_images_dir()` from `mlss_monitor.grow.photo_storage` (the existing helper that respects app_settings override → env var → default).

- [ ] **Step 3: Run + commit**

Commit: `Add GET /photos list + /photos/<id> fetch endpoints (Task 2)`

---

## Task 3: Long-range moisture chart component

**Files:**
- Create: `static/js/grow/components/moisture-history-chart.mjs`
- Test: `tests/js/test_moisture_history_chart.mjs`

- [ ] **Step 1: Write failing tests**

- `test_renders_range_selector_with_5_options` — 24h / 7d / 30d / 90d / all
- `test_default_range_is_24h`
- `test_renders_line_when_short_range_data_shape` — `{ts, pct, raw}` shape → single line
- `test_renders_band_when_downsampled_data_shape` — `{ts, pct_min, pct_avg, pct_max}` shape → SVG band (filled area between min and max) + line at avg
- `test_clicking_range_button_refetches_with_new_range` — mock fetch; click "7d"; assert new fetch URL contains `range=7d`
- `test_overlays_watering_events_as_vertical_marks` — for each watering event, draw a vertical line at its `ts`
- `test_shows_target_band_horizontal_line` — if unit has overrides.watering_target, draw a horizontal target line on the chart

- [ ] **Step 2: Implement**

Use the existing `sensor-event-chart.mjs` as a base pattern. Build a small SVG renderer with:
- X axis: time, 5-7 evenly-spaced labels appropriate to the range
- Y axis: 0-100% with minor gridlines at 25/50/75
- For raw data: single SVG `<path>` with line interpolation
- For downsampled data: SVG `<path>` for the band (fill) + `<path>` for the avg line + outline at min/max
- Watering events: `<line>` verticals at event timestamps
- Target band: dashed `<line>` horizontal at target_pct

- [ ] **Step 3: Run + commit**

Commit: `Add moisture-history-chart component with range selector + downsample support (Task 3)`

---

## Task 4: Photo timelapse scrubber

**Files:**
- Create: `static/js/grow/components/photo-timelapse.mjs`
- Test: `tests/js/test_photo_timelapse.mjs`

- [ ] **Step 1: Write failing tests**

- `test_renders_scrubber_with_correct_step_count` — given 10 photos, scrubber has 10 positions
- `test_default_position_is_latest_photo` — scrubber starts at the rightmost (most recent) position
- `test_changing_scrubber_position_loads_photo_by_id` — mock fetch for `/photos/<id>`; move scrubber left; assert correct photo URL is set on the img element
- `test_play_button_advances_through_timelapse` — click play; mock advancing timer; assert position auto-increments at the configured interval
- `test_play_loops_or_stops_at_end` — pick one (probably stops at end) and pin it
- `test_displays_taken_at_for_current_photo` — caption shows the timestamp of the currently-displayed photo

- [ ] **Step 2: Implement**

```javascript
export function renderPhotoTimelapse(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.className = "du-panel cfg-tlapse";
  // ... range selector (24h/7d/30d/all)
  // ... fetch /photos?range=...
  // ... render <input type="range" min="0" max="N-1" value="N-1">
  // ... <img> element with the current photo URL
  // ... play/pause button
  // ... caption with formatted taken_at
  return wrap;
}
```

Avoid eager-loading every photo (could be hundreds). Just preload the current + next 2 photos as the user scrubs.

- [ ] **Step 3: Run + commit**

Commit: `Add photo-timelapse component with scrubber + autoplay (Task 4)`

---

## Task 5: History panel orchestrator + subtab integration

**Files:**
- Create: `static/js/grow/components/history-panel.mjs`
- Modify: `static/js/grow/unit_detail.mjs`
- Test: `tests/js/test_unit_detail_skeleton.mjs` (extend — test History is now enabled)

- [ ] **Step 1: Write failing tests**

- `test_history_panel_mounts_chart_and_timelapse` — given a unit, the panel includes both children
- `test_history_subtab_is_enabled` — extend skeleton test
- `test_clicking_history_subtab_renders_history_content` — extend the existing tab-switch test

- [ ] **Step 2: Implement**

```javascript
// history-panel.mjs
import { renderMoistureHistoryChart } from "./moisture-history-chart.mjs";
import { renderPhotoTimelapse } from "./photo-timelapse.mjs";

export function renderHistoryPanel(unit, opts = {}) {
  const doc = opts.ownerDocument || document;
  const wrap = doc.createElement("div");
  wrap.appendChild(renderMoistureHistoryChart(unit, opts));
  wrap.appendChild(renderPhotoTimelapse(unit, opts));
  return wrap;
}
```

In `unit_detail.mjs`:
- Flip the History subtab to `enabled: true`, remove `deferred`
- Extend `switchSubtab` to handle `tabId === "history"` → `body.appendChild(renderHistoryPanel(unit))`

- [ ] **Step 3: Run + commit**

Commit: `Wire History subtab + panel orchestrator (Task 5)`

---

## Task 6: E2E stack test

**Files:**
- Create: `tests/grow_server/test_history_e2e.py`

- [ ] **Step 1: Write the e2e test**

Real Flask app + admin session + seeded unit with 100 telemetry rows + 10 photos. One test that:
1. GETs `/api/grow/units/1/history?range=24h` → expected shape (raw, not downsampled)
2. GETs `/api/grow/units/1/history?range=30d` with 1000 seeded points → expected shape (downsampled)
3. GETs `/api/grow/units/1/photos?range=24h` → list shape
4. GETs `/api/grow/units/1/photos/<first_id>` → JPEG bytes match fixture file
5. RBAC: viewer can read history (it's read-only data — confirm intended posture; if endpoint is admin-only, adjust)

- [ ] **Step 2: Implement**

Mirror the structure from `tests/grow_server/test_configure_e2e.py` but simpler — no WS round-trip needed.

- [ ] **Step 3: Run + commit**

Commit: `Add e2e stack test for History tab flow (Task 6)`

---

## Self-review notes

- All endpoints are GET → no RBAC change needed (assuming history is viewer-readable, which matches "look at your plant's data" UX expectations). Confirm this assumption in Task 6.
- The downsampling is in-Python rather than SQL because SQLite doesn't have window/percentile functions in stock builds. For 1k-130k points, Python-side bucketing is fast enough.
- Photos by-id endpoint cross-checks `unit_id` to prevent path-traversal-by-photo-id (security: unit A's logged-in viewer shouldn't fetch unit B's photos).
- The chart is vanilla SVG — no Chart.js or D3 dependency added.
- Photo timelapse only fetches metadata up-front (id + taken_at); JPEG bytes load on demand as the user scrubs.

---

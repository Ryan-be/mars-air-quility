# Phase 6 Design Specification — Display UI & ML Insights

**Date:** 2026-04-04
**Branch:** claude/zealous-hugle
**Status:** Ready for implementation
**Prerequisite:** Phase 5 complete (multivariate composite River models, attribution engine, FeatureVector pipeline)

---

## Overview

Phase 6 has two headline goals:

1. **Richer inference cards** — every card gains a detection method chip, an attribution badge with runner-up, a temporal sparkline, and ⓘ tooltips on every evidence chip.
2. **History page enters the ML era** — the Correlations tab grows to support all 10 sensor channels, gains an anomaly event overlay, and gets a smarter zoom-triggered analysis panel. The Patterns tab is renamed "Detections & Insights" and rebuilt as a narrative-first ML insights view with 9 distinct sections.

Additional work: live anomaly scores on the Insights Engine settings page, a Settings nav reorganisation, and two targeted bug fixes (UTC timestamps and MICS6814 channel units).

---

## Design Principles

These principles apply to every feature in this phase:

- **Backend-heavy interpretation.** All narrative text, ratio calculations, trend labels, attribution narratives, and drift flags are computed in Python. JavaScript only renders pre-computed fields. No analytical logic in JS.
- **Human-readable first.** Every chart, score, and badge has an ⓘ tooltip explaining its meaning in plain English. No raw model jargon is ever shown without an accompanying explanation.
- **Hybrid REST + SSE.** Pages load their initial historical state via REST on first load. The existing SSE stream pushes incremental updates so views stay live without a full page refresh.

---

## Section 1 — Backend API & SSE Extensions

### 1.1 New REST Endpoints

All new endpoints sit under the `/api/history/` prefix. A new route file `mlss_monitor/routes/api_history.py` houses all of them. Register it in the Flask app factory alongside existing blueprints.

#### `GET /api/history/sensor`

Returns time-series data for all 10 sensor channels over a requested window.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `channels` | string | No | `all` (default) or comma-separated channel names |
| `start` | ISO 8601 string | Yes | Window start (UTC) |
| `end` | ISO 8601 string | Yes | Window end (UTC) |

**Response schema:**

```json
{
  "timestamps": ["2026-04-04T14:00:00Z", "..."],
  "channels": {
    "tvoc_ppb":    [120, 135, 141, "..."],
    "eco2_ppm":    [512, 528, 531, "..."],
    "temperature_c": [21.4, 21.5, "..."],
    "humidity_pct":  [48.2, 48.0, "..."],
    "pm1_ug_m3":   [3.1, 3.2, "..."],
    "pm25_ug_m3":  [4.8, 5.1, "..."],
    "pm10_ug_m3":  [6.2, 6.5, "..."],
    "co_ppb":      [12400, 12350, "..."],
    "no2_ppb":     [8900, 8870, "..."],
    "nh3_ppb":     [15200, 15180, "..."]
  }
}
```

Note: `co_ppb`, `no2_ppb`, and `nh3_ppb` field names are kept for backwards compatibility but their values are raw resistance readings in kΩ (see Bug Fix 2). This channel replaces whatever sensor data the current Correlations chart fetches.

**Implementation:** Query `sensor_data` table. Filter by timestamp range. Return at native 60-second resolution. All 10 channels must be present in every response (use `null` for missing values).

---

#### `GET /api/history/ml-context`

Returns inference records and attribution summaries for a time window. Used by the Correlations tab analysis panel (on zoom) and the Detections & Insights tab.

**Query parameters:**

| Parameter | Type | Required |
|-----------|------|----------|
| `start` | ISO 8601 string | Yes |
| `end` | ISO 8601 string | Yes |

**Response schema:**

```json
{
  "inferences": [
    {
      "id": 42,
      "created_at": "2026-04-04T14:23:00Z",
      "title": "TVOC spike detected",
      "event_type": "tvoc_spike",
      "severity": "warning",
      "attribution_source": "cooking",
      "attribution_confidence": 0.73,
      "runner_up_source": "combustion",
      "runner_up_confidence": 0.61,
      "detection_method": "rule"
    }
  ],
  "attribution_summary": {
    "cooking": 4,
    "combustion": 2,
    "ventilation": 1
  },
  "dominant_source": "cooking",
  "dominant_source_sentence": "Cooking accounts for 57% of events — ventilation around meal times would have the most impact."
}
```

**Implementation:** Call `get_inferences()` with the timestamp filter. Compute `detection_method` on each record (see Section 1.4). Compute `attribution_summary` as a count dict over `attribution_source`. Compute `dominant_source` as the key with the highest count. Call `narrative_engine.generate_period_summary()` to populate `dominant_source_sentence`.

---

#### `GET /api/history/baselines`

Returns the current EMA baseline for every channel, as stored in the live `AnomalyDetector` instance.

**Query parameters:** None.

**Response schema:**

```json
{
  "tvoc_ppb": 118.4,
  "eco2_ppm": 508.2,
  "temperature_c": 21.3,
  "humidity_pct": 47.9,
  "pm1_ug_m3": 2.9,
  "pm25_ug_m3": 4.5,
  "pm10_ug_m3": 6.0,
  "co_ppb": 12380.0,
  "no2_ppb": 8890.0,
  "nh3_ppb": 15190.0
}
```

**Implementation:** Access the shared `AnomalyDetector` instance (however it is currently exposed to routes) and read its per-channel EMA baseline values. If a channel has no baseline yet (cold-start), return `null` for that key.

---

#### `GET /api/history/narratives`

Returns all backend-generated narrative content for the Detections & Insights tab for a given window. This is the heaviest endpoint; it calls multiple narrative engine functions and returns a fully composed payload.

**Query parameters:**

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `start` | ISO 8601 string | Yes | — |
| `end` | ISO 8601 string | Yes | — |

**Response schema:**

```json
{
  "period_summary": "The past 24 hours were mostly clean with a brief period of elevated TVOC around the evening meal. Cooking was the dominant detected source.",
  "trend_indicators": [
    {
      "channel": "tvoc_ppb",
      "label": "TVOC",
      "unit": "ppb",
      "current_baseline": 118.4,
      "week_ago_baseline": 105.2,
      "pct_change": 12.5,
      "direction": "up",
      "colour": "amber",
      "sentence": "TVOC baseline is 12.5% higher than a week ago — worth monitoring."
    }
  ],
  "longest_clean_hours": 18.3,
  "longest_clean_start": "2026-04-03T22:00:00Z",
  "longest_clean_end": "2026-04-04T16:18:00Z",
  "attribution_breakdown": {
    "cooking": 4,
    "combustion": 2,
    "ventilation": 1
  },
  "dominant_source_sentence": "Cooking accounts for 57% of events — ventilation around meal times would have the most impact.",
  "fingerprint_narratives": [
    {
      "source_id": "cooking",
      "label": "Cooking",
      "emoji": "🍳",
      "event_count": 4,
      "avg_confidence": 0.71,
      "typical_hours": [12, 13, 18, 19],
      "narrative": "Cooking was detected 4 times, mostly around lunch and dinner. TVOC and eCO2 patterns match gas hob activity. Opening a window while cooking would reduce peak readings."
    }
  ],
  "anomaly_model_narratives": [
    {
      "model_id": "combustion_signature",
      "label": "Combustion Signature",
      "event_count": 2,
      "description": "Elevated CO resistance with correlated TVOC and particle rises — consistent with combustion events.",
      "narrative": "The combustion signature model flagged 2 events. Both occurred in the evening and show the classic CO/TVOC co-rise pattern."
    }
  ],
  "pattern_heatmap": {
    "0_18": 2,
    "0_19": 1,
    "1_18": 3
  },
  "drift_flags": [
    {
      "channel": "co_ppb",
      "shift_pct": 18.2,
      "direction": "up",
      "message": "CO resistance has shifted 18% over 7 days. This could mean sensor drift, or a new persistent background source. Worth checking."
    }
  ]
}
```

**`pattern_heatmap` key format:** `"{day_of_week}_{hour_of_day}"` where day 0 = Monday, hour 0–23. Value is event count for that cell.

**Implementation notes:**

1. Fetch inferences for the window via `get_inferences()`.
2. Fetch baselines now via `AnomalyDetector`.
3. Fetch baselines 7 days ago — query the `sensor_data` table for the EMA snapshot or recompute from historical data. Implementation detail: store a daily baseline snapshot in the DB, or recompute from the oldest 24h of data in the 7d window. Choose whichever is already available; document the choice in a code comment.
4. Call all relevant `narrative_engine` functions (see Section 1.5).
5. Compose and return the full dict.

---

### 1.2 SSE Stream Extensions

The existing SSE endpoint gains two new event types. These are pushed to all connected clients.

#### Extended `sensor_reading` event

Currently the `sensor_reading` event may only include a subset of channels. It must be extended to include all 10 channels on every push. Payload addition:

```json
{
  "type": "sensor_reading",
  "data": {
    "timestamp": "2026-04-04T14:30:00Z",
    "tvoc_ppb": 132,
    "eco2_ppm": 521,
    "temperature_c": 21.5,
    "humidity_pct": 48.1,
    "pm1_ug_m3": 3.0,
    "pm25_ug_m3": 4.9,
    "pm10_ug_m3": 6.3,
    "co_ppb": 12390,
    "no2_ppb": 8880,
    "nh3_ppb": 15195
  }
}
```

#### New `inference_fired` event

Pushed immediately when `DetectionEngine` saves a new inference to the database.

```json
{
  "type": "inference_fired",
  "data": {
    "id": 43,
    "created_at": "2026-04-04T14:31:00Z",
    "title": "Combustion signature detected",
    "event_type": "anomaly_combustion_signature",
    "severity": "alert",
    "attribution_source": "combustion",
    "attribution_confidence": 0.81,
    "detection_method": "ml"
  }
}
```

**Implementation:** Hook into the existing detection pipeline. After `DetectionEngine` saves an inference, enqueue the SSE event in the same async path used for existing events. `detection_method` is computed the same way as for REST responses (see Section 1.4).

#### New `anomaly_scores` event

Pushed every 30 seconds by a background task.

```json
{
  "type": "anomaly_scores",
  "data": {
    "timestamp": "2026-04-04T14:31:30Z",
    "scores": {
      "combustion_signature": 0.42,
      "particle_distribution": 0.11,
      "ventilation_quality": 0.67,
      "gas_relationship": 0.08,
      "thermal_moisture": 0.29
    }
  }
}
```

**Implementation:** Add a 30-second timer in the SSE generator (or existing background loop) that reads the current score from each River model and pushes this event. Each score is a float 0.0–1.0. During cold-start, omit the model key (or set to `null`) — the client renders "Learning…" for missing/null scores.

---

### 1.3 Inference Sparkline Endpoint

#### `GET /api/inferences/<id>/sparkline`

Returns sensor time-series data for the ±15-minute window around a specific inference event. This endpoint is lazy — called only when the inference dialog is opened, not during list load.

**Path parameter:** `id` — integer inference ID.

**Response schema:**

```json
{
  "timestamps": ["2026-04-04T14:15:00Z", "..."],
  "channels": {
    "tvoc_ppb": [115, 118, 122, 135, 148, 152, 141, 132, 125, 121, 118, 117, 116, 118, 120, "..."],
    "eco2_ppm": [508, 510, 515, 522, 535, 540, 530, 518, 510, 507, 505, 504, 505, 507, 509, "..."]
  },
  "inference_at": "2026-04-04T14:30:00Z",
  "triggering_channels": ["tvoc_ppb", "eco2_ppm"]
}
```

**Implementation:**

1. Look up the inference by `id`. Return 404 if not found.
2. Compute `window_start = created_at - 15 minutes`, `window_end = created_at + 15 minutes`.
3. Query `sensor_data` table for the window at 60-second resolution.
4. Derive `triggering_channels` from the inference's `sensor_snapshot` evidence field. For composite/multivariate inferences (`detection_method == "ml"`), include all channels that feed the relevant model. For rule/statistical inferences, include the channels present in `sensor_snapshot`.
5. The `channels` dict in the response contains **only** `triggering_channels` — not all 10 — to keep the sparkline focused.
6. `inference_at` is the inference's `created_at` with `Z` suffix (UTC).

---

### 1.4 `detection_method` Field

This field is computed at query time from `event_type`. It is **never stored** in the database. It must be added to:

- The `get_inferences()` function in `database/db_logger.py` (computed, appended to each row dict).
- The `/api/inferences` response.
- The `/api/history/ml-context` response.
- The `inference_fired` SSE event payload.

**Mapping logic:**

```python
RULE_EVENT_TYPES = {
    "tvoc_spike", "eco2_danger", "eco2_elevated", "mould_risk",
    "correlated_pollution", "sustained_poor_air",
    "pm1_spike", "pm1_elevated", "pm25_spike", "pm25_elevated",
    "pm10_spike", "pm10_elevated",
    "temp_high", "temp_low", "humidity_high", "humidity_low", "vpd_high", "vpd_low",
    "rapid_tvoc_rise", "rapid_eco2_rise", "rapid_pm25_rise",
    # annotation_context_* matched by prefix below
}

STATISTICAL_EVENT_TYPE_PREFIX = "anomaly_"
STATISTICAL_SINGLE_CHANNEL_SUFFIXES = {
    "tvoc", "eco2", "temperature", "humidity",
    "pm25", "pm1", "pm10", "co", "no2", "nh3"
}

ML_EVENT_TYPES = {
    "anomaly_combustion_signature", "anomaly_particle_distribution",
    "anomaly_ventilation_quality", "anomaly_gas_relationship",
    "anomaly_thermal_moisture"
}

def compute_detection_method(event_type: str) -> str:
    if event_type in ML_EVENT_TYPES:
        return "ml"
    if event_type.startswith("anomaly_") and event_type[len("anomaly_"):] in STATISTICAL_SINGLE_CHANNEL_SUFFIXES:
        return "statistical"
    if event_type.startswith("annotation_context_"):
        return "rule"
    if event_type in RULE_EVENT_TYPES:
        return "rule"
    # Fallback — log a warning and return "rule"
    return "rule"
```

Place this function in `database/db_logger.py` (or a shared utils module accessible to both `db_logger.py` and routes). Import and call it wherever `detection_method` must be included in a response.

---

### 1.5 Narrative Engine — `mlss_monitor/narrative_engine.py`

A new module of pure functions with no IO, no database calls, and no Flask imports. All functions take plain Python dicts/lists and return strings or dicts. This makes them trivially testable.

#### `generate_period_summary(inferences, trend_indicators, dominant_source) -> str`

Generates a 2–3 sentence summary of the period.

**Parameters:**

- `inferences`: list of inference dicts (same shape as API response).
- `trend_indicators`: list of trend indicator dicts (from `compute_trend_indicators`).
- `dominant_source`: string source label, or `None` if no attributions.

**Output:** Plain English string, 2–3 sentences. Example: *"The past 24 hours were mostly clean with one alert and two warnings. Cooking was the most commonly attributed source. TVOC and eCO2 baselines are stable."*

**Logic:** Branch on `len(inferences)` (zero events = clean period message), dominant source presence, and whether any trend indicators are amber/red.

---

#### `generate_fingerprint_narrative(source_id, label, events, avg_confidence, typical_hours) -> str`

Generates a 2–3 sentence narrative card for a source fingerprint.

**Parameters:**

- `source_id`: string identifier (e.g. `"cooking"`).
- `label`: human-readable label (e.g. `"Cooking"`).
- `events`: list of inference dicts attributed to this source.
- `avg_confidence`: float 0.0–1.0.
- `typical_hours`: list of ints (hours of day, 0–23) when this source was detected.

**Output:** Plain English 2–3 sentences including event count, confidence characterisation, time-of-day summary, and one actionable sentence. Example: *"Cooking was detected 4 times, mostly around lunch and dinner (12:00–13:00 and 18:00–19:00). Average attribution confidence was 71%, which is moderately strong. Opening a window while cooking would reduce peak readings."*

**Actionable advice lookup:** Maintain a dict in this module mapping `source_id` to a static actionable advice string. If `source_id` not found, omit the actionable sentence.

**Zero events case:** If `events` is empty, return: *"No [label] events detected in this period."*

---

#### `generate_anomaly_model_narrative(model_id, label, event_count, description) -> str`

Generates a narrative card for a composite multivariate model.

**Parameters:**

- `model_id`: string (e.g. `"combustion_signature"`).
- `label`: human-readable label (e.g. `"Combustion Signature"`).
- `event_count`: int.
- `description`: one-sentence description of what this model watches.

**Output:** 2–3 sentences. Example: *"The Combustion Signature model flagged 2 events. It watches for co-rises in CO resistance, TVOC, and particles — a pattern typical of nearby combustion. Both events occurred in the evening and resolved within 20 minutes."*

If `event_count == 0`, this function is not called (the section is omitted for models with no events).

---

#### `detect_drift_flags(baselines_now, baselines_7d_ago, threshold=0.15) -> list[dict]`

Compares current EMA baselines to baselines from 7 days ago and returns flags for channels that have shifted significantly.

**Parameters:**

- `baselines_now`: dict `{channel: float}`.
- `baselines_7d_ago`: dict `{channel: float}`.
- `threshold`: float, default `0.15` (15%).

**Output:** List of dicts. Empty list if no drift detected.

```python
[
    {
        "channel": "co_ppb",
        "shift_pct": 18.2,
        "direction": "up",   # "up" | "down"
        "message": "CO resistance has shifted 18% over 7 days. This could mean sensor drift, or a new persistent background source. Worth checking."
    }
]
```

**Logic:** For each channel present in both dicts, compute `shift_pct = abs(now - then) / then * 100`. If `shift_pct > threshold * 100`, emit a flag. `direction` is `"up"` if `now > then`, else `"down"`. Skip channels where `baselines_7d_ago[channel]` is `None` or zero.

---

#### `compute_trend_indicators(baselines_now, baselines_7d_ago, channel_meta) -> list[dict]`

Returns one trend indicator dict per channel.

**Parameters:**

- `baselines_now`: dict `{channel: float}`.
- `baselines_7d_ago`: dict `{channel: float}`.
- `channel_meta`: dict `{channel: {label, unit}}` — the existing channel metadata from `_CHANNEL_META` in `inference_evidence.py`.

**Output:** List of dicts (one per channel):

```python
{
    "channel": "tvoc_ppb",
    "label": "TVOC",
    "unit": "ppb",
    "current_baseline": 118.4,
    "week_ago_baseline": 105.2,
    "pct_change": 12.5,
    "direction": "up",
    "colour": "amber",   # "green" | "amber" | "red"
    "sentence": "TVOC baseline is 12.5% higher than a week ago — worth monitoring."
}
```

**Colour logic:** `green` if `pct_change <= 10%`, `amber` if `10% < pct_change <= 25%`, `red` if `pct_change > 25%`. Direction and colour apply regardless of whether the change is up or down (both directions can be concerning depending on the sensor). The `sentence` field is generated inline from the label, pct_change, direction, and a fixed phrase table.

---

#### `compute_longest_clean_period(inferences, window_start, window_end) -> dict`

Finds the longest contiguous gap between inference events (or between window boundaries and the first/last event).

**Parameters:**

- `inferences`: list of inference dicts, sorted ascending by `created_at`.
- `window_start`: ISO string.
- `window_end`: ISO string.

**Output:**

```python
{
    "hours": 18.3,
    "start": "2026-04-03T22:00:00Z",
    "end": "2026-04-04T16:18:00Z"
}
```

If there are no inferences in the window, the entire window is the clean period. If the window is shorter than 1 hour, `hours` may be 0.0.

---

#### `compute_pattern_heatmap(inferences) -> dict`

Counts events per day-of-week × hour-of-day cell.

**Parameters:**

- `inferences`: list of inference dicts with `created_at` (UTC ISO strings with `Z`).

**Output:** Dict with keys in `"{day}_{hour}"` format (day 0 = Monday, 0–6; hour 0–23). Only cells with at least one event are included (sparse dict — do not emit zero-count cells).

```python
{"0_18": 2, "0_19": 1, "1_18": 3, "4_12": 1}
```

**Note:** Parse `created_at` as UTC. Convert to the same timezone as the data before bucketing (currently UTC — do not apply local offset in the backend; leave timezone display to the frontend).

---

## Section 2 — Inference Card Enhancements

These changes apply to `static/js/dashboard.js` and wherever inference cards are rendered. The dialog is the primary target; the compact list card receives only the detection method chip.

### 2.1 Detection Method Chip

**Placement:** Inline with the severity badge, in both the compact list card and the expanded dialog.

**Variants:**

| `detection_method` | Label | Style |
|--------------------|-------|-------|
| `"rule"` | Rule | Grey chip — `background: #6b7280; color: #fff` |
| `"statistical"` | Statistical | Blue chip — `background: #3b82f6; color: #fff` |
| `"ml"` | ML | Purple chip — `background: #8b5cf6; color: #fff` |

**ⓘ tooltip text (same for all variants):**
*"Rule = a fixed threshold was crossed. Statistical = an unusual reading compared to this sensor's learned normal. ML = an unusual pattern across multiple sensors simultaneously."*

**Chip HTML pattern:**

```html
<span class="chip chip--rule" title="[tooltip text]">
  Rule <span class="chip-info">ⓘ</span>
</span>
```

Use CSS classes `chip--rule`, `chip--statistical`, `chip--ml` for colour variants. The ⓘ icon triggers the tooltip on hover/focus.

---

### 2.2 Attribution Badge + Runner-Up

**Placement:** In the inference dialog only. A new "Source" row below the severity badge.

**Structure:**

```
Source:  [🍳 Cooking — 73%]   ←  coloured pill
         Also consistent with: Combustion (61%)  ←  muted secondary line
```

**Primary pill:** Colour is the source fingerprint's assigned colour (from the attribution engine config). Contains: emoji + label + confidence as percentage rounded to nearest integer.

**Runner-up line:** Rendered only if `runner_up_source` is not null AND `runner_up_confidence >= (attribution_confidence - 0.15)`. Muted text, no pill styling. Format: *"Also consistent with: [Label] ([confidence%])"*.

**ⓘ tooltip text:**
*"The attribution engine scores this event against known source fingerprints — combinations of sensor patterns associated with specific real-world causes."*

**No attribution case:** If `attribution_source` is null, omit the entire Source row. Do not show a "none" or empty state.

**Source label and emoji lookup:** The frontend must maintain a mapping from `source_id` to `{label, emoji, colour}`. This mapping must be the same set of sources defined in the attribution engine config. If a source_id is not in the frontend mapping, display the raw `source_id` with no emoji and a neutral colour.

---

### 2.3 Temporal Sparkline

**Placement:** In the inference dialog only. Below the attribution row, above the evidence chips.

**Behaviour:** Fetched lazily when the dialog opens. Show a loading spinner while fetching `/api/inferences/<id>/sparkline`. On load, render the chart. On fetch error, show: *"Sparkline unavailable."*

**Chart specification:**

- Library: Plotly (already in use).
- Type: multi-line time-series (one line per `triggering_channel`).
- Height: 100px (compact).
- X-axis: 30-minute window (−15 min to +15 min relative to inference time). Display as relative time labels: `−15m`, `−10m`, `−5m`, `0`, `+5m`, `+10m`, `+15m`.
- Y-axis: auto-scaled, no label (space is tight). One scale per chart (do not dual-axis this).
- Lines: each channel uses its assigned colour from `_CHANNEL_META`.
- Vertical dashed marker at `t = 0` (the inference `created_at`). Label: `"Event"`.
- `showlegend: false` — channel identity shown by colour only (consistent with the broader design pattern).
- Margins: minimal (`l:10, r:10, t:5, b:25`).
- No interactivity (no hover, no zoom) — this is a read-only sparkline.

**ⓘ tooltip text:**
*"Shows how sensor values moved in the 30 minutes around this event, so you can see the build-up and aftermath."*

---

### 2.4 Evidence Row Tooltips

**Placement:** Each chip in the existing evidence/sensor snapshot row in the inference dialog.

Each chip already shows a sensor label and value. Add an ⓘ icon to each chip. The tooltip text is generated by the backend in the `sensor_snapshot` evidence field: include a `ratio` field (value divided by EMA baseline at time of event, computed in the attribution engine or inference pipeline).

**ⓘ tooltip text pattern:**
*"[ratio]× normal — this channel was [ratio] times higher than its recent typical value when this event was detected."*

Example: *"3.2× normal — this channel was 3.2 times higher than its recent typical value when this event was detected."*

**Backend change required:** Add a `ratio` field to each channel entry in `sensor_snapshot`. Computed as `observed_value / ema_baseline_at_time`. If baseline is unavailable (cold-start), omit `ratio` and omit the ⓘ tooltip.

**Ratio display format:** Round to one decimal place. If `ratio < 0.5`, label as `"unusually low"` rather than giving a ratio (for channels like CO/NO2/NH3 where lower resistance means more gas: invert the interpretation in the label). Implementation note: include a `ratio_direction` field (`"high"` or `"low"`) in `sensor_snapshot` so the frontend does not need to know channel semantics.

---

## Section 3 — Correlations Tab

Changes are in `static/js/history.js` and `static/js/charts_correlation.js`.

### 3.1 Full Channel Selector (Toggle Chips)

Replace any existing channel controls with a chip-based selector for all 10 channels.

**Layout:** Chips grouped by category. Each group has a bold group label that acts as a toggle button (tap to toggle all chips in the group). Chips are large pill-shaped buttons with minimum 44px tap target height (accessibility requirement).

**Channel groups:**

| Group | Channels |
|-------|---------|
| Air Quality | TVOC (tvoc_ppb), eCO2 (eco2_ppm) |
| Particles | PM1 (pm1_ug_m3), PM2.5 (pm25_ug_m3), PM10 (pm10_ug_m3) |
| Gas Sensors | CO — resistance (co_ppb), NO2 — resistance (no2_ppb), NH3 — resistance (nh3_ppb) |
| Environment | Temperature (temperature_c), Humidity (humidity_pct) |

**Chip anatomy:** Coloured dot (channel colour from `_CHANNEL_META`) + human-readable label. Active state: filled background matching channel colour. Inactive state: outlined/ghost.

**Quick-select buttons:**
- **All** — activates all 10 chips.
- **None** — deactivates all chips (chart shows empty state with message: *"Select at least one channel above."*).
- Group label tap — toggles all chips in that group.

**Chart synchronisation:** Chip state controls Plotly trace visibility via `Plotly.restyle(chartDiv, {visible: [true/false, ...]}, [traceIndices])`. Do not re-fetch data on toggle — all 10 channels are fetched on load and traces are pre-built, just hidden/shown.

**CO/NO2/NH3 ⓘ:** Each of these three chips includes an ⓘ icon. Tooltip:
*"CO, NO2 and NH3 are measured as electrical resistance by the MICS6814 sensor — lower resistance means more gas detected. These are raw sensor readings, not calibrated gas concentrations."*

**Plotly config:** `showlegend: false` — chip UI is the legend.

---

### 3.2 Anomaly Event Overlay

A toggleable layer of vertical marker lines on the Correlations chart. Default state: **on**.

**Toggle:** A single checkbox/toggle above the chart labelled "Show detections". When off, all markers are hidden (`Plotly.restyle` to set opacity to 0, or remove traces).

**Marker specification:**

- Type: Plotly shape (vertical line) from y=0 to y=1 (axis fraction) at the inference timestamp.
- Colour by severity (DB values are `critical`, `warning`, `info`; event_type prefix used for anomaly/pattern colouring):
  - `critical` → red (`#ef4444`)
  - `warning` → amber (`#f59e0b`)
  - `info` with event_type prefix `anomaly_` or `pattern` → blue (`#3b82f6`)
  - All other `info` → grey (`#6b7280`)
- Width: 1px, dashed.

**Hover/tap interaction:** When hovering/tapping a marker, show a tooltip with:
- Inference title.
- Detection method chip (text only, no HTML in Plotly tooltips).
- Attributed source (if available): *"Attributed to: [Label] ([confidence%])"*.

**Implementation note:** Plotly shapes do not support hover natively. Use invisible scatter traces at the marker x-positions with the hover text set, and zero-opacity markers styled as vertical lines via error bars or shape annotations. Alternatively use Plotly layout shapes for visual and a parallel hidden scatter trace for hover events — this is the preferred approach.

**ⓘ on the overlay toggle:**
*"Shows when the system detected an event — align these with sensor spikes to understand what triggered each detection."*

---

### 3.3 Smarter Analysis Panel

The analysis panel lives beside or below the Correlations chart. Currently it shows basic stats when the user zooms. In Phase 6 it gains ML-aware content.

**Trigger:** User zooms on the Correlations chart (Plotly `plotly_relayout` event with `xaxis.range` set). Panel queries `/api/history/ml-context?start=<zoom_start>&end=<zoom_end>`.

**Loading state:** Show a spinner/skeleton while the request is in flight.

**Panel sections (rendered after response arrives):**

#### Events in window

List of inferences that fired within the zoom window. Each row:
- Inference title.
- Detection method chip (same visual as inference card).
- Attributed source pill (same visual as inference dialog).

If no events: *"No detections in this window."*

#### Sensor co-movement

Plain-English sentence(s) describing which active channels moved together during the window. This text is **not** generated by the frontend — it comes from a new field on the `/api/history/ml-context` response: `comovement_summary` (string). Add this field to the endpoint's response and generate it in the route handler using a simple correlation check over the sensor data for the window. Example output: *"TVOC and eCO2 rose together — consistent with a build-up of indoor air pollutants."*

**Implementation:** Compute Pearson correlation between channel pairs for the zoomed window. For pairs with |r| > 0.7, emit a sentence from a phrase template. Include at most 3 sentences (top 3 correlated pairs).

#### Peak vs baseline

For each **active** channel (currently visible via toggle chips), show:
- Channel label.
- Peak value in the window.
- EMA baseline.
- Ratio: *"[peak / baseline]× baseline"*.

Format: compact table or grid. Example row: `TVOC: peak 312 ppb — 2.6× baseline (118 ppb)`.

If baseline is null (cold-start): *"Baseline not yet available."*

#### Attribution summary

Only shown if 2 or more inferences fired in the window. Format: *"2 of 3 events attributed to Cooking."* or *"Events attributed to: Cooking (2), Combustion (1)."*

**ⓘ on each sub-section:** Each of the four sub-sections has its own ⓘ with a one-sentence explanation.

---

### 3.4 SSE Live Extension

`sensor_reading` SSE events are used to extend the Correlations chart in real time. When a `sensor_reading` event arrives:

1. Append the new timestamp to the chart's x-data for all 10 channels.
2. Append the new sensor values to each channel's y-data.
3. Call `Plotly.extendTraces()` to update the chart without a full redraw.
4. If the user has not zoomed (i.e. the chart is in the default "last N hours" view), auto-scroll the x-axis range to keep the latest data in view. If the user has zoomed, do not alter the x-axis range.

---

## Section 4 — Detections & Insights Tab

The History page's "Patterns" tab is renamed to "Detections & Insights". The tab bar label change is in `templates/history.html`. All tab content is replaced.

A new JavaScript file `static/js/detections_insights.js` handles this tab exclusively. It must not be loaded until the tab is first activated (lazy load).

### Data Loading

On tab activation:

1. Determine the active window (default: 24h ending now).
2. Fetch `/api/history/narratives?start=<start>&end=<end>`.
3. Fetch `/api/history/baselines`.
4. Render all 9 sections.

On window selector change (6h / 24h / 7d):

1. Re-fetch both endpoints with the new window parameters.
2. Re-render all sections.

SSE `inference_fired` events: when a new inference arrives, re-fetch `/api/history/narratives` for the current window and re-render the relevant sections (period summary, longest clean, attribution breakdown, fingerprint narratives, anomaly model narratives, heatmap). Do not re-render the entire tab — update section by section.

---

### Window Selector

Three toggle buttons: **6h** | **24h** | **7d**. Default: **24h**. Active button is visually highlighted. On click, update the window and re-fetch data. The window always ends at "now" (current timestamp at fetch time).

---

### Section 4.1 — Period Summary Card

**Position:** Top of tab.

**Content:** The `period_summary` string from the narratives response. 2–3 sentences, rendered as a paragraph in a card with a light background.

**ⓘ:**
*"This summary is generated from detection events and sensor trends — not a statistical average, but an interpretation of what actually happened in your space."*

---

### Section 4.2 — Trend Indicators Row

**Position:** Below period summary.

**Layout:** Horizontal scrolling row of tiles. One tile per channel. Each tile:

- Channel label (e.g. "TVOC").
- Current baseline value with unit (e.g. "118.4 ppb").
- Week-ago baseline value with unit (e.g. "105.2 ppb a week ago").
- Percentage change with direction arrow (e.g. "↑ 12.5%").
- Colour: tile border or background tint — green/amber/red per `colour` field.
- `sentence` field rendered below the tile as a caption.

**No week-ago data case:** If `week_ago_baseline` is `null`, show the tile greyed out with caption: *"Not enough history to compare."*

---

### Section 4.3 — Longest Clean Period

**Position:** Below trend indicators.

**Content:** Single stat line:
*"Your longest event-free period: 18.3 hours (Fri 22:00 → Sat 16:18)."*

Times are formatted using the browser's `toLocaleString()` with options `{weekday: 'short', hour: '2-digit', minute: '2-digit'}`.

**Zero events case:** If `longest_clean_hours` equals the full window duration (i.e. no events at all), show: *"No events detected — the entire period was clean."*

---

### Section 4.4 — Attribution Breakdown

**Position:** Below longest clean period.

**Content:**

1. Plotly donut chart. Each slice = one source. Colours from source fingerprint config. `showlegend: false`. Labels shown as text inside/beside slices. Hover: source label + count + percentage.
2. Below the chart: `dominant_source_sentence` from the narratives response (generated by backend).

**Zero events case:** Show a placeholder donut with a single grey slice labelled "No events" and caption: *"No events were detected in this period — nothing to attribute."*

**ⓘ on section:**
*"Attribution assigns detected events to likely real-world causes based on the combination of sensor readings observed."*

---

### Section 4.5 — Fingerprint Narratives

**Position:** Below attribution breakdown.

**Content:** One card per source fingerprint defined in the attribution engine config. Cards are always shown — sources with zero events show the zero-event message.

**Card anatomy:**

- Header: source emoji + label (e.g. "🍳 Cooking") + event count badge (e.g. "4 events").
- Average confidence: small muted text (e.g. "Avg. confidence: 71%"). Hidden if `event_count == 0`.
- Typical time-of-day: small muted text (e.g. "Typically: 12:00–13:00, 18:00–19:00"). Hidden if `event_count == 0`. Derived from `typical_hours` list: group consecutive hours into ranges.
- Narrative text: the `narrative` string from `fingerprint_narratives` list.

**Card ordering:** Sort by `event_count` descending (highest first). Zero-event sources go last.

**ⓘ on section:**
*"Each source fingerprint is a pattern of sensor behaviour associated with a real-world cause."*

---

### Section 4.6 — Anomaly Model Narratives

**Position:** Below fingerprint narratives.

**Content:** One card per composite multivariate model that fired at least once in the window. If no models fired: omit this section entirely (do not show an empty state).

**Card anatomy:**

- Header: model label (e.g. "Combustion Signature") + event count badge.
- Description: `description` field from the response (one sentence on what the model watches).
- Narrative: `narrative` field.

**ⓘ on section:**
*"These detections come from the ML models that watch multiple sensors together, catching events no single sensor threshold would flag."*

---

### Section 4.7 — Recurring Pattern Heatmap

**Position:** Below anomaly model narratives.

**Content:**

1. 7 × 24 colour grid. Rows = days of week (Mon–Sun). Columns = hours (0–23). Colour intensity proportional to event count in each cell (white = 0, deepening blue → max count). Render as a Plotly heatmap or a CSS grid of 168 `<div>` cells (Plotly preferred for consistent colour scaling).
2. Below the grid: a generated pattern sentence from the backend. This sentence is added as a new field `pattern_sentence` in the narratives response. Example: *"Combustion events cluster on weekday evenings (18:00–20:00)."* If no clear pattern: *"No recurring time pattern detected in this period."*

**Cell tap interaction:** Tapping a cell with count > 0 shows a popover/modal listing the events that occurred in that day/hour slot. Each event shown as: title + detection method chip + attributed source.

**X-axis labels:** Hour labels every 3 hours: 0, 3, 6, 9, 12, 15, 18, 21.
**Y-axis labels:** Day abbreviations: Mon, Tue, Wed, Thu, Fri, Sat, Sun.

**ⓘ on section:**
*"Patterns are detected when the same event type recurs at similar times across multiple days."*

---

### Section 4.8 — Normal Bands Chart

**Position:** Below heatmap.

**Content:** Time-series chart covering the same window as the selector (6h/24h/7d).

**Specification:**

- One line per channel (all 10, same toggle chip control as Correlations tab — share the chip component or re-use the same CSS class pattern).
- Shaded band per channel: ±1 EMA standard deviation (or ±[configurable factor] × EMA, whichever is currently used for anomaly detection threshold). Shading is a semi-transparent fill between upper and lower bound lines. Opacity: 0.15.
- Anomaly event overlay: same vertical markers as Correlations tab (Section 3.2). Toggle on/off with the same "Show detections" control.
- X-axis: time with readable labels.
- Y-axis: auto-scaled, no dual-axis.
- `showlegend: false` — channel identity shown by chips.

**ⓘ on chart:**
*"The shaded band shows the system's learned 'normal' range for each sensor — built up from hundreds of readings over time. Spikes outside the band are what trigger anomaly detections."*

**Data source:** Fetch via `/api/history/sensor` + `/api/history/baselines`. Compute the band bounds in the frontend: `baseline ± (baseline × threshold_factor)`. The `threshold_factor` is returned as a new field `anomaly_threshold_factor` in the `/api/history/baselines` response (a float, e.g. `0.25` meaning ±25% of baseline).

Add `anomaly_threshold_factor` to the `GET /api/history/baselines` response.

---

### Section 4.9 — Sensor Drift Flag (Conditional)

**Position:** Below normal bands chart. Only rendered if `drift_flags` is non-empty.

**Content:** One warning card per flagged channel.

**Card anatomy:**

- Warning icon + channel label.
- Message text from `drift_flags[n].message`.
- Shift percentage and direction displayed prominently.

**ⓘ on section:**
*"Baseline shift is detected by comparing the sensor's recent typical value to its value from 7 days ago."*

---

## Section 5 — Settings Reorganisation & Live Anomaly Scores

### 5.1 Route Change

The Insights Engine settings page moves from `/insights-engine` to `/settings/insights-engine`.

**Changes:**
- `mlss_monitor/routes/pages.py`: remove the `/insights-engine` route; add it at `/settings/insights-engine`. No redirect needed.
- The config sub-page remains at `/settings/insights-engine/config` (unchanged).
- `templates/base.html`: update nav to remove any top-level "Insights Engine" link. The Settings nav item already exists — verify it links to `/settings` or a settings overview page.

**Navigation update:** The Settings section in the nav must include a link to `/settings/insights-engine`. If there is already a settings landing page at `/settings`, add "Insights Engine" as a sub-item. If `/settings` is currently a redirect to `/settings/insights-engine/config`, change it to a settings overview or leave as-is — document the decision in a code comment.

---

### 5.2 Insights Engine Page — Live Anomaly Score Column

The existing anomaly models table in `templates/insights_engine.html` gains two new columns.

#### Column: "Current Score"

- Renders a mini progress bar (0–1 range).
- Bar width: `score * 100%` of the column cell width.
- Colour:
  - Green (`#22c55e`) when `score < 0.60`
  - Amber (`#f59e0b`) when `0.60 <= score < 0.75`
  - Red (`#ef4444`) when `score >= 0.75`
- During cold-start (score is `null` from SSE event): show "Learning…" text instead of bar.

**Update mechanism:** SSE `anomaly_scores` event (every 30s). On event receipt, for each model ID in `scores`, update the corresponding table row's progress bar.

**Model ID matching:** The `anomaly_scores` SSE payload covers both per-channel models and the five multivariate composite models. Per-channel model IDs are the DB column names (`tvoc_ppb`, `eco2_ppm`, `temperature_c`, `humidity_pct`, `pm1_ug_m3`, `pm25_ug_m3`, `pm10_ug_m3`, `co_ppb`, `no2_ppb`, `nh3_ppb`). Composite model IDs are: `combustion_signature`, `particle_distribution`, `ventilation_quality`, `gas_relationship`, `thermal_moisture`. These must match the model identifiers used as table row keys in the Insights Engine page.

**ⓘ on column header:**
*"The anomaly score (0–1) shows how unusual the current sensor readings are compared to what this model has learned is normal. Scores above 0.75 trigger a detection event."*

#### Enhanced "Status" Column

The current "Ready" / "Learning" status gains a third state:

- `"⚠ Elevated"`: shown when the current score for this model is >= 0.75 (but has not yet triggered a new inference in the current observation window).

**ⓘ on "Learning…" in score column:**
*"This model is still building its understanding of normal. It needs [cold_start_n - n_seen] more readings before it will start detecting anomalies."*

The values `cold_start_n` and `n_seen` must be included in the `/api/insights-engine` (or equivalent) response for each model, and also in the `anomaly_scores` SSE event (as an `n_seen` dict field alongside `scores`). Add `n_seen: {model_id: int}` to the `anomaly_scores` SSE payload.

---

## Section 6 — Bug Fixes

### Bug Fix 1 — UTC → Browser Local Time

**Root cause:** SQLite stores timestamps as plain strings without timezone suffix (e.g. `"2026-04-04 14:30:00"`). JavaScript's `new Date("2026-04-04 14:30:00")` is browser-dependent — some engines treat ambiguous strings as local time, others as UTC, causing incorrect display.

**Fix:** In `database/db_logger.py`, in `get_inferences()` and all related functions that return rows from the database, normalise the `created_at` (and any other timestamp) field by:

1. Replacing the space separator with `T`.
2. Appending the `Z` suffix.

Result: `"2026-04-04T14:30:00Z"` — unambiguously UTC ISO 8601.

**Scope:** This single change in the serialisation layer fixes timestamps across all pages and endpoints. No JavaScript changes required — `new Date("2026-04-04T14:30:00Z")` is parsed correctly by all browsers, and `toLocaleString()` then converts to the browser's local timezone automatically.

**Affected functions in `database/db_logger.py`:** `get_inferences()`, `get_inference_by_id()`, and any other function that returns `created_at` or timestamp fields from any table. Use a helper function:

```python
def _normalise_ts(ts: str | None) -> str | None:
    """Convert 'YYYY-MM-DD HH:MM:SS' to 'YYYY-MM-DDTHH:MM:SSZ'."""
    if ts is None:
        return None
    return ts.replace(" ", "T") + "Z" if ts and not ts.endswith("Z") else ts
```

Apply `_normalise_ts()` to every timestamp field before returning from any DB function.

---

### Bug Fix 2 — NH3/NO2/CO Units

**Root cause:** `_CHANNEL_META` in `mlss_monitor/inference_evidence.py` labels CO, NO2, and NH3 channels with the unit `"ppb"`. The MICS6814 sensor outputs raw electrical resistance for these channels — it does not output calibrated gas concentrations in ppb. Displaying `"ppb"` is factually incorrect and misleading.

**Fix in `mlss_monitor/inference_evidence.py`:**

Change the `unit` field for these three channels from `"ppb"` to `"kΩ"`:

```python
# Before:
"co_ppb":  {"label": "CO",  "unit": "ppb", ...},
"no2_ppb": {"label": "NO2", "unit": "ppb", ...},
"nh3_ppb": {"label": "NH3", "unit": "ppb", ...},

# After:
"co_ppb":  {"label": "CO (resistance)",  "unit": "kΩ", ...},
"no2_ppb": {"label": "NO2 (resistance)", "unit": "kΩ", ...},
"nh3_ppb": {"label": "NH3 (resistance)", "unit": "kΩ", ...},
```

**Cascade:** Anywhere these channel labels appear in the UI — inference cards, evidence chips, correlations selector, normal bands chart, trend indicators — the updated label and unit are picked up automatically via `_CHANNEL_META`. No separate template changes are required unless labels are hardcoded elsewhere. Search for hardcoded `"CO"`, `"NO2"`, `"NH3"` strings in templates and JS files and update them to include `"(resistance)"`.

**ⓘ tooltip wherever these channels appear:**
*"CO, NO2 and NH3 are measured as electrical resistance by the MICS6814 sensor — lower resistance means more gas detected. These are raw sensor readings, not calibrated gas concentrations."*

This tooltip must be added to: evidence chips in the inference dialog, channel toggle chips in the correlations selector, channel tiles in the trend indicators row.

---

## File Map

| File | Action | Notes |
|------|--------|-------|
| `mlss_monitor/narrative_engine.py` | **Create** | Pure narrative/analysis functions — no IO, no Flask imports |
| `mlss_monitor/routes/api_history.py` | **Create** | All `/api/history/*` endpoints |
| `mlss_monitor/routes/pages.py` | **Modify** | Move `/insights-engine` route to `/settings/insights-engine` |
| `mlss_monitor/routes/api_inferences.py` | **Modify** | Add `detection_method` field; add sparkline endpoint |
| `mlss_monitor/routes/api_insights.py` | **Modify** | Add SSE `anomaly_scores` event (30s interval) |
| `mlss_monitor/inference_evidence.py` | **Modify** | Fix CO/NO2/NH3 units to `kΩ`; add `(resistance)` to labels |
| `database/db_logger.py` | **Modify** | Add `_normalise_ts()` helper; apply to all timestamp fields in all DB functions |
| `templates/history.html` | **Modify** | Rename "Patterns" tab to "Detections & Insights"; replace tab content scaffold |
| `templates/insights_engine.html` | **Modify** | Add live score column and n_seen to anomaly models table |
| `templates/base.html` | **Modify** | Update nav: remove standalone "Insights Engine" link; add under Settings |
| `static/js/dashboard.js` | **Modify** | Inference dialog: add detection chip, attribution badge, sparkline, evidence ⓘ |
| `static/js/history.js` | **Modify** | Channel toggles, anomaly overlay, smarter analysis panel, SSE integration |
| `static/js/detections_insights.js` | **Create** | New tab JS: fetch narratives, render all 9 sections, SSE updates |
| `static/js/charts_correlation.js` | **Modify** | Full 10-channel support, toggle chips component, anomaly overlay markers |
| `tests/test_narrative_engine.py` | **Create** | Unit tests for all 7 narrative engine functions |
| `tests/test_api_history.py` | **Create** | Unit tests for all 4 new history endpoints |

---

## Out of Scope — Phase 7

The following items are explicitly deferred:

- `UserAnnotationSource` DataSource
- Mini charts inside the inference list (not the dialog)
- Per-channel anomaly model tuning UI
- Google Coral / Layer B ML

---

## Implementation-Phase Extras (Not in This Spec)

Tracked as tasks, not blocking Phase 6:

- README documentation pass covering the inference engine (deterministic rules + River ML), the FeatureVector concept and its data flow, and the path from sensor readings through features → detection → attribution → inference card display.
- Add `.claude/` to `.gitignore` — this directory should not be committed to the public repository.

---

## Dependency Summary

Phase 6 depends on Phase 5 having delivered:

- `attribution_source`, `attribution_confidence`, `runner_up_source`, `runner_up_confidence` fields on inference records (populated by the attribution engine).
- The five composite River models (`combustion_signature`, `particle_distribution`, `ventilation_quality`, `gas_relationship`, `thermal_moisture`) wired into `DetectionEngine` and producing `event_type` values matching the `ML_EVENT_TYPES` set above.
- `sensor_snapshot` evidence field on inference records (for sparkline `triggering_channels` derivation and ratio computation).
- The `AnomalyDetector` instance being accessible from Flask route handlers (for baselines and live scores).

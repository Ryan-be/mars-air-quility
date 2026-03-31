# Smart Inference Engine — Design Spec
_Date: 2026-03-31_

## Overview

Replace the current threshold-only if/else inference engine with a four-layer pipeline that combines declarative rules, streaming anomaly detection, and multi-sensor source attribution. The refactor also introduces a unified data source abstraction, two-tier storage for second-resolution attribution, and new UI surfaces for configuration and insight display.

This is a large, iterative refactor. The design is structured so that each phase delivers working, testable value independently.

---

## Goals

- Replace hard-coded if/else threshold logic with YAML-defined declarative rules (maintainable without touching Python)
- Add streaming anomaly detection that works with sparse data today and improves automatically over 2 years
- Add source attribution: identify *what* is causing a reading, not just *that* a threshold was crossed
- Abstract all data sources behind a common interface so new sensors and external feeds are additive
- Two-tier storage: 1-second hot tier (in-memory, last 60 minutes) for attribution; 60-second cold tier (existing SQLite) for long-term baselines
- All processing local — no cloud compute, no external API calls for inference
- Lay groundwork for Layer B: Google Coral / online ML in a future phase
- UI to configure rules and fingerprints, and to display attribution results intuitively

## Non-Goals (this phase)

- ML-based adaptive thresholds (Layer B — future)
- User annotation as a data source (future D — architecture supports it, not implemented now)
- Migration to a time-series database (InfluxDB etc — revisit if SQLite becomes a bottleneck)
- Google Coral integration (future)

---

## Hardware Context

- Raspberry Pi 4, 4 GB RAM
- Sensors: SGP30 (TVOC, eCO2), AHT20 (temp, humidity), MICS6814 (CO, NO2, NH3), particulate sensor (PM2.5)
- External: weather API data (purged after 7 days)
- Future: Pi 5 + Google Coral M.2 (for Layer B)
- All inference runs on-device; no network dependency

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Data Source Layer                        │
│   DataSource ABC → SGP30, AHT20, MICS6814, Weather, PM      │
│         UserAnnotationSource ← add later (Phase 6)          │
└─────────────────────────┬────────────────────────────────────┘
                          │ NormalisedReading
┌─────────────────────────▼────────────────────────────────────┐
│                     Storage Layer                            │
│  Hot tier:  deque (3600 × NormalisedReading, ~360 KB RAM)   │
│             last 60 minutes at 1-second resolution           │
│  Cold tier: existing SQLite at 60-second resolution          │
│             (schema unchanged, 2-year retention)             │
└──────────────┬──────────────────────────┬────────────────────┘
               │ hot tier (last 60 min)   │ cold tier (history)
┌──────────────▼──────────────────────────▼────────────────────┐
│                   Feature Extraction                         │
│  Per-sensor: current, baseline, slope_1m/5m/30m,            │
│              elevated_minutes, peak_ratio, is_declining,     │
│              decay_rate, pulse_detected                      │
│  Cross-sensor: pm25_correlated_with_tvoc,                    │
│                nh3_lag_behind_tvoc_seconds                   │
└──────────────────────────┬───────────────────────────────────┘
                           │ FeatureVector
┌──────────────────────────▼───────────────────────────────────┐
│                    Detection Layer                           │
│  ┌─────────────────────┐   ┌──────────────────────────────┐  │
│  │    rule-engine      │   │  river HalfSpaceTrees        │  │
│  │  YAML rules         │   │  streaming anomaly detection │  │
│  │  (declarative C)    │   │  (statistical D)             │  │
│  └──────────┬──────────┘   └──────────────┬───────────────┘  │
└─────────────┼────────────────────────────┼────────────────────┘
              │ rule events                │ anomaly events
┌─────────────▼────────────────────────────▼────────────────────┐
│                   Attribution Layer                           │
│  YAML source fingerprints → confidence scoring               │
│  sensor_score × 0.6 + temporal_score × 0.4                   │
│  Returns top match + runner-up if scores within 0.15         │
│  ← replace scoring with TFLite model here (Layer B)          │
└──────────────────────────┬────────────────────────────────────┘
                           │ InferenceResult
┌──────────────────────────▼────────────────────────────────────┐
│                    Output Layer                               │
│   save_inference() — existing DB schema, no changes          │
└───────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Data Source Abstraction

### Purpose
Decouple the inference pipeline from specific sensor implementations. Every source produces a `NormalisedReading`. Adding a new sensor = one new class, no changes downstream.

### Interface

```python
@dataclass
class NormalisedReading:
    timestamp: datetime
    source: str                  # e.g. "sgp30", "weather_api"
    tvoc_ppb:        float | None
    eco2_ppm:        float | None
    temperature_c:   float | None
    humidity_pct:    float | None
    pm25_ug_m3:      float | None
    co_ppb:          float | None
    no2_ppb:         float | None
    nh3_ppb:         float | None
    # extend here; None means sensor not present / no data yet

class DataSource(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def get_latest(self) -> NormalisedReading: ...
```

### Initial implementations
- `SGP30Source` — TVOC, eCO2
- `AHT20Source` — temperature, humidity
- `MICS6814Source` — CO, NO2, NH3
- `ParticulateSource` — PM2.5
- `WeatherAPISource` — external weather (temp, humidity, pressure)

### Future extension (Phase 6)
- `UserAnnotationSource` — user-entered context ("just painted", "cooking") feeds into attribution as soft evidence

---

## Layer 2 — Storage (Two-Tier)

### Hot Tier
- `collections.deque(maxlen=3600)` — in-memory ring buffer
- Populated at 1-second resolution by the sensor read loop
- Holds last 60 minutes of `NormalisedReading` objects
- Memory: ~360 KB (negligible on Pi 4)
- No persistence — rebuilt from cold tier on startup (last 60 min of 60s data, upsampled as placeholder until 1s readings fill the buffer)
- Used exclusively by Feature Extraction for attribution

### Cold Tier
- Existing SQLite schema, unchanged
- Written every 60 seconds (one reading per minute, as today)
- Used for: anomaly baselines (river training), hourly/daily summaries, long-term pattern detection
- Retention: 2 years for sensor data, 7 days for external weather

### Write flow
```
sensor read (every 1s) → NormalisedReading → hot tier deque
                                           → every 60s: cold tier SQLite
```

---

## Layer 3 — Feature Extraction

Consumes the hot tier (last 60 minutes at 1s) and produces a `FeatureVector` before each detection cycle.

### FeatureVector fields

```python
@dataclass
class FeatureVector:
    timestamp: datetime

    # Per sensor (shown for TVOC; same pattern for eco2, temp, humidity, pm25, co, no2, nh3)
    tvoc_current: float | None
    tvoc_baseline: float | None          # rolling 24hr median from cold tier
    tvoc_slope_1m: float | None          # ppb/min
    tvoc_slope_5m: float | None
    tvoc_slope_30m: float | None
    tvoc_elevated_minutes: float | None  # continuous time above baseline
    tvoc_peak_ratio: float | None        # current / baseline
    tvoc_is_declining: bool | None
    tvoc_decay_rate: float | None        # ppb/min (negative when declining)
    tvoc_pulse_detected: bool | None     # spike + decay pattern in last 30min

    # Cross-sensor
    nh3_lag_behind_tvoc_seconds: float | None   # None if no correlated spike
    pm25_correlated_with_tvoc: bool | None
    co_correlated_with_tvoc: bool | None

    # Derived
    vpd_kpa: float | None
```

Fields are `None` when there is insufficient data (e.g., MICS6814 has only hours of data). Detection rules and fingerprint scoring skip `None` fields gracefully — they neither confirm nor deny.

---

## Layer 4 — Detection Layer

### rule-engine (declarative rules — C)

Library: `rule-engine` (pip install rule-engine, ~no transitive deps)

Rules are string expressions evaluated against the `FeatureVector`. Stored in `config/rules.yaml`, loaded at startup, reloadable at runtime.

#### Rule schema
```yaml
rules:
  - id: tvoc_spike
    expression: "tvoc_peak_ratio > 1.5 and tvoc_current > 250"
    event_type: tvoc_spike
    severity: warning
    confidence: 0.8
    title_template: "TVOC spike detected ({tvoc_current:.0f} ppb)"
    description_template: >
      TVOC has risen to {tvoc_current:.0f} ppb, {tvoc_peak_ratio:.1f}×
      above the {tvoc_baseline:.0f} ppb baseline over the last
      {tvoc_elevated_minutes:.0f} minutes.
    action: "Identify and ventilate the source."

  - id: eco2_danger
    expression: "eco2_current >= 2000"
    event_type: eco2_danger
    severity: critical
    confidence: 0.95
    title_template: "Dangerous CO₂ level ({eco2_current:.0f} ppm)"
    description_template: >
      CO₂ has reached {eco2_current:.0f} ppm, above the 2000 ppm danger
      threshold. Cognitive impairment is likely at this concentration.
    action: "Ventilate immediately."

  - id: mould_risk
    expression: "humidity_elevated_minutes > 240 and humidity_current > 70 and temperature_c > 20"
    event_type: mould_risk
    severity: warning
    confidence: 0.75
    title_template: "Mould risk conditions ({humidity_current:.0f}% RH for {humidity_elevated_minutes:.0f} min)"
    description_template: >
      Humidity has been above 70% for {humidity_elevated_minutes:.0f} minutes
      at {temperature_c:.1f}°C. Sustained conditions favour mould growth.
    action: "Reduce humidity. Check for poor air circulation or moisture sources."
```

All threshold-based detectors from `inference_engine.py` are migrated to this format (tvoc_spike, eco2_danger, eco2_elevated, temp_high, temp_low, humidity_high, humidity_low, vpd_low, vpd_high, mould_risk, correlated_pollution, rapid_temp_change, rapid_humidity_change, sustained_poor_air). No threshold detection logic lives in Python.

Summary-type functions (`_hourly_summary`, `_daily_summary`, `_daily_pattern`, `_overnight_buildup`) are retained as Python — they compute composite scores and generate narrative text that cannot be expressed as boolean expressions. They are refactored to consume the `FeatureVector` rather than raw DB rows.

### river (anomaly detection — D)

Library: `river` (pip install river)
Algorithm: `HalfSpaceTrees` per sensor channel

- One model instance per sensor channel (tvoc, eco2, temperature, humidity, pm25, co, no2, nh3)
- Updates with every cold-tier write (every 60s)
- Outputs anomaly score 0.0–1.0
- Score > configurable threshold (default 0.7) → generates anomaly event
- Models persisted to disk (pickle) so they survive restarts and accumulate learning
- Graceful cold-start: scores suppressed until model has seen N readings (configurable, default 1440 = 1 day)

```yaml
# config/anomaly.yaml
anomaly:
  algorithm: half_space_trees
  score_threshold: 0.7
  cold_start_readings: 1440
  channels:
    - tvoc_ppb
    - eco2_ppm
    - temperature_c
    - humidity_pct
    - pm25_ug_m3
    - co_ppb      # sparse until MICS6814 has enough history
    - no2_ppb
    - nh3_ppb
```

---

## Layer 5 — Attribution Layer

### Purpose
Given detection events and the current `FeatureVector`, identify the most probable pollution source and attach it to the inference as human-readable explanation.

### Fingerprint schema

Stored in `config/fingerprints.yaml`. Each fingerprint defines expected sensor states and temporal profile.

```yaml
sources:

  - id: biological_offgas
    label: "Biological off-gassing"
    description: "Human flatulence or similar biological VOC source"
    sensors:
      tvoc:  high          # high | elevated | normal | low | absent
      nh3:   high
      eco2:  slight_rise
      pm25:  normal
      co:    absent
    temporal:
      rise_rate: fast              # fast | moderate | slow
      sustain_max_minutes: 10
      decay_rate: fast
      nh3_follows_tvoc: true
      nh3_max_lag_seconds: 120
    confidence_floor: 0.65

  - id: chemical_offgassing
    label: "Chemical off-gassing"
    description: "Cleaning products, paint, adhesives, air fresheners, new furniture"
    sensors:
      tvoc:  elevated
      nh3:   normal
      pm25:  normal
      eco2:  normal
    temporal:
      rise_rate: moderate
      sustain_min_minutes: 30
      decay_rate: slow
      nh3_follows_tvoc: false
    confidence_floor: 0.6

  - id: cooking
    label: "Cooking activity"
    sensors:
      tvoc:  elevated
      pm25:  elevated
      temperature: rising
    temporal:
      rise_rate: moderate
      pm25_correlated_with_tvoc: true
      sustain_min_minutes: 15
    confidence_floor: 0.55

  - id: combustion
    label: "Combustion"
    description: "Candle, open fire, smoking"
    sensors:
      tvoc:  high
      co:    elevated
      pm25:  high
      no2:   elevated
    temporal:
      rise_rate: fast
      pm25_correlated_with_tvoc: true
    confidence_floor: 0.8

  - id: external_pollution
    label: "External pollution ingress"
    description: "Outdoor air quality event entering via ventilation"
    sensors:
      pm25:  high
      tvoc:  normal
      co:    normal
    temporal:
      rise_rate: slow
      sustain_min_minutes: 60
    confidence_floor: 0.55
```

### Scoring

```
sensor_score   = fraction of non-None sensor fields matching expected state
temporal_score = fraction of non-None temporal fields matching observed profile
confidence     = (sensor_score × 0.6) + (temporal_score × 0.4)
```

Output:
- Primary attribution if `confidence >= confidence_floor`
- Runner-up surfaced if within 0.15 of primary (ambiguous sources shown, not hidden)
- If no source clears its floor: "Unknown source" with highest-scoring candidates listed

### Layer B extension point
When Coral integration is implemented, the YAML scoring function is replaced by a TFLite classifier that takes the same `FeatureVector` as input and returns `(source_id, confidence)`. The fingerprints remain as fallback and human-readable reference.

---

## Narrative Generation

Dynamic human-readable text is still generated for every inference. It works in three parts that are concatenated into the final `description` field.

### Part 1 — Rule narrative (what happened)

Each rule in `rules.yaml` carries title and description templates. Slots are filled from the `FeatureVector` at fire time:

```yaml
- id: tvoc_spike
  expression: "tvoc_peak_ratio > 1.5 and tvoc_current > 250"
  title_template: "TVOC spike detected ({tvoc_current:.0f} ppb)"
  description_template: >
    TVOC has risen to {tvoc_current:.0f} ppb,
    {tvoc_peak_ratio:.1f}× above the {tvoc_baseline:.0f} ppb baseline
    over the last {tvoc_elevated_minutes:.0f} minutes.
  action: "Identify and ventilate the source."
```

### Part 2 — Attribution narrative (what's causing it)

Each fingerprint in `fingerprints.yaml` carries its own description template. The attribution engine also auto-generates ruling-out clauses from sensors that were expected absent/normal but confirmed so by the FeatureVector:

```yaml
- id: chemical_offgassing
  description_template: >
    TVOC is elevated ({tvoc_current:.0f} ppb) but PM2.5 and eCO₂ are normal.
    This is typical of volatile organic sources that don't produce particles
    or CO₂: {examples}.
  examples: "cleaning products, air fresheners, paint, adhesives, new furniture, cosmetics"
  action_template: "Ventilate the room. {persistence_note}"
```

Auto-generated ruling-out clause example: if `pm25: absent` in the fingerprint and PM2.5 is confirmed normal, the engine appends "No PM2.5 rise rules out combustion."

### Part 3 — Summary functions (composite narrative, Python)

`_hourly_summary`, `_daily_summary`, `_daily_pattern`, and `_overnight_buildup` continue to generate multi-sentence composite descriptions in Python, exactly as today. They are refactored to consume `FeatureVector` rather than raw DB rows, but their narrative output is unchanged.

### Final output

```
title       = rule title_template (filled)
description = rule description_template (filled)
            + attribution description_template (filled)
            + ruling-out clauses (auto-generated from evidence)
action      = attribution action_template (filled) or rule action fallback
evidence    = FeatureVector values + attribution scores (dict, unchanged schema)
```

The resulting inference descriptions are at least as rich as today — typically richer, since the attribution layer adds cross-sensor reasoning that was previously absent or hardcoded per-detector.

---

## Output Layer

`save_inference()` signature is unchanged. The attribution result is embedded in the `description` and `evidence` dict fields that already exist:

```python
save_inference(
    event_type="tvoc_spike",
    severity="warning",
    title="TVOC spike — biological off-gassing (65% confidence)",
    description="TVOC spiked to 708 ppb with NH3 following 45 seconds later. "
                "Pattern matches biological off-gassing. No PM2.5 rise rules out combustion.",
    action="Ventilate the room. Source is short-lived.",
    evidence={
        "tvoc_peak": 708,
        "nh3_peak": 12,
        "nh3_lag_seconds": 45,
        "attribution": "biological_offgas",
        "attribution_confidence": 0.65,
        "runner_up": "chemical_offgassing",
        "runner_up_confidence": 0.41,
    },
    confidence=0.65,
    ...
)
```

---

## Configuration UI

All new settings live under `/settings/insights-engine/` to keep them grouped separately from existing system settings.

### Rule manager (`/settings/insights-engine/rules`)
- List all rules from `rules.yaml` with current enabled/disabled state
- Edit expression, severity, confidence, and narrative templates inline
- Add new rule (expression builder with live validation against a sample FeatureVector)
- Disable/enable without deleting
- Changes write to `rules.yaml` and trigger hot-reload (no restart)

### Fingerprint manager (`/settings/insights-engine/fingerprints`)
- List all source fingerprints
- Add/edit fingerprint (sensor states, temporal profile, confidence floor, narrative templates)
- Preview: given current live readings, what would this fingerprint score?
- Changes write to `fingerprints.yaml` and take effect immediately

### Anomaly settings (`/settings/insights-engine/anomaly`)
- Per-channel score threshold slider
- Cold-start reading count
- View current anomaly score per channel (live)
- Model reset per channel (if sensor was faulty for a period)

### Data source manager (`/settings/insights-engine/sources`)
- List all registered `DataSource` implementations with status (active/error/no data)
- Enable/disable per source
- Per-source last-reading timestamp and health indicator

---

## Display UI

### Inference card enhancement
Current inference cards show title + description. Add:
- Attribution badge: source label + confidence percentage
- Runner-up shown in muted text if ambiguous
- Evidence expandable section showing the sensor pattern that triggered attribution
- Temporal sparkline: last 30 minutes of the triggering sensor(s)

### Live anomaly panel
- Per-channel anomaly score gauge (0–1)
- Scores above threshold highlighted
- "Learning" indicator during cold-start period

### Source attribution history
- Timeline view: which sources were attributed over the last 24h
- Filter by source type
- Click through to full inference detail

---

## Migration Strategy

The existing `inference_engine.py` detectors map directly to `rules.yaml` entries. No inference logic is lost — it is transcribed from Python into YAML expressions. The migration can be validated by running both engines in parallel and comparing outputs before switching over.

---

## Phased Implementation Plan

This refactor is too large for a single session. Each phase is independently deployable and testable.

### Phase 1 — Data Source Abstraction + Hot Tier
_Touches: sensor read loop, new `data_sources/` module, storage layer_

- Define `DataSource` ABC and `NormalisedReading` dataclass
- Wrap existing sensors in `DataSource` implementations
- Implement hot-tier ring buffer (deque, 3600 entries)
- Wire 1-second sensor loop into hot tier; 60-second write to cold tier unchanged
- Tests: each DataSource returns valid NormalisedReading; hot tier fills correctly

### Phase 2 — Feature Extraction
_Touches: new `feature_extractor.py`, no changes to detection or DB_

- Implement `FeatureVector` dataclass
- Implement `FeatureExtractor` that reads hot tier and cold tier baselines
- Per-sensor features: current, baseline, slopes (1m/5m/30m), elevated_minutes, peak_ratio, pulse_detected
- Cross-sensor features: nh3_lag_behind_tvoc, pm25_correlated_with_tvoc
- Tests: feature values are correct for synthetic sensor sequences (unit tests with fixture data)

### Phase 3 — Detection Layer (rule-engine + river)
_Touches: replace `inference_engine.py` internals; rules.yaml introduced_

- Transcribe all 14 threshold-based detectors into `config/rules.yaml`
- Retain summary functions (_hourly_summary, _daily_summary, _daily_pattern, _overnight_buildup) as Python, refactored to consume FeatureVector
- Implement rule loader and evaluator using `rule-engine`
- Implement `river` anomaly detector with per-channel HalfSpaceTrees
- Model persistence (pickle on each update, load on startup)
- Run both old and new engines in parallel for one week; compare outputs
- Remove old inference_engine.py once parity confirmed
- Tests: each rule fires correctly on synthetic FeatureVectors; river model serialises/deserialises

### Phase 4 — Attribution Layer
_Touches: new `attribution/` module, fingerprints.yaml introduced, inference output enriched_

- Define fingerprint YAML schema and loader
- Implement sensor scorer and temporal scorer
- Implement confidence combiner and runner-up logic
- Enrich `save_inference()` calls with attribution evidence
- Ship initial fingerprint library (biological, chemical, cooking, combustion, external pollution)
- Tests: each fingerprint scores correctly on synthetic FeatureVectors; ambiguous cases surface runner-up

### Phase 5 — Configuration UI
_Touches: new Flask routes, new templates, YAML read/write_

- Rule manager: list, edit, add, enable/disable rules; hot-reload
- Fingerprint manager: list, edit, add fingerprints; live score preview
- Anomaly settings: per-channel threshold, model reset
- Data source manager: status, enable/disable

### Phase 6 — Display UI + UserAnnotationSource
_Touches: existing inference card templates, new timeline view_

- Inference card: attribution badge, evidence expandable, temporal sparkline
- Live anomaly score panel
- Attribution history timeline
- `UserAnnotationSource` DataSource implementation (user context feeds attribution)

### Phase 7 — Layer B preparation (future)
_Not in scope now — documented for continuity_

- Google Coral / TFLite classifier replaces YAML fingerprint scoring in Attribution Layer
- `river` online models upgraded to learned classifiers
- Pi 5 + M.2 HAT+ assumed for this phase

---

## Library Summary

| Library | Purpose | Why |
|---------|---------|-----|
| `rule-engine` | Declarative threshold rules | Lightweight, rules as storable strings, no heavy deps |
| `river` | Streaming anomaly detection | Online learning, works with 1 week of data, improves over 2 years, 100% local, Pi 4 friendly |
| `PyYAML` | Rule + fingerprint definitions | Human-readable config files, likely already a transitive dep |

No other new libraries. All processing local. No cloud dependencies.

---

## Open Questions (resolved)

| Question | Decision |
|----------|----------|
| Online vs local | 100% local. "Online" = streaming/incremental, not internet. |
| Pi 4 vs Pi 5 | Pi 4 4GB sufficient now. Upgrade to Pi 5 when adding Coral (Layer B). |
| Storage at 1s resolution | Two-tier: 1s in-memory hot tier (60 min), 60s cold tier (SQLite, 2 years). |
| User annotations as source | Architecture supports it; implement in Phase 6. |
| Attribution ambiguity | Surface runner-up when within 0.15 of primary. Never silently pick one. |
| Rule engine for attribution | Fingerprint YAML + scoring (not experta). Simpler, transparent, same outcome. |

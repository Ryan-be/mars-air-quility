# Phase 5 — Multivariate Detection + Actionable Inference Cards

**Date:** 2026-04-04
**Branch:** claude/zealous-hugle / feature/phase3-detection-layer

---

## Goal

Two tightly coupled improvements:

1. **Multivariate anomaly detection** — five composite models that learn correlations
   *between* sensor channels rather than scoring each in isolation. Catches events that
   no single-channel threshold or per-channel anomaly score would flag.

2. **Actionable inference cards** — every saved inference carries enough structured
   evidence that the UI can render a real-world description (actual values, ratio to
   baseline, trend direction, attributed source) without any logic beyond display.
   The backend owns the interpretation; the UI only formats it.

---

## Design Principles

- **Backend-heavy** — all interpretation, ratio calculation, trend labelling, and
  human-readable text generation happens in Python. The JS layer renders pre-computed
  fields; it never calculates.
- **Progressive enrichment** — existing threshold-rule inferences are enriched with
  the same structured evidence schema as anomaly inferences so the UI is uniform.
- **Additive, not breaking** — new models sit alongside existing ones; no schema
  migrations required for the inferences table (evidence is a JSON blob).

---

## Subsystem A — MultivarAnomalyDetector

### A1. Config (`config/multivar_anomaly.yaml`)

```yaml
multivar_anomaly:
  threshold: 0.75
  cold_start_readings: 500
  models:
    - id: combustion_signature
      label: "Combustion signature"
      description: "Detects combustion events (candles, cooking, wood-burning) as a
        joint pattern across CO, NO2, PM2.5 and PM10. Catches events where all four
        rise moderately together — a pattern missed by individual thresholds."
      channels:
        - co_current
        - no2_current
        - pm25_current
        - pm10_current

    - id: particle_distribution
      label: "Particle size distribution"
      description: "Monitors the ratio relationship between PM1, PM2.5 and PM10.
        PM1≈PM2.5 (ultrafine) indicates combustion or smoke; PM10>>PM2.5 indicates
        coarse dust or pollen. Flags when the size distribution becomes unusual."
      channels:
        - pm1_current
        - pm25_current
        - pm10_current

    - id: ventilation_quality
      label: "Ventilation quality"
      description: "Tracks the joint build-up of eCO2, TVOC and NH3. All three rise
        together in a poorly-ventilated space even when no single channel breaches its
        threshold. Flags stale-air conditions earlier than rule-based detection."
      channels:
        - eco2_current
        - tvoc_current
        - nh3_current

    - id: gas_relationship
      label: "Gas sensor relationship"
      description: "Monitors the normal correlation structure of CO, NO2 and NH3 from
        the MICS6814. When one channel breaks its expected relationship with the others
        this may indicate sensor drift, sensor failure, or a genuinely unusual gas
        mixture requiring investigation."
      channels:
        - co_current
        - no2_current
        - nh3_current

    - id: thermal_moisture
      label: "Thermal-moisture stress"
      description: "Scores temperature, humidity and VPD together. Catches comfort-zone
        stress events that no single metric shows clearly — e.g. moderate humidity
        combined with high temperature pushing VPD into plant-stress territory, or
        HVAC failure showing as a correlated drift in all three."
      channels:
        - temperature_current
        - humidity_current
        - vpd_kpa
```

### A2. Class (`mlss_monitor/multivar_anomaly_detector.py`)

- Loads `multivar_anomaly.yaml`; creates one `HalfSpaceTrees(n_trees=10, height=8,
  window_size=150, seed=42)` per model.
- Pickle persistence in `model_dir` as `multivar_{model_id}.pkl`; same param-mismatch
  auto-discard logic as `AnomalyDetector`.
- `learn_and_score(fv: FeatureVector) -> dict[str, float | None]` — returns
  `{model_id: score}`. Returns `None` for a model during its cold-start period.
- `anomalous_models(scores) -> list[str]` — models whose score >= threshold.
- `bootstrap(channel_data)` — same pattern as `AnomalyDetector.bootstrap`; feeds the
  cross-channel reading vectors from historical data.
- `_save_models()` / `_load_models()` — identical pattern to existing detector.

### A3. DetectionEngine integration

`DetectionEngine.__init__` loads `MultivarAnomalyDetector` from the multivar config
path (new constructor arg, optional with a sensible default path).

`DetectionEngine.run(fv)` — after the per-channel anomaly block:

```
scores = self._multivar_detector.learn_and_score(fv)
for model_id in self._multivar_detector.anomalous_models(scores):
    event_type = f"anomaly_{model_id}"
    # dedupe 1 hour
    # build rich evidence (see Subsystem B)
    # save_inference(...)
```

### A4. `init_db.py` CHECK constraint

Add the five new event types to the `inferences.event_type` CHECK constraint:
`anomaly_combustion_signature`, `anomaly_particle_distribution`,
`anomaly_ventilation_quality`, `anomaly_gas_relationship`, `anomaly_thermal_moisture`.

---

## Subsystem B — Actionable Inference Evidence

### B1. EMA baseline tracking in `AnomalyDetector`

Add `self._ema: dict[str, float]` initialised to `{}`. In `learn_and_score`, after
scoring each channel, update:

```python
alpha = 0.05  # ~20-reading half-life
self._ema[ch] = alpha * value + (1 - alpha) * self._ema.get(ch, value)
```

`AnomalyDetector` exposes `baseline(ch) -> float | None` returning `self._ema.get(ch)`.
`MultivarAnomalyDetector` does the same per model dimension.

### B2. Rich evidence builder (`mlss_monitor/inference_evidence.py`)

New module — pure functions, no IO:

```python
def build_anomaly_evidence(
    fv: FeatureVector,
    channel: str,
    score: float,
    baseline: float | None,
    trend_field: str | None = None,
) -> dict:
    """Returns a structured evidence dict for a single-channel anomaly."""

def build_multivar_evidence(
    fv: FeatureVector,
    model_id: str,
    model_label: str,
    channels: list[str],
    score: float,
    baselines: dict[str, float | None],
) -> dict:
    """Returns a structured evidence dict for a composite anomaly."""

def enrich_rule_evidence(
    fv: FeatureVector,
    base_evidence: dict,
    relevant_channels: list[str],
    baselines: dict[str, float | None],
) -> dict:
    """Adds sensor snapshot to existing threshold-rule evidence."""
```

The evidence schema for all inference types:

```json
{
  "sensor_snapshot": [
    {
      "channel": "tvoc_ppb",
      "label": "TVOC",
      "value": 487.0,
      "unit": "ppb",
      "baseline": 152.0,
      "ratio": 3.2,
      "trend": "rising"
    }
  ],
  "anomaly_score": 0.84,
  "model_id": "combustion_signature",
  "_thresholds": { ... }
}
```

`trend` is derived from fv slope fields:
- `slope_1m > 0.5 unit/min` → `"rising"`
- `slope_1m < -0.5 unit/min` → `"falling"`
- else → `"stable"`

The threshold is per-channel (e.g., 0.5 ppb/min for TVOC, 0.05 °C/min for temperature)
and defined as a constant dict in `inference_evidence.py`.

### B3. Richer human-readable description and action

`inference_evidence.py` also exports:

```python
def anomaly_description(snapshot: list[dict], model_label: str | None = None) -> str
def anomaly_action(snapshot: list[dict], model_label: str | None = None) -> str
```

For single-channel: _"TVOC at 487 ppb — 3.2× your typical 152 ppb, and rising."_
For multivar combustion: _"A combustion-pattern anomaly was detected: CO, NO2, PM2.5
and PM10 moved together in an unusual way. The most elevated dimension was PM2.5 at
2.8× baseline. This pattern is consistent with candles, cooking, or wood-burning."_

Action is contextual per model:
- combustion → "Identify and ventilate the source. Check for open flames or smouldering."
- particle_distribution → "Check for unusual dust sources or outdoor pollution ingress."
- ventilation_quality → "Open a window or run a fan. CO2/TVOC/NH3 are building up together."
- gas_relationship → "Inspect MICS6814 sensor. One gas reading has broken its normal relationship with the others."
- thermal_moisture → "Check heating/cooling system. Temperature, humidity and VPD are stressed together."

### B4. JS evidence renderer update (`static/js/dashboard.js`)

The JS renders the pre-computed `sensor_snapshot` array. No calculation.

For each entry in `sensor_snapshot`:
- Label + value + unit
- Ratio badge: `3.2× normal` coloured green (<1.5×), amber (1.5–3×), red (>3×)
- Trend arrow: ↑ rising / ↓ falling / → stable

This replaces the current generic key-value loop for the `evidence` section of the
inference dialog. The `_thresholds` section rendering is unchanged.

---

## File Map

| File | Action |
|------|--------|
| `config/multivar_anomaly.yaml` | **Create** — 5 model definitions |
| `mlss_monitor/multivar_anomaly_detector.py` | **Create** — composite detector |
| `mlss_monitor/inference_evidence.py` | **Create** — evidence builder, description templates |
| `mlss_monitor/anomaly_detector.py` | **Modify** — add EMA tracking + `baseline()` |
| `mlss_monitor/detection_engine.py` | **Modify** — wire MultivarAnomalyDetector; use inference_evidence builder for all save_inference calls |
| `database/init_db.py` | **Modify** — add 5 new event types to CHECK constraint |
| `templates/dashboard.html` (or static JS) | **Modify** — render `sensor_snapshot` array instead of generic k-v |
| `static/js/dashboard.js` | **Modify** — structured evidence renderer |
| `tests/test_multivar_anomaly_detector.py` | **Create** — unit tests |
| `tests/test_inference_evidence.py` | **Create** — unit tests for builders |

---

## Out of Scope (Phase 5)

- Outdoor wind speed in `ventilation_quality` model (requires adding weather data to
  FeatureVector — deferred to Phase 6)
- Mini charts in inference dialogs
- Per-channel anomaly model tuning UI

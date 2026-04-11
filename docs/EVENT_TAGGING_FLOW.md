# Mars Air Quality: Event Tagging Flow - Complete Analysis

## Summary

Event tagging in mars-air-quality is a sophisticated flow that allows users to mark time ranges on the **Correlations tab**, capture rich sensor data, extract temporal features, generate inferences, and feed tagged data to ML models for attribution learning. The system learns sensor patterns associated with real-world pollution sources to improve future automatic detection.

---

## 1. User-Facing Event Tagging: The Correlations Tab

### 1.1 UI Entry Point

**File**: [templates/history.html](../templates/history.html)#L213)

The Correlations tab provides:
- **Time series brush chart**: User drags to select a time window of interest
- **Channel selection chips**: Toggle which sensor channels to display
- **Analysis panels**: Dynamically updated with correlation analysis

When a time range is selected, the system displays:
- **Peak vs baseline**: Shows max value in selection vs learned baseline for each channel
- **Sensor co-movement**: Which channels moved together (Pearson r > 0.7)
- **Source attribution suggestion**: ML-predicted cause (combustion, cooking, etc.)
- **Range tagging section**: User selects a tag from dropdown and clicks "Save tagged event"

### 1.2 JavaScript Handler

**File**: [static/js/history.js](../static/js/history.js)#L59-L99)

When the user clicks "Save tagged event":
```javascript
const resp = await fetch('/api/history/range-tag', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ start: selected.start, end: selected.end, tag }),
});
```

The handler:
1. Validates that a time range and tag are selected
2. Constructs start/end timestamps from the brushed selection
3. Sends `POST /api/history/range-tag` with the selected tag

---

## 2. Backend Processing Pipeline: `/api/history/range-tag`

### 2.1 Endpoint Handler

**File**: [mlss_monitor/routes/api_history.py](../mlss_monitor/routes/api_history.py)#L235-L315)

The endpoint performs these steps in sequence:

#### Step 1: Build Feature Vector from Sensor Data

```python
fv_result = _build_feature_vector(start, end)
```

This:
- Queries **sensor_data** table (downsampled hourly data, 10 sensor channels)
- Queries **hot_tier** table (1-second resolution, 60-min retention)
- **Merges** both sources by timestamp (if same timestamp exists, sensor_data takes precedence)
- Converts rows to `NormalisedReading` objects

### 2.2 Data & Readings Collection

**Function**: `_build_range_readings(start, end)` → `list[NormalisedReading]`

**NormalisedReading fields** (from [mlss_monitor/data_sources/base.py](../mlss_monitor/data_sources/base.py))):
```
timestamp, source, tvoc_ppb, eco2_ppm, temperature_c, humidity_pct,
pm1_ug_m3, pm25_ug_m3, pm10_ug_m3, co_ppb, no2_ppb, nh3_ppb
```

**Storage**: All readings are stored in the evidence JSON:
```json
{
  "range_start": "2024-01-15T10:30:00Z",
  "range_end": "2024-01-15T10:45:00Z",
  "readings": [
    {
      "timestamp": "2024-01-15T10:30:00+00:00",
      "tvoc_ppb": 245.3,
      "eco2_ppm": 810,
      ...
    },
    ...
  ]
}
```

### 2.3 Feature Extraction

**Function**: `_build_feature_vector(start, end)` → `dict[str, object]`

#### Baseline Retrieval

```python
baselines: dict[str, float | None] = {}
for field in [...]:  # all 10 sensor fields
    try:
        baselines[field] = engine._anomaly_detector.baseline(field)
    except Exception:
        baselines[field] = None
```

The **baseline** is an **EMA (Exponential Moving Average)** maintained by the **AnomalyDetector** with α=0.05. It represents the "normal" value for each sensor.

#### Feature Vector Extraction

**File**: [mlss_monitor/feature_extractor.py](../mlss_monitor/feature_extractor.py))

```python
fv = FeatureExtractor().extract(readings, baselines)
```

For each of the 10 sensor channels (tvoc, eco2, temperature, humidity, pm1, pm25, pm10, co, no2, nh3), the extractor computes **10 temporal features**:

| Feature | Description | Example |
|---------|-------------|---------|
| `{sensor}_current` | Latest reading in window | 345.2 ppb |
| `{sensor}_baseline` | EMA of normal values | 250.0 ppb |
| `{sensor}_slope_1m` | units/minute over last 60s | +2.3 ppb/min |
| `{sensor}_slope_5m` | Slope over last 5 min | +1.1 ppb/min |
| `{sensor}_slope_30m` | Slope over last 30 min | +0.8 ppb/min |
| `{sensor}_elevated_minutes` | Time above baseline | 3.5 mins |
| `{sensor}_peak_ratio` | current ÷ baseline | 1.38× |
| `{sensor}_is_declining` | Boolean: slope < 0 ? | false |
| `{sensor}_decay_rate` | Negative slope when declining | -0.5 ppb/min |
| `{sensor}_pulse_detected` | Spike-and-decay pattern? | true |

#### Cross-Sensor Features

Additionally computed:
- `nh3_lag_behind_tvoc_seconds`: Time offset between NH₃ and TVOC peaks (indicates source sequencing)
- `pm25_correlated_with_tvoc`: Both rising together over 5 min window?
- `co_correlated_with_tvoc`: CO and TVOC moving in sync?

#### Derived Features

- `vpd_kpa`: Vapor Pressure Deficit (from temperature & humidity)

### 2.4 Rule & ML Evaluation (Optional)

```python
evaluator = _make_range_evaluator()
if evaluator is not None:
    # Dry-run detection engine to suggest what rules/ML would have fired
    candidates = evaluator.evaluate(FeatureVector(...))
    best_candidate = _choose_best_candidate(candidates)
```

This **shadow evaluates** the range to see if any detection rules or ML models would have fired. If a match is found, it becomes the suggested event type.

### 2.5 Inference Creation & Storage

**File**: [database/db_logger.py](../database/db_logger.py)#L381-L425)

If an event is detected or suggested:
```python
inference_id = save_inference(
    event_type="tvoc_spike",  # or detected event type
    severity="warning",
    title="User-tagged event",
    description="...",
    action="...",
    evidence={
        "fv_timestamp": start,
        "feature_vector": fv_result["feature_vector"],  # All 100+ features
        "range_start": start,
        "range_end": end,
        "readings": readings,  # All raw sensor data
        # ... attribution results if applicable
    },
    confidence=0.5,
)
```

**Database Storage** ([inferences](../database/db_logger.py))):
```sql
CREATE TABLE inferences (
    id INTEGER PRIMARY KEY,
    created_at TEXT,
    event_type TEXT,
    severity TEXT,  -- 'critical', 'warning', 'info'
    title TEXT,
    description TEXT,
    action TEXT,
    evidence TEXT,  -- JSON blob with feature_vector, readings, etc.
    confidence REAL,
    sensor_data_start_id INTEGER,
    sensor_data_end_id INTEGER,
    annotation TEXT,  -- Narrative context
    dismissed INTEGER DEFAULT 0,
    user_notes TEXT
)
```

### 2.6 Tag Association

**Function**: `add_inference_tag(inference_id, tag, confidence=1.0)`

**File**: [database/db_logger.py](../database/db_logger.py)#L536-L560)

```python
if tag:
    add_inference_tag(inference_id, tag, 1.0, allowed_tags=allowed)
```

**Database Storage** ([event_tags](../database/db_logger.py))):
```sql
CREATE TABLE event_tags (
    id INTEGER PRIMARY KEY,
    inference_id INTEGER,
    tag TEXT,  -- fingerprint ID (e.g. 'cooking', 'combustion')
    confidence REAL,  -- user confidence 0-1
    created_at TEXT,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
)
```

---

## 3. What "0 above baseline" Means

In the **Peak vs baseline** analysis panel ([static/js/charts_correlation.js](../static/js/charts_correlation.js)#L753-L759)):

```javascript
const baseline = _corrBaselines[ch];
const ratioStr = baseline 
  ? `${(peak/baseline).toFixed(1)}× baseline (${baseline.toFixed(1)})` 
  : 'Baseline not yet available.';
```

**"0 above baseline"** (or more accurately, "Baseline not yet available") means:
- The sensor channel has not yet accumulated enough readings for the EMA to stabilize
- This typically occurs during:
  - **Cold start**: First 1440 readings (24 hours at 1 Hz sampling) with default config
  - **Fresh sensor**: Recently added or reset sensor
  - **Gap in data**: If data collection was paused

**Ratio interpretation**:
- `1.0× baseline`: Currently reading at normal level
- `2.5× baseline`: 2.5× higher than normal (~bad)
- `0.5× baseline`: Half the normal level (e.g., for inverse sensors like CO resistance)

---

## 4. Correlation Calculation & Display

### 4.1 Baseline Correlations (Pearson r²)

**File**: [static/js/charts_correlation.js](../static/js/charts_correlation.js)#L462-L510)

When a range is selected, the system computes all pairwise linear regressions:

```javascript
const tvocEco2Pairs = subset.filter(d => d.eco2 != null && d.tvoc != null);
const reg = tvocEco2Pairs.length >= 2
  ? linearRegression(tvocEco2Pairs.map(d => d.eco2), 
                     tvocEco2Pairs.map(d => d.tvoc))
  : null;

// reg.r2 indicates strength: 
// > 0.7 = strong correlation, 0.4-0.7 = moderate, < 0.4 = weak
```

**Interpretation**:
- **r² > 0.7**: "Strong correlation — likely common source"
  - Example: Combustion event where PM2.5 and TVOC both rise from cooking
- **0.4 < r² < 0.7**: "Moderate correlation"
  - Example: TVOC from furniture off-gassing + occupancy CO₂
- **r² < 0.4**: "Weak correlation — likely independent sources"
  - Example: Outdoor PM infiltration (no VOC) + indoor occupants (CO₂)

### 4.2 Temporal Correlations (Feature-Based)

**File**: [mlss_monitor/feature_extractor.py](../mlss_monitor/feature_extractor.py)#L225-L260)

```python
def _sensors_correlated(readings, field_a, field_b, window_seconds=300, invert_b=False):
    """True if both sensors moving same direction over window_seconds."""
    slope_a = _slope(readings, field_a, window_seconds)
    slope_b = _slope(readings, field_b, window_seconds)
    if slope_a is None or slope_b is None:
        return None
    effective_b = -slope_b if invert_b else slope_b
    return slope_a > 0 and effective_b > 0
```

Used for ML features like `pm25_correlated_with_tvoc`.

---

## 5. Data Captured When Tagging an Event

When a user tags a time range, **all of this is stored**:

### 5.1 Raw Sensor Readings

Every 1Hz reading in the window:
```javascript
"readings": [
  {
    "timestamp": "2024-01-15T10:30:15+00:00",
    "tvoc_ppb": 240,
    "eco2_ppm": 820,
    "temperature_c": 22.5,
    "humidity_pct": 55,
    "pm1_ug_m3": 3.2,
    "pm25_ug_m3": 5.1,
    "pm10_ug_m3": 8.4,
    "co_ppb": 950,    // 950 Ω resistance
    "no2_ppb": 1200,  // resistance
    "nh3_ppb": 2300   // resistance
  },
  // ... many more points
]
```

### 5.2 Computed Feature Vector

All 100+ features in one snapshot:
```python
{
    "timestamp": "2024-01-15T10:30:15+00:00",
    
    # TVOC (10 features)
    "tvoc_current": 340,
    "tvoc_baseline": 250,
    "tvoc_slope_1m": 2.3,
    "tvoc_slope_5m": 1.1,
    "tvoc_slope_30m": 0.8,
    "tvoc_elevated_minutes": 3.5,
    "tvoc_peak_ratio": 1.36,
    "tvoc_is_declining": False,
    "tvoc_decay_rate": None,
    "tvoc_pulse_detected": True,
    
    # ... (same 10 features for eco2, temperature, humidity, pm1, pm25, pm10, co, no2, nh3)
    
    # Cross-sensor (3 features)
    "nh3_lag_behind_tvoc_seconds": 8.5,
    "pm25_correlated_with_tvoc": True,
    "co_correlated_with_tvoc": True,
    
    # Derived (1 feature)
    "vpd_kpa": 1.2
}
```

### 5.3 Evidence JSON (Stored in DB)

**Schema**: [inferences.evidence](../database/db_logger.py)#L410)

```json
{
    "range_start": "2024-01-15T10:30:00Z",
    "range_end": "2024-01-15T10:45:00Z",
    "feature_vector": { ... 100+ features ... },
    "readings": [ ... raw 1Hz data ... ],
    "attribution_source": "cooking",           // if detected
    "attribution_confidence": 0.82,
    "fv_timestamp": "2024-01-15T10:30:00Z"
}
```

### 5.4 User Tag

Stored separately in [event_tags](../database/db_logger.py)) table:
- **tag**: Fingerprint ID (e.g., "cooking", "combustion", "external_pollution")
- **confidence**: User confidence 0–1 (typically 1.0)
- **created_at**: When the tag was applied

---

## 6. ML Model Learning from Tagged Events

### 6.1 Attribution Engine Training

**File**: [mlss_monitor/attribution/engine.py](../mlss_monitor/attribution/engine.py)#L188-L225)

When the engine initializes or is commanded to retrain:

```python
def train_on_tags(self):
    rows = get_inferences(limit=1000, include_dismissed=False)
    tagged = [r for r in rows if r.get("tags")]
    
    for inf in tagged:
        evidence = inf.get("evidence", {})
        fv_dict = evidence.get("feature_vector")
        
        # Use feature vector fields as input features
        features = {k: v for k, v in fv_dict.items() 
                   if k != "timestamp" and v is not None}
        
        # Train classifier on (features → tag)
        for tag in inf["tags"]:
            self._ml_model.learn_one(features, tag["tag"])
    
    # Persist to disk for next restart
    with open(self._pkl_path, "wb") as fh:
        pickle.dump(self._ml_model, fh)
```

### 6.2 ML Model Architecture

- **Type**: River `StandardScaler | LogisticRegression` (online/streaming)
- **Input**: Feature vector fields (100+ features, many can be None)
- **Output**: Tag label (fingerprint ID like "cooking", "combustion", etc.)
- **Learning**: Incremental (`learn_one()`) — learns from each tagged event
- **Persistence**: Pickled to `data/classifier.pkl` across restarts

### 6.3 Attribution Scoring

**File**: [mlss_monitor/attribution/engine.py](../mlss_monitor/attribution/engine.py)#L115-L180)

When a new inference is generated, the engine:

1. **Scores all fingerprints** (stateful rule-based patterns):
   ```python
   for fp in self._fingerprints:
       ss = sensor_score(fp, fv)    # Pattern match
       ts = temporal_score(fp, fv)  # Timing match
       conf = combine(ss, ts)       # Hybrid score 0-1
   ```

2. **Gets ML prediction**:
   ```python
   ml_label, ml_conf = self._ml_predict(fv)
   ```

3. **Hybrid scoring** (if fingerprint top match agrees with ML):
   ```python
   if best_fp.label == ml_label and ml_conf > 0.5:
       best_conf = 0.6 * fingerprint_conf + 0.4 * ml_conf
   else:
       best_conf = 0.6 * fingerprint_conf  # Lower confidence
   ```

4. **Returns result**:
   - Source ID, label, confidence
   - Runner-up (if within 0.15 delta)
   - Description template filled with feature values
   - Recommended action

### 6.4 Model Evaluation

```python
def evaluate_accuracy(self):
    """Compare ML predictions vs user tags on recent inferences."""
    # Builds confusion matrix to identify misclassifications
    # TODO: adjust weights based on confusion
```

---

## 7. Complete Data Flow Diagram

```
User selects time range on Correlations tab
          ↓
User selects tag from dropdown
          ↓
JavaScript calls POST /api/history/range-tag
          ↓
┌─────────────────────────────────────────┐
│ Backend: /api/history/range-tag         │
├─────────────────────────────────────────┤
│ 1. Query sensor_data + hot_tier         │
│    (merge by timestamp)                 │
│                                         │
│ 2. Fetch baselines from AnomalyDetector │
│    (_ema dict, alpha=0.05)              │
│                                         │
│ 3. FeatureExtractor.extract()           │
│    → 100+ temporal features             │
│                                         │
│ 4. Optional: Shadow-run DetectionEngine │
│    to suggest event_type                │
│                                         │
│ 5. save_inference():                    │
│    INSERT inferences table              │
│    evidence JSON = {                    │
│      readings,                          │
│      feature_vector,                    │
│      range_start/end,                   │
│      attribution results                │
│    }                                    │
│                                         │
│ 6. add_inference_tag():                 │
│    INSERT event_tags table              │
│    (inference_id, tag, confidence)      │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│ Learning Trigger                        │
├─────────────────────────────────────────┤
│ attributionEngine.train_on_tags()       │
│ (on command or init)                    │
│                                         │
│ For each tagged inference:              │
│   Extract feature_vector from evidence  │
│   Train: ML_model.learn_one(            │
│     features → tag                      │
│   )                                     │
│                                         │
│ Persist: pickle.dump(ML_model)          │
│ → data/classifier.pkl                   │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│ Future Attribution                      │
├─────────────────────────────────────────┤
│ When next inference fires:              │
│   score = 0.6 * fingerprint_score       │
│         + 0.4 * ml_predict(fv)          │
│                                         │
│ Evidence stored with:                   │
│   attribution_source                    │
│   attribution_confidence                │
└─────────────────────────────────────────┘
```

---

## 8. Temporal Windows & Data Selection

### 8.1 How Temporal Windows Are Defined

User interaction on Correlations tab:
1. User drags on the **brush chart** to select a time interval
2. The selection is stored in `_corrSelectedRange`:
   ```javascript
   {
       start: "2024-01-15T10:30:00Z",
       end: "2024-01-15T10:45:00Z"
   }
   ```
3. This window determines which readings are included

### 8.2 Data Sources for Selected Window

**hot_tier** (preferred for recent data):
- Stores 1-second resolution readings
- ~60-minute retention window
- Used for inferences that occurred < 60 min ago
- Table: `hot_tier(timestamp, source, tvoc_ppb, eco2_ppm, ...)`

**sensor_data** (cold storage):
- Stores approximately hourly downsampled readings
- Unlimited retention
- Used for historical range analysis
- Table: `sensor_data(timestamp, tvoc, eco2, temperature, ...)`

**Selection logic** ([api_history.py](../mlss_monitor/routes/api_history.py)#L130-L150)):
```python
def _build_range_readings(start: str, end: str) -> list[NormalisedReading]:
    sensor_rows = _query_sensor_data(DB_FILE, start, end)
    hot_rows = _query_hot_tier(DB_FILE, start, end)
    
    merged: dict[str, dict] = {}
    for row in sensor_rows:
        key = row.get("timestamp").strip().replace(" ", "T")
        merged[key] = row
    
    for row in hot_rows:
        key = row.get("timestamp").strip()
        if key not in merged:  # hot_tier only fills gaps
            merged[key] = row
    
    sorted_rows = sorted(merged.values(), key=lambda r: r.get("timestamp", ""))
    # Convert to NormalisedReading objects
    readings = _rows_to_readings(sorted_rows, source="history")
    return readings
```

---

## 9. Baseline Calculation Details

### 9.1 Baseline Initialization

**File**: [mlss_monitor/anomaly_detector.py](../mlss_monitor/anomaly_detector.py)#L55-L65)

On startup, attempts to load persisted models and EMA state from pickle files:
```python
self._ema: dict[str, float] = {}  # Channel → EMA value
```

### 9.2 Baseline Update Cycle

During each detection cycle ([inference_engine.py](../mlss_monitor/inference_engine.py)) called ~every 60s):

```python
def learn_and_score(self, fv: FeatureVector) -> dict[str, float | None]:
    for ch in self._channels():
        value = getattr(fv, fv_field, None)
        if value is None:
            continue
        
        # Score anomaly
        raw_score = float(model.score_one({"value": float(value)}))
        model.learn_one({"value": float(value)})
        
        # Update EMA baseline
        alpha = 0.05
        self._ema[ch] = alpha * value + (1 - alpha) * self._ema.get(ch, value)
        
        scores[ch] = None if n_seen < cold_start else raw_score
```

**EMA Formula**:
```
new_baseline = 0.05 × current_reading + 0.95 × old_baseline
```

This gives:
- Rapid response to sustained changes (half-life ~20 readings)
- Smooth filtering of transient spikes
- Continuous learning without retraining

### 9.3 Cold Start Suppression

```python
cold_start = self._config.get("cold_start_readings", 1440)
scores[ch] = None if self._n_seen[ch] < cold_start else raw_score
```

- Default: 1440 readings (24 hours at 1 Hz)
- During cold start, anomaly scores are suppressed (None)
- Baseline is still computed and updated

### 9.4 Baseline Bootstrap

**File**: [mlss_monitor/anomaly_detector.py](../mlss_monitor/anomaly_detector.py)#L82-L99)

On startup, can pre-load historical data to warm up baseline:

```python
def bootstrap(self, channel_data: dict[str, list[float]]) -> None:
    for ch, values in channel_data.items():
        model = self._models[ch]
        for v in values:
            model.learn_one({"value": float(v)})
            self._n_seen[ch] += 1
        log.info("AnomalyDetector.bootstrap: fed %d readings into %r", len(values), ch)
    self._save_models()
```

---

## 10. What Gets Used for ML Model Learning

### 10.1 Training Data Source

**Query**: Last 1000 inferences with tags
```python
rows = get_inferences(limit=1000, include_dismissed=False)
tagged = [r for r in rows if r.get("tags")]
```

### 10.2 Features Used

From `feature_vector` JSON in evidence:
- **All 100+ float fields** that are not None
- Includes baseline, slopes, ratios, correlations, VPD, etc.
- Does **not** include raw readings (too granular)

### 10.3 Labels Used

From `event_tags` table:
- **Tag ID** (e.g., "cooking", "combustion", "external_pollution")
- One tag per inference, but multiple tags per inference is possible
- Training creates (features → tag) mappings

### 10.4 Model Improvements Over Time

As more events are tagged:
1. **Fingerprint scoring** remains static (YAML-defined patterns)
2. **ML model** learns from tagged feature vectors
3. **Hybrid scoring** adjusts weights (0.6 × fingerprint + 0.4 × ML)
4. **Evaluate accuracy**: Confusion matrix on recent predictions vs tags

### 10.5 Ready Threshold

```python
_READY_THRESHOLD = 5  # minimum tagged samples for a label to be "ready"
```

A label is only considered "ready" for reliable ML predictions after ≥5 tagged samples.

---

## 11. Architecture Highlights

### 11.1 Key Design Patterns

1. **Temporal feature extraction**: 10 features per sensor captures pattern at multiple timescales (1 min, 5 min, 30 min slopes)
2. **EMA baselines**: Lightweight streaming baseline without heavy history storage
3. **Hybrid scoring**: Combines static fingerprints (reliable but verbose) with learned ML model (fast but sample-constrained)
4. **River online learning**: Incremental ML that learns from each tagged sample without retraining
5. **Evidence persistence**: Stores raw readings + features for reproducibility and future analysis

### 11.2 Data Quality Assumptions

- **Sensor data is clean**: Grid search for NaN/None values and graceful skip
- **Timestamps are UTC**: Normalized on read/write
- **Baselines stabilize**: 24-hour cold start before anomaly scoring
- **Tags are expert input**: No validation of user tags; treated as ground truth

### 11.3 Extensibility

To add a new ML-learned fingerprint:
1. Add row to `config/fingerprints.yaml` (stateful pattern rules)
2. Tag a few events with the new fingerprint ID
3. Call `attributionEngine.train_on_tags()`
4. Next inferences will include ML predictions for that fingerprint

---

## 12. Example: Complete Tagging Scenario

**Scenario**: User notices TVOC spike at 10:30 AM, suspects cooking.

**Step 1: User Action**
- Drags brush selection on Correlations tab: 10:25 AM – 10:35 AM
- Selects "cooking" tag from dropdown
- Clicks "Save tagged event"

**Step 2: Backend Processing**
- Queries 600 readings (10 min × 1 Hz) from hot_tier
- Fetches baselines: tvoc_baseline=250 ppb, eco2_baseline=800 ppm, ...
- Extracts feature vector:
  - tvoc_current: 450 ppb
  - tvoc_baseline: 250 ppb
  - tvoc_slope_1m: +3.2 ppb/min (rising)
  - tvoc_peak_ratio: 1.8×
  - pm25_correlated_with_tvoc: true
  - ... 90+ more features
- Shadow-runs DetectionEngine:
  - Rule: "high_tvoc" matches (450 > 350 threshold)
  - Attribution: cooking fingerprint scores 0.78
- Creates inference:
  ```
  INSERT inferences(...) VALUES(
    event_type="tvoc_spike",
    severity="warning",
    confidence=0.78,
    evidence={
      "readings": [600 raw readings],
      "feature_vector": {all 100+ features},
      "range_start": "2024-01-15T10:25:00Z",
      "range_end": "2024-01-15T10:35:00Z",
      "attribution_source": "cooking",
      "attribution_confidence": 0.78
    }
  )
  ```
- Adds tag:
  ```
  INSERT event_tags(inference_id=123, tag="cooking", confidence=1.0)
  ```

**Step 3: UI Feedback**
- JavaScript displays: "Tagged range saved successfully."
- Correlations tab updates to show new event

**Step 4: ML Learning (Future)**
- User or system calls `attributionEngine.train_on_tags()`
- Query fetches all tagged inferences (including this one)
- River model learns: `{tvoc_current=450, tvoc_slope_1m=3.2, ..., tvoc_peak_ratio=1.8, ...} → "cooking"`
- Model persisted to `data/classifier.pkl`

**Step 5: Next Similar Event**
- Next TVOC spike at 6:00 PM (different day)
- DetectionEngine runs attribution with trained ML model
- Output: "cooking" with 0.82 confidence (hybrid: 0.6×fingerprint + 0.4×ML)
- Stored in evidence as `attribution_source: "cooking"`

---

## Summary Table: Data Flow

| Component | Input | Processing | Output | Storage |
|-----------|-------|-----------|--------|---------|
| **UI** | Time range + tag | Brush selection | start/end timestamps + tag ID | Browser memory |
| **Endpoint** | start, end, tag | Query DB + extract features | 100+ feature vector | inferences.evidence JSON |
| **FeatureExtractor** | NormalisedReadings + baselines | 10 features × 10 sensors | Feature vector dict | evidence |
| **DetectionEngine** | Feature vector | Rule + ML eval | event_type, confidence | inferences row |
| **Tagging** | inference_id + tag | Validate tag in vocabulary | Tag row in event_tags | event_tags table |
| **Attribution ML** | Tagged inferences | Train on (features → tag) | River classifier | data/classifier.pkl |
| **Future inference** | Feature vector | Score with trained ML | prediction + confidence | inferences.evidence |

---

## Key Files Reference

- **UI**: [templates/history.html](../templates/history.html)), [static/js/history.js](../static/js/history.js)), [static/js/charts_correlation.js](../static/js/charts_correlation.js))
- **Endpoint**: [mlss_monitor/routes/api_history.py](../mlss_monitor/routes/api_history.py)#L235)
- **Feature extraction**: [mlss_monitor/feature_extractor.py](../mlss_monitor/feature_extractor.py)), [mlss_monitor/feature_vector.py](../mlss_monitor/feature_vector.py))
- **Baselines**: [mlss_monitor/anomaly_detector.py](../mlss_monitor/anomaly_detector.py))
- **Data persistence**: [database/db_logger.py](../database/db_logger.py))
- **Attribution ML**: [mlss_monitor/attribution/engine.py](../mlss_monitor/attribution/engine.py))
- **Detection**: [mlss_monitor/detection_engine.py](../mlss_monitor/detection_engine.py))

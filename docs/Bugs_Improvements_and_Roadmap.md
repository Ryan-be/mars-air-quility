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



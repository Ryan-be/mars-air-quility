# Event Tagging Enhancements — Design Spec

**Date:** 2026-04-05
**Branch:** `feature/event-tagging-learning`
**Phase:** 7 (Enhanced Learning & Model Updating)

---

## Context

The event tagging system allows users to label detected inferences and annotate custom time ranges, feeding a River `LogisticRegression` classifier that improves attribution alongside the existing fingerprint engine. The core flow is working. This spec covers three enhancements:

1. **Classifier persistence** — survive restarts without full retraining latency
2. **Tag normalization** — enforce controlled vocabulary end-to-end
3. **Classifier feedback panel** — surface training health in the Admin UI

---

## Feature 1: Classifier Persistence

### Goal
Persist the trained River pipeline to disk so the model state is preserved across restarts. Retraining from DB remains the fallback.

### Approach
Serialize `AttributionEngine._ml_model` with `pickle` after every successful `train_on_tags()` call. Load from disk at startup before falling back to DB retraining.

### File
`data/classifier.pkl` (relative to project root, alongside the SQLite database)

### Behaviour

**Startup sequence in `AttributionEngine.__init__()`:**
1. Check if `data/classifier.pkl` exists
2. If yes → load with `pickle.load()`, log `"AttributionEngine: loaded classifier from disk (N tags)"`, skip DB retraining
3. If no → run existing `train_on_tags()` from DB, log as before

**After every `train_on_tags()` call:**
1. Serialize model to `data/classifier.pkl` with `pickle.dump()`
2. Log `"AttributionEngine: classifier saved to disk"`
3. Wrap in try/except — failure to save must not block tagging

**Invalidation:** No explicit invalidation needed. The pickle is always overwritten after retraining, so it stays in sync.

### Error Handling
- Corrupt/incompatible pickle (e.g., after River version upgrade): catch `Exception` on load, log warning, fall back to DB retraining, delete the bad file
- Missing `data/` directory: create it if absent

---

## Feature 2: Tag Normalization (Controlled Vocabulary)

### Goal
Ensure only valid fingerprint IDs are stored as tags, eliminating free-form strings that fragment the classifier's label space.

### Canonical Vocabulary
Tags are the `id` field from `config/fingerprints.yaml`, stored as lowercase underscore strings:

| ID | Label |
|----|-------|
| `biological_offgas` | Biological off-gassing |
| `chemical_offgassing` | Chemical off-gassing |
| `cooking` | Cooking activity |
| `combustion` | Combustion |
| `external_pollution` | External pollution ingress |
| `cleaning_products` | Cleaning products |
| `human_activity` | Human activity / occupancy |
| `vehicle_exhaust` | Vehicle exhaust |
| `mould_voc` | Mould / fungal VOC |
| `personal_care` | Personal Care Products |

### Changes

**Backend — `add_inference_tag()` in `database/db_logger.py`:**
- Accept optional `allowed_tags: set[str]` parameter
- If provided and tag not in set → raise `ValueError(f"Unknown tag: {tag}")`
- Callers pass `state.detection_engine._attribution_engine.valid_tags`

**Backend — `AttributionEngine`:**
- Add `valid_tags: frozenset[str]` property returning fingerprint IDs from loaded fingerprints
- Pass to `add_inference_tag()` from both API endpoints

**Backend — API endpoints:**
- `POST /api/inferences/<id>/tags` → validate tag, return `400 {"error": "invalid_tag", "valid_tags": [...]}` if invalid
- `POST /api/history/range-tag` → same validation
- `GET /api/tags` → new endpoint, returns `{"tags": [{"id": "cooking", "label": "Cooking activity"}, ...]}` driven from fingerprints.yaml

**Frontend — `templates/history.html`:**
- Remove `<input type="text" id="infTagCustom">` and its associated JS
- Replace both `<select>` option values with the canonical underscore IDs (fix hyphen→underscore mismatch)
- Populate both dropdowns dynamically from `GET /api/tags` on page load (removes hardcoding, stays in sync with YAML)

### Naming Fix
Current HTML uses hyphens (`biological-offgassing`). Canonical form is the YAML `id` field (underscores). Both dropdowns will be updated to use underscore IDs.

---

## Feature 3: Classifier Feedback Panel

### Goal
Surface classifier training health in the Admin UI, matching the visual style and update mechanism of the existing **Anomaly Models** card.

### Location
`templates/admin.html` — Insights Engine tab, below the existing Anomaly Models card.

### Card Layout

```
┌─ 🧠 Classifier Model ─────────────────────────────────────┐
│  Tag Label        │ Samples │ Avg Confidence │ Status      │
│  cooking          │   12    │   ████░░  0.72 │ ● Ready     │
│  combustion       │    3    │   ██░░░░  0.41 │ ⏳ Learning  │
│  vehicle_exhaust  │    0    │   —            │ ⏳ Learning  │
└────────────────────────────────────────────────────────────┘
```

**Columns:**
| Column | Source | Notes |
|--------|--------|-------|
| Tag Label | Fingerprint ID | All known labels shown, even untrained ones |
| Samples | Count from `event_tags` table grouped by tag | |
| Avg Confidence | Mean `predict_proba` score for this label across last 50 inferences | Only populated once model has ≥ 5 samples |
| Status | "Ready" if ≥ 5 samples, "Learning" otherwise | |

**Status thresholds (score bar colours matching anomaly models):**
- Green: avg confidence ≥ 0.70
- Amber: 0.50–0.69
- Red: < 0.50 (but Ready — model exists, low confidence)
- Grey "Learning": < 5 samples

### New API Endpoint
`GET /api/classifier/stats` — requires `admin` role

```json
{
  "total_samples": 47,
  "tag_stats": [
    {
      "tag": "cooking",
      "label": "Cooking activity",
      "sample_count": 12,
      "avg_confidence": 0.72,
      "ready": true
    },
    {
      "tag": "combustion",
      "label": "Combustion",
      "sample_count": 3,
      "avg_confidence": 0.41,
      "ready": false
    }
  ]
}
```

`avg_confidence` is computed by running `predict_proba` on all stored feature vectors for that tag and averaging. Returns `null` if model not ready for that label.

### Updates
- Panel loaded on page load via `fetch('/api/classifier/stats')`
- Refreshed after each tag is added (frontend calls refresh on tag submission success)
- No SSE needed — classifier stats change only when user adds a tag, not continuously

### Placement in admin.html
Add after the existing `📡 Anomaly Models` card, same `.card` wrapper class.

---

## Out of Scope
- Multi-label tagging (one tag per inference enforced by UI)
- Tag recency weighting
- Feature importance / SHAP values
- Exporting the classifier

---

## File Change Summary

| File | Change |
|------|--------|
| `mlss_monitor/attribution/engine.py` | Add pickle save/load, `valid_tags` property, confidence stats for `/api/classifier/stats` |
| `database/db_logger.py` | Add `allowed_tags` validation to `add_inference_tag()` |
| `mlss_monitor/routes/api_inferences.py` | Tag validation + 400 response |
| `mlss_monitor/routes/api_history.py` | Tag validation + 400 response |
| `mlss_monitor/routes/api_insights.py` | Add `GET /api/classifier/stats` endpoint |
| `mlss_monitor/routes/api_tags.py` | New file: `GET /api/tags` endpoint |
| `templates/admin.html` | Add Classifier Model card |
| `templates/history.html` | Remove custom text input, fix dropdown IDs, dynamic load from `/api/tags` |

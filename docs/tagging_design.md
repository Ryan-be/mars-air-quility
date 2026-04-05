# Tagging and Learning Design

## Purpose

This document explains how user tagging connects to the inference engine and how those tags help the system learn over time.

## Overview

The system has two related learning paths:

1. **Detection** — the engine finds unusual sensor events using rules, statistical anomaly detectors, and composite River ML models.
2. **Attribution** — the system assigns those detections to real-world sources using fingerprints and an incremental classifier trained on user tags.

User tags are the bridge between these two paths.

## What a tag means

A tag is a human label attached to an inference event. Examples include:

- `personal_care_products`
- `cooking`
- `combustion`
- `cleaning_products`

When a user tags an event, the system records the tag in the database and uses the associated evidence to improve future attributions.

## Where tags are stored

Tags are persisted in the `event_tags` table, which links:

- `inference_id` — the event being tagged
- `tag` — the label chosen by the user
- `confidence` — usually `1.0` for direct user tags
- `created_at` — timestamp for when the tag was added

The inference itself also stores evidence in the `inferences` table, including:

- `feature_vector` — derived measurements from the sensor window
- `readings` — the raw sensor values for the selected range
- `sensor_snapshot` — the structured signal summary for rule-based events

## How tagging updates detection/attribution

The current design is built around a hybrid attribution model:

1. A detection event is created and saved in `inferences`.
2. The attribution engine scores that event against a set of source fingerprints from `config/fingerprints.yaml`.
3. If the event is tagged by a user, the tag is stored in `event_tags`.
4. Tag insertion triggers `AttributionEngine.train_on_tags()`.
5. The classifier retrains using feature vectors from all tagged events in the database.
6. Future attributions combine fingerprint confidence with classifier score.

## River models in the system

The project uses River models in two ways:

- `AnomalyDetector` and `MultivarAnomalyDetector` use River `HalfSpaceTrees` for streaming anomaly detection. These models detect unusual single-channel and multi-channel patterns respectively.
- `AttributionEngine` uses River `StandardScaler | LogisticRegression` as a lightweight online classifier for tagged attribution data.

### What each River model does

- `HalfSpaceTrees` detect anomalies in live sensor streams by learning what "normal" looks like and flagging deviations.
- `LogisticRegression` learns a mapping from `FeatureVector` values to user-supplied tags.

## What is trained from a tag

Only events that contain a `feature_vector` can train the classifier.

For a tagged event, the system extracts:

- all numeric and boolean feature values from the feature vector
- the user-supplied tag label

Then the classifier calls `learn_one(features, tag)` for each tagged sample.

## Tagging flow for user-selected ranges

A user can tag an arbitrary history range. That flow is:

1. User selects a time range in history.
2. The system builds a `FeatureVector` from sensor readings in that range.
3. A `User-tagged event` inference is created with evidence containing:
   - `readings`
   - `feature_vector`
4. When the user submits a tag, `event_tags` is updated.
5. `train_on_tags()` retrains the classifier from all existing tagged feature vectors.

## Why this is useful

This design allows the system to learn from user feedback without requiring a separate offline training pipeline.

- Fingerprints provide robust, interpretable attribution.
- Tags help the classifier adapt to the actual environment and sensor behaviour.
- The hybrid approach reduces false attributions by blending heuristics with learned patterns.

## Known limitations and risks

- The classifier state is in-process and is rebuilt from tags at startup. If the engine fails to retrain on startup, the model can remain empty until a new tag is added.
- Only tagged events with a complete `feature_vector` contribute to training.
- Custom tag strings are not normalized before training, so inconsistent naming can reduce classifier quality.
- The fingerprint heuristic is still the primary signal; the classifier refines confidence rather than replacing fingerprints.

## Recommended improvements

- Persist the classifier state to disk so learning survives restarts more efficiently.
- Normalize or constrain tag names to a controlled vocabulary.
- Add an automatic startup training step for all existing tagged events (already implemented in the current codebase).
- Add clear UI feedback when a tagged event is used as training data.

## Files involved

- `mlss_monitor/routes/api_history.py` — range tagging and feature vector building
- `database/db_logger.py` — tag persistence and training trigger
- `mlss_monitor/attribution/engine.py` — fingerprint scoring and River classifier training
- `config/fingerprints.yaml` — source fingerprint definitions
- `mlss_monitor/feature_extractor.py` — builds `FeatureVector` from sensor readings
- `mlss_monitor/feature_vector.py` — feature schema
- `static/js/dashboard.js` and `static/js/detections_insights.js` — evidence rendering in the UI

"""AttributionEngine: scores fingerprints against a FeatureVector, returns top match."""
from __future__ import annotations

import dataclasses
import logging
import pickle
from pathlib import Path

from river import linear_model, preprocessing  # pylint: disable=import-error

from database.db_logger import get_inference_tags, get_inferences  # pylint: disable=import-outside-toplevel
from mlss_monitor.attribution.loader import Fingerprint, load_fingerprints
from mlss_monitor.attribution.scorer import combine, sensor_score, temporal_score
from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

# If the runner-up confidence is within this delta of the primary, it is surfaced.
_RUNNER_UP_DELTA = 0.15
_READY_THRESHOLD = 5  # minimum tagged samples for a label to be "ready"


class _StringLabelClassifier:
    """Wraps a River classifier to accept string labels for training and prediction.

    River's LogisticRegression calls int(y_true) in gradient computation, so
    string labels must be encoded as integers. This wrapper handles encoding on
    learn_one and decoding on predict/predict_proba_one.
    """

    def __init__(self):
        self._label_to_idx: dict[str, int] = {}
        self._idx_to_label: dict[int, str] = {}
        self._model = preprocessing.StandardScaler() | linear_model.LogisticRegression()

    def _encode(self, label: str) -> int:
        if label not in self._label_to_idx:
            idx = len(self._label_to_idx)
            self._label_to_idx[label] = idx
            self._idx_to_label[idx] = label
        return self._label_to_idx[label]

    def _decode(self, idx: int) -> str:
        return self._idx_to_label.get(idx, "")

    def learn_one(self, features: dict, label: str) -> None:
        self._model.learn_one(features, self._encode(label))

    def predict_proba_one(self, features: dict) -> dict[str, float]:
        raw = self._model.predict_proba_one(features)
        if not raw:
            return {}
        return {self._decode(idx): prob for idx, prob in raw.items()}

    def predict_one(self, features: dict) -> str:
        idx = self._model.predict_one(features)
        return self._decode(idx)
# Minimum ML confidence to trust the classifier over fingerprint alone.
_ML_CONFIDENCE_MIN = 0.5
# Strong ML confidence — overrides fingerprint disagreement when classifier is very sure.
_ML_OVERRIDE_CONFIDENCE = 0.7


@dataclasses.dataclass
class AttributionResult:
    source_id:            str
    label:                str
    confidence:           float
    runner_up_id:         str | None
    runner_up_confidence: float | None
    description:          str
    action:               str


class AttributionEngine:
    """Loads fingerprints from YAML and scores them against a FeatureVector.

    Usage:
        engine = AttributionEngine("config/fingerprints.yaml")
        result = engine.attribute(fv)   # AttributionResult or None
    """

    def __init__(self, config_path) -> None:
        self._config_path = Path(config_path)
        # Ensure data directory exists for classifier persistence.
        (self._config_path.parent.parent / "data").mkdir(exist_ok=True)
        self._fingerprints: list[Fingerprint] = load_fingerprints(self._config_path)
        self._ml_model = _StringLabelClassifier()
        log.info(
            "AttributionEngine: loaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )
        # Try loading persisted classifier; fall back to DB retraining.
        pkl = self._pkl_path
        if pkl.exists():
            try:
                with open(pkl, "rb") as fh:
                    self._ml_model = pickle.load(fh)
                log.info("AttributionEngine: loaded classifier from disk (%s)", pkl.name)
                return
            except Exception as exc:
                log.warning(
                    "AttributionEngine: corrupt pickle %s (%s) — retraining from DB",
                    pkl, exc,
                )
                try:
                    pkl.unlink()
                except OSError:
                    pass
        self.train_on_tags()

    @property
    def _pkl_path(self) -> Path:
        """Path to the persisted classifier: <project_root>/data/classifier.pkl."""
        return self._config_path.parent.parent / "data" / "classifier.pkl"

    @property
    def valid_tags(self) -> frozenset[str]:
        """Frozenset of canonical tag IDs derived from loaded fingerprints."""
        return frozenset(fp.id for fp in self._fingerprints)

    def tags_with_labels(self) -> list[dict]:
        """Return [{"id": ..., "label": ...}, ...] for all loaded fingerprints."""
        return [{"id": fp.id, "label": fp.label} for fp in self._fingerprints]

    def reload(self) -> None:
        """Thread-safe hot-reload: re-read fingerprints.yaml.

        Acquires the shared yaml_lock before reading so a concurrent
        atomic_write cannot race with this read.
        """
        from mlss_monitor.yaml_io import yaml_lock
        with yaml_lock:
            self._fingerprints = load_fingerprints(self._config_path)
        log.info(
            "AttributionEngine: reloaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )

    def attribute(self, fv: FeatureVector):
        """Score all fingerprints against fv, return the best match above its floor.

        Returns:
            AttributionResult with runner_up set if runner-up is within 0.15.
            None if no fingerprint clears its confidence_floor.
        """
        if not self._fingerprints:
            return None

        scored = []
        for fp in self._fingerprints:
            try:
                ss = sensor_score(fp, fv)
                ts = temporal_score(fp, fv)
                conf = combine(ss, ts)
                scored.append((conf, fp))
            except Exception as exc:
                log.warning(
                    "AttributionEngine: error scoring fingerprint %r: %s",
                    fp.id, exc,
                )

        if not scored:
            return None

        # Sort descending by confidence
        scored.sort(key=lambda x: x[0], reverse=True)
        best_conf, best_fp = scored[0]

        # Get ML prediction
        ml_label, ml_conf = self._ml_predict(fv)
        if ml_label and ml_conf > _ML_CONFIDENCE_MIN:
            if best_fp.label == ml_label:
                best_conf = 0.6 * best_conf + 0.4 * ml_conf
            elif ml_conf >= _ML_OVERRIDE_CONFIDENCE:
                best_conf = ml_conf
                best_fp = self._fp_by_label(ml_label)
                if best_fp is None:
                    best_conf = 0.0

        if best_conf < best_fp.confidence_floor:
            ml_lbl, ml_cnf = self.ml_score(fv)
            if ml_lbl and ml_cnf > _ML_CONFIDENCE_MIN:
                ml_fp = self._fp_by_label(ml_lbl)
                if ml_fp is not None:
                    best_conf = ml_cnf
                    best_fp = ml_fp
                else:
                    return None
            else:
                return None

        # Runner-up
        runner_up_id = None
        runner_up_conf = None
        if len(scored) > 1:
            second_conf, second_fp = scored[1]
            if (best_conf - second_conf) <= _RUNNER_UP_DELTA:
                runner_up_id = second_fp.id
                runner_up_conf = second_conf

        # Fill description template (gracefully handle missing fields).
        # Replace None with 0 so numeric format specs like {tvoc_current:.0f}
        # succeed even when a sensor field has no data.
        fv_dict = {k: (v if v is not None else 0)
                   for k, v in dataclasses.asdict(fv).items()}

        class _SafeDict(dict):
            def __missing__(self, key):
                return 0

        try:
            desc = best_fp.description_template.format_map(_SafeDict(fv_dict))
        except Exception:
            desc = best_fp.description

        try:
            action = best_fp.action_template.format_map(
                _SafeDict({**fv_dict, "persistence_note": ""})
            )
        except Exception:
            action = best_fp.action_template

        return AttributionResult(
            source_id=best_fp.id,
            label=best_fp.label,
            confidence=best_conf,
            runner_up_id=runner_up_id,
            runner_up_confidence=runner_up_conf,
            description=desc,
            action=action,
        )

    def evaluate_accuracy(self):
        """Evaluate fingerprint accuracy against tagged events.

        Queries recent tagged inferences, re-runs attribution on their FeatureVectors,
        and logs confusion matrix.
        """
        try:
            # Get recent inferences with tags
            rows = get_inferences(limit=100, include_dismissed=False)
            for row in rows:
                row["tags"] = get_inference_tags(row["id"])
            tagged = [r for r in rows if r.get("tags")]
            if not tagged:
                log.info("AttributionEngine: no tagged events to evaluate")
                return

            confusion = {}
            for inf in tagged:
                user_tags = [t["tag"] for t in inf["tags"]]
                evidence = inf.get("evidence", {})
                if not isinstance(evidence, dict):
                    continue
                fv_dict = evidence.get("feature_vector")
                if not fv_dict:
                    continue
                # Reconstruct FV (simplified, assuming all fields present)
                from mlss_monitor.feature_vector import FeatureVector  # pylint: disable=import-outside-toplevel,reimported
                from datetime import datetime  # pylint: disable=import-outside-toplevel
                fv = FeatureVector(
                    timestamp=datetime.fromisoformat(fv_dict["timestamp"]),
                    **{k: v for k, v in fv_dict.items() if k != "timestamp"}
                )
                result = self.attribute(fv)
                predicted = result.label if result else "unknown"
                for user_tag in user_tags:
                    key = (predicted, user_tag)
                    confusion[key] = confusion.get(key, 0) + 1

            if confusion:
                log.info("AttributionEngine: confusion matrix: %s", confusion)
                # TODO: adjust weights based on confusion  # pylint: disable=fixme
        except Exception as exc:
            log.warning("AttributionEngine: evaluation error: %s", exc)

    def _fp_by_label(self, label: str):
        """Return the Fingerprint whose id (fingerprint label) matches `label`, or None."""
        for fp in self._fingerprints:
            if fp.id == label:
                return fp
        return None

    def ml_score(self, fv: FeatureVector):
        """Return (label, confidence) from the classifier, or (None, 0.0) if not ready.

        Returns None when the classifier has been trained on fewer than _READY_THRESHOLD
        samples total, so callers can distinguish "no signal yet" from "model produced
        a confident prediction".
        """
        total_samples = sum(
            1 for fp in self._fingerprints
            if self._tag_count_for_label(fp.id) >= _READY_THRESHOLD
        )
        if total_samples == 0:
            return None, 0.0
        return self._ml_predict(fv)

    def _tag_count_for_label(self, tag: str) -> int:
        """Count how many times `tag` appears in event_tags (from last train run cache)."""
        return getattr(self, "_tag_count_cache", {}).get(tag, 0)

    def train_on_tags(self):
        """Train ML model on user-tagged events.

        Feature vectors are always re-extracted from raw sensor data so that
        improvements to FeatureExtractor (new fields, changed algorithms) are
        automatically incorporated on every retrain. Falls back to the stored
        feature_vector when raw data is unavailable (e.g. older than retention).
        """
        try:
            # pylint: disable=import-outside-toplevel
            from datetime import datetime, timedelta, timezone

            # Reset model so repeated retrains don't accumulate duplicate samples.
            self._ml_model = _StringLabelClassifier()

            # Lazy import to avoid circular dependency; fails gracefully.
            try:
                from mlss_monitor.routes.api_history import _build_feature_vector as _bfv
            except Exception:  # pylint: disable=broad-exception-caught
                _bfv = None

            rows = get_inferences(limit=1000, include_dismissed=False)
            for row in rows:
                row["tags"] = get_inference_tags(row["id"])
            tagged = [r for r in rows if r.get("tags")]
            log.info(
                "AttributionEngine: found %d tagged inferences out of %d total",
                len(tagged), len(rows),
            )

            self._tag_count_cache: dict[str, int] = {}
            for inf in tagged:
                for t in inf.get("tags", []):
                    tag = t["tag"]
                    self._tag_count_cache[tag] = self._tag_count_cache.get(tag, 0) + 1

            trained = 0
            for inf in tagged:
                fv_features: dict | None = None

                # ── Try live re-extraction from raw sensor data ───────────────────
                created_at = inf.get("created_at", "")
                if _bfv and created_at:
                    try:
                        dt = datetime.fromisoformat(
                            created_at.rstrip("Z")
                        ).replace(tzinfo=timezone.utc)
                        win_start = (dt - timedelta(minutes=30)).isoformat()
                        win_end   = (dt + timedelta(minutes=15)).isoformat()
                        result    = _bfv(win_start, win_end)
                        fv_dict   = result.get("feature_vector") or {}
                        if fv_dict:
                            fv_features = {
                                k: v for k, v in fv_dict.items()
                                if k != "timestamp" and v is not None
                            }
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        log.debug(
                            "AttributionEngine: backfill extraction failed for %s: %s",
                            created_at, exc,
                        )

                # ── Fall back to stored evidence blob ────────────────────────────
                if not fv_features:
                    evidence = inf.get("evidence", {})
                    if not isinstance(evidence, dict):
                        continue
                    fv_dict = evidence.get("feature_vector") or {}
                    if not fv_dict:
                        continue
                    fv_features = {
                        k: v for k, v in fv_dict.items()
                        if k != "timestamp" and v is not None
                    }

                if not fv_features:
                    continue

                for tag in inf["tags"]:
                    self._ml_model.learn_one(fv_features, tag["tag"])
                    trained += 1

            if trained:
                log.info(
                    "AttributionEngine: trained on %d samples from %d tagged inferences",
                    trained, len(tagged),
                )
            else:
                log.warning(
                    "AttributionEngine: 0 usable training samples from %d tagged inferences",
                    len(tagged),
                )
            # Persist model to disk.
            try:
                with open(self._pkl_path, "wb") as fh:
                    pickle.dump(self._ml_model, fh)
                log.info("AttributionEngine: classifier saved to disk")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                log.warning("AttributionEngine: could not save classifier: %s", exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.warning("AttributionEngine: training error: %s", exc)

    def classifier_stats(self) -> dict:
        """Return per-tag training statistics for the admin classifier panel.

        Returns:
            {
                "total_samples": int,
                "tag_stats": [
                    {"tag": str, "label": str, "sample_count": int,
                     "avg_confidence": float | None, "ready": bool},
                    ...
                ]
            }
        """
        # Count tagged samples per tag from the DB.
        tag_counts: dict[str, int] = {}
        try:
            rows = get_inferences(limit=5000, include_dismissed=False)
            for inf in rows:
                tags = get_inference_tags(inf["id"])
                for t in tags:
                    tag_counts[t["tag"]] = tag_counts.get(t["tag"], 0) + 1
        except Exception as exc:
            log.warning("AttributionEngine.classifier_stats: DB error: %s", exc)

        # Compute avg predicted confidence per tag using stored feature vectors.
        tag_conf_sums: dict[str, float] = {}
        tag_conf_counts: dict[str, int] = {}
        try:
            rows_fv = get_inferences(limit=5000, include_dismissed=False)
            for inf in rows_fv:
                evidence = inf.get("evidence") or {}
                if isinstance(evidence, str):
                    import json as _json  # pylint: disable=import-outside-toplevel
                    try:
                        evidence = _json.loads(evidence)
                    except Exception:
                        continue
                fv_dict = evidence.get("feature_vector")
                if not fv_dict:
                    continue
                features = {k: v for k, v in fv_dict.items()
                            if k != "timestamp" and v is not None}
                if not features:
                    continue
                try:
                    proba = self._ml_model.predict_proba_one(features)
                    if proba:
                        for label, conf in proba.items():
                            tag_conf_sums[label] = tag_conf_sums.get(label, 0.0) + conf
                            tag_conf_counts[label] = tag_conf_counts.get(label, 0) + 1
                except Exception:
                    pass
        except Exception as exc:
            log.warning("AttributionEngine.classifier_stats: confidence calc error: %s", exc)

        tag_stats = []
        for fp in self._fingerprints:
            count = tag_counts.get(fp.id, 0)
            ready = count >= _READY_THRESHOLD
            avg_conf = None
            n_conf = tag_conf_counts.get(fp.id, 0)
            if ready and n_conf > 0:
                avg_conf = round(tag_conf_sums.get(fp.id, 0.0) / n_conf, 3)
            tag_stats.append({
                "tag": fp.id,
                "label": fp.label,
                "sample_count": count,
                "avg_confidence": avg_conf,
                "ready": ready,
            })

        return {
            "total_samples": sum(tag_counts.values()),
            "tag_stats": tag_stats,
        }

    def _ml_predict(self, fv: FeatureVector):
        """Get ML prediction for FV."""
        features = dataclasses.asdict(fv)
        features.pop("timestamp", None)
        features = {k: v for k, v in features.items() if v is not None}
        try:
            pred = self._ml_model.predict_proba_one(features)
            if pred:
                best_label = max(pred, key=pred.get)
                return best_label, pred[best_label]
        except Exception:
            pass
        return None, 0.0

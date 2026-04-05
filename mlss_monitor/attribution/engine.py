"""AttributionEngine: scores fingerprints against a FeatureVector, returns top match."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from river import linear_model, preprocessing  # pylint: disable=import-error

from mlss_monitor.attribution.loader import Fingerprint, load_fingerprints
from mlss_monitor.attribution.scorer import combine, sensor_score, temporal_score
from mlss_monitor.feature_vector import FeatureVector

log = logging.getLogger(__name__)

# If the runner-up confidence is within this delta of the primary, it is surfaced.
_RUNNER_UP_DELTA = 0.15


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
        self._fingerprints: list[Fingerprint] = load_fingerprints(self._config_path)
        self._ml_model = preprocessing.StandardScaler() | linear_model.LogisticRegression()
        log.info(
            "AttributionEngine: loaded %d fingerprints from %s",
            len(self._fingerprints),
            self._config_path.name,
        )

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
        if ml_label and ml_conf > 0.5:  # Some threshold
            # Hybrid: 0.6 fingerprint + 0.4 ML
            if best_fp.label == ml_label:
                best_conf = 0.6 * best_conf + 0.4 * ml_conf
            else:
                # If disagree, lower confidence
                best_conf = 0.6 * best_conf

        if best_conf < best_fp.confidence_floor:
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
            from database.db_logger import get_inferences  # pylint: disable=import-outside-toplevel
            # Get recent inferences with tags
            rows = get_inferences(limit=100, include_dismissed=False)
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
                from mlss_monitor.feature_vector import FeatureVector  # pylint: disable=import-outside-toplevel
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
                # TODO: adjust weights based on confusion
        except Exception as exc:
            log.warning("AttributionEngine: evaluation error: %s", exc)

    def train_on_tags(self):
        """Train ML model on tagged events."""
        try:
            from database.db_logger import get_inferences  # pylint: disable=import-outside-toplevel
            rows = get_inferences(limit=1000, include_dismissed=False)
            tagged = [r for r in rows if r.get("tags")]
            trained = 0
            for inf in tagged:
                evidence = inf.get("evidence", {})
                if not isinstance(evidence, dict):
                    continue
                fv_dict = evidence.get("feature_vector")
                if not fv_dict:
                    continue
                # Use FV fields as features, tag as target
                features = {k: v for k, v in fv_dict.items() if k != "timestamp" and v is not None}
                for tag in inf["tags"]:
                    self._ml_model.learn_one(features, tag["tag"])
                    trained += 1
            if trained:
                log.info("AttributionEngine: trained on %d tagged samples", trained)
        except Exception as exc:
            log.warning("AttributionEngine: training error: %s", exc)

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

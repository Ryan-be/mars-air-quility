"""AttributionEngine: scores fingerprints against a FeatureVector, returns top match."""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

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
        log.info(
            "AttributionEngine: loaded %d fingerprints from %s",
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

        # Fill description template (gracefully handle missing fields)
        fv_dict = dataclasses.asdict(fv)

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

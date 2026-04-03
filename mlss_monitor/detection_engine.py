"""DetectionEngine: orchestrates RuleEngine + AnomalyDetector → inferences.

In dry_run=True (shadow) mode: evaluates rules and logs what would fire,
but never calls save_inference. Used during parallel validation against
the old inference_engine.

In dry_run=False (live) mode: calls save_inference for each event.
Switch mode by changing the dry_run flag in app.py once parity is confirmed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from database.db_logger import (
    DB_FILE,
    get_recent_inference_by_type,
    save_inference,
)
from mlss_monitor.anomaly_detector import AnomalyDetector
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.rule_engine import RuleEngine

log = logging.getLogger(__name__)


class DetectionEngine:
    """Orchestrates RuleEngine + AnomalyDetector.

    dry_run=True: shadow/validation mode — logs what would fire, no DB writes.
    dry_run=False: live mode — calls save_inference for each event.
    """

    def __init__(
        self,
        rules_path: str | Path,
        anomaly_config_path: str | Path,
        model_dir: str | Path,
        dry_run: bool = True,
    ) -> None:
        self._dry_run = dry_run
        self._rule_engine = RuleEngine(rules_path)
        self._anomaly_detector = AnomalyDetector(anomaly_config_path, model_dir)

    # ── Short-term detection (call at _CYCLE_60S) ─────────────────────────────

    def run(self, fv: FeatureVector) -> list[str]:
        """Evaluate threshold rules + anomaly detector against the FeatureVector.

        Returns a list of event_type strings that fired (for shadow-mode logging).
        In dry_run=True mode, never calls save_inference.
        In dry_run=False mode, calls save_inference for each new event (respects
        dedupe window via get_recent_inference_by_type).
        """
        fired: list[str] = []

        # 1. Threshold rule events
        matches = self._rule_engine.evaluate(fv)
        for match in matches:
            if get_recent_inference_by_type(match.event_type, hours=match.dedupe_hours):
                continue  # within dedupe window
            fired.append(match.event_type)
            if not self._dry_run:
                try:
                    save_inference(
                        event_type=match.event_type,
                        severity=match.severity,
                        title=match.title,
                        description=match.description,
                        action=match.action,
                        evidence={"fv_timestamp": fv.timestamp.isoformat()},
                        confidence=match.confidence,
                    )
                except Exception as exc:
                    log.error(
                        "DetectionEngine: save_inference failed for %r: %s",
                        match.event_type,
                        exc,
                    )

        # 2. Anomaly detection
        scores = self._anomaly_detector.learn_and_score(fv)
        anomalous = self._anomaly_detector.anomalous_channels(scores)
        for ch in anomalous:
            event_type = f"anomaly_{ch}"
            if get_recent_inference_by_type(event_type, hours=1):
                continue
            score = scores[ch]
            fired.append(event_type)
            if not self._dry_run:
                try:
                    save_inference(
                        event_type=event_type,
                        severity="warning",
                        title=f"Statistical anomaly: {ch.replace('_', ' ')} (score {score:.2f})",
                        description=(
                            f"A statistical anomaly was detected in {ch} with a score of "
                            f"{score:.2f} (threshold 0.7). This reading is unusual compared "
                            f"to the learned historical pattern for this sensor channel."
                        ),
                        action=(
                            "Monitor the sensor. If readings persist unusually, "
                            "investigate the cause or reset the anomaly model for this channel."
                        ),
                        evidence={"channel": ch, "anomaly_score": round(score, 4)},
                        confidence=round(score, 2),
                    )
                except Exception as exc:
                    log.error(
                        "DetectionEngine: save_inference failed for anomaly %r: %s",
                        ch,
                        exc,
                    )

        if fired:
            mode = "DRY-RUN" if self._dry_run else "LIVE"
            log.info("[DetectionEngine][%s] fired: %s", mode, fired)

        return fired

    # ── Long-term summaries (call at _CYCLE_1H / _CYCLE_24H) ─────────────────

    def run_hourly(self, fv: FeatureVector) -> None:
        """Run hourly summary — populated in Task 5."""
        pass

    def run_daily(self, fv: FeatureVector) -> None:
        """Run daily summaries — populated in Task 5."""
        pass

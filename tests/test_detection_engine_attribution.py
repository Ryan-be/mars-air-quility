"""Tests that DetectionEngine.run() injects attribution evidence when dry_run=False."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from mlss_monitor.detection_engine import DetectionEngine
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _write_rules(tmp_path: Path) -> Path:
    rules = {
        "rules": [
            {
                "id": "tvoc_spike",
                "expression": "tvoc_peak_ratio > 1.5 and tvoc_current > 200",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "confidence": 0.8,
                "dedupe_hours": 1,
                "title_template": "TVOC spike ({tvoc_current:.0f} ppb)",
                "description_template": "TVOC elevated to {tvoc_current:.0f} ppb.",
                "action": "Ventilate.",
            }
        ]
    }
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.dump(rules))
    return p


def _write_anomaly(tmp_path: Path) -> Path:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,
            "model_dir": str(tmp_path / "models"),
            "channels": [],  # no channels — anomaly won't fire
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _write_fingerprints(tmp_path: Path) -> Path:
    cfg = {
        "sources": [
            {
                "id": "chemical_offgassing",
                "label": "Chemical off-gassing",
                "description": "VOC without particles",
                "examples": "paint, cleaning products",
                "sensors": {"tvoc": "elevated", "pm25": "absent"},
                "temporal": {},
                "confidence_floor": 0.4,
                "description_template": "TVOC at {tvoc_current:.0f} ppb, no PM2.5.",
                "action_template": "Ventilate.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _make_engine(tmp_path, dry_run=False) -> DetectionEngine:
    return DetectionEngine(
        rules_path=_write_rules(tmp_path),
        anomaly_config_path=_write_anomaly(tmp_path),
        model_dir=tmp_path / "models",
        fingerprints_path=_write_fingerprints(tmp_path),
        dry_run=dry_run,
    )


def _fv_tvoc_spike() -> FeatureVector:
    return FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
        tvoc_elevated_minutes=5.0,
        pm25_current=None,
        pm25_peak_ratio=None,
    )


def test_detection_engine_accepts_fingerprints_path(tmp_path):
    """DetectionEngine.__init__ accepts a fingerprints_path parameter."""
    engine = _make_engine(tmp_path)
    assert engine is not None


def test_run_injects_attribution_evidence_when_live(tmp_path):
    """In dry_run=False mode, save_inference is called with attribution keys in evidence."""
    engine = _make_engine(tmp_path, dry_run=False)
    fv = _fv_tvoc_spike()

    saved_calls = []

    def fake_save(**kwargs):
        saved_calls.append(kwargs)

    def fake_get_recent(event_type, hours):
        return None  # no dedupe — let it fire

    with patch("mlss_monitor.detection_engine.save_inference", fake_save), \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        engine.run(fv)

    assert len(saved_calls) >= 1
    call = saved_calls[0]
    evidence = call["evidence"]
    assert "attribution" in evidence
    assert "attribution_confidence" in evidence


def test_run_dry_run_does_not_call_save_inference(tmp_path):
    """In dry_run=True mode, save_inference is never called."""
    engine = _make_engine(tmp_path, dry_run=True)
    fv = _fv_tvoc_spike()

    saved_calls = []

    def fake_save(**kwargs):
        saved_calls.append(kwargs)

    def fake_get_recent(event_type, hours):
        return None

    with patch("mlss_monitor.detection_engine.save_inference", fake_save), \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        engine.run(fv)

    assert len(saved_calls) == 0


def test_run_without_fingerprints_path_still_works(tmp_path):
    """DetectionEngine works fine when fingerprints_path is not provided."""
    engine = DetectionEngine(
        rules_path=_write_rules(tmp_path),
        anomaly_config_path=_write_anomaly(tmp_path),
        model_dir=tmp_path / "models",
        dry_run=True,
    )
    fv = _fv_tvoc_spike()

    def fake_get_recent(event_type, hours):
        return None

    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", fake_get_recent):
        fired = engine.run(fv)
    assert isinstance(fired, list)

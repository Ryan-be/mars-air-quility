"""Tests for DetectionEngine: rule + anomaly orchestration, dry-run mode."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.detection_engine import DetectionEngine


def _make_fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=datetime.now(timezone.utc), **kwargs)


def _write_minimal_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (rules_path, anomaly_path, model_dir)."""
    rules = {
        "rules": [
            {
                "id": "tvoc_test",
                "expression": "tvoc_current > 100",
                "event_type": "tvoc_spike",
                "severity": "warning",
                "dedupe_hours": 1,
                "confidence": 0.8,
                "title_template": "TVOC {tvoc_current:.0f} ppb",
                "description_template": "TVOC is {tvoc_current:.0f} ppb.",
                "action": "Ventilate.",
            }
        ]
    }
    anomaly = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,
            "model_dir": str(tmp_path / "models"),
            "channels": ["tvoc_ppb"],
        }
    }
    rules_path = tmp_path / "rules.yaml"
    anomaly_path = tmp_path / "anomaly.yaml"
    model_dir = tmp_path / "models"
    rules_path.write_text(yaml.dump(rules))
    anomaly_path.write_text(yaml.dump(anomaly))
    return rules_path, anomaly_path, model_dir


# ── dry_run=True (shadow mode) ────────────────────────────────────────────────

def test_run_dry_run_does_not_call_save_inference(tmp_path):
    """In dry_run=True mode, save_inference must never be called."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=300.0)  # triggers tvoc_test rule
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=False):
        engine.run(fv)
        mock_save.assert_not_called()


def test_run_dry_run_returns_fired_event_types(tmp_path):
    """dry_run=True mode returns the list of event types that would fire."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=False):
        fired = engine.run(fv)
    assert "tvoc_spike" in fired


def test_run_dry_run_returns_empty_when_no_rules_fire(tmp_path):
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv(tvoc_current=50.0)  # below threshold
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=False):
        fired = engine.run(fv)
    assert "tvoc_spike" not in fired


# ── dry_run=False (live mode) ─────────────────────────────────────────────────

def test_run_live_calls_save_inference_when_rule_fires(tmp_path):
    """In dry_run=False mode, save_inference is called for each matched rule."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=False)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=False):
        engine.run(fv)
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["event_type"] == "tvoc_spike"


def test_run_live_skips_event_within_dedupe_window(tmp_path):
    """If get_recent_inference_by_type returns True, rule must not re-fire."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=False)

    fv = _make_fv(tvoc_current=300.0)
    with patch("mlss_monitor.detection_engine.save_inference") as mock_save, \
         patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=True):
        engine.run(fv)
        mock_save.assert_not_called()


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_run_with_none_feature_vector_does_not_raise(tmp_path):
    """Passing an empty FeatureVector (all None) must not raise."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    fv = _make_fv()  # all fields None
    with patch("mlss_monitor.detection_engine.get_recent_inference_by_type", return_value=False):
        fired = engine.run(fv)
    assert fired == []

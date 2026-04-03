"""Tests for AnomalyDetector: river HalfSpaceTrees, scoring, persistence."""
from __future__ import annotations

import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.anomaly_detector import AnomalyDetector


def _make_fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=datetime.now(timezone.utc), **kwargs)


def _write_config(tmp_path: Path) -> Path:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,  # small for tests
            "model_dir": str(tmp_path / "models"),
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(cfg))
    return p


# ── Initialisation ────────────────────────────────────────────────────────────

def test_anomaly_detector_creates_model_dir(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    AnomalyDetector(cfg_path, model_dir)
    assert model_dir.exists()


def test_anomaly_detector_initialises_models_for_channels(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)
    assert "tvoc_ppb" in det._models
    assert "eco2_ppm" in det._models


# ── learn_and_score ───────────────────────────────────────────────────────────

def test_learn_and_score_returns_none_during_cold_start(tmp_path):
    """Scores must be None until cold_start_readings threshold is reached."""
    cfg_path = _write_config(tmp_path)  # cold_start = 5
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    # Call 4 times (below cold_start=5)
    for _ in range(4):
        scores = det.learn_and_score(fv)
    assert scores["tvoc_ppb"] is None
    assert scores["eco2_ppm"] is None


def test_learn_and_score_returns_float_after_cold_start(tmp_path):
    """After cold_start_readings, scores are floats between 0 and 1."""
    cfg_path = _write_config(tmp_path)  # cold_start = 5
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    scores = None
    for _ in range(6):  # past cold_start=5
        scores = det.learn_and_score(fv)

    assert scores is not None
    assert isinstance(scores["tvoc_ppb"], float)
    assert 0.0 <= scores["tvoc_ppb"] <= 1.0


def test_learn_and_score_returns_none_for_none_field(tmp_path):
    """If a FeatureVector field is None, score for that channel is None."""
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=None, eco2_current=600.0)
    scores = det.learn_and_score(fv)
    assert scores["tvoc_ppb"] is None  # no value → no score


# ── anomalous_channels ────────────────────────────────────────────────────────

def test_anomalous_channels_filters_by_threshold(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    scores = {"tvoc_ppb": 0.8, "eco2_ppm": 0.3}  # threshold=0.7
    anomalous = det.anomalous_channels(scores)
    assert "tvoc_ppb" in anomalous
    assert "eco2_ppm" not in anomalous


def test_anomalous_channels_excludes_none_scores(tmp_path):
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    scores = {"tvoc_ppb": None, "eco2_ppm": 0.9}
    anomalous = det.anomalous_channels(scores)
    assert "tvoc_ppb" not in anomalous
    assert "eco2_ppm" in anomalous


# ── Persistence ───────────────────────────────────────────────────────────────

def test_models_are_saved_and_reloaded(tmp_path):
    """After training, reloading AnomalyDetector restores n_seen."""
    cfg_path = _write_config(tmp_path)
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    fv = _make_fv(tvoc_current=100.0, eco2_current=600.0)
    for _ in range(3):
        det.learn_and_score(fv)

    # Reload
    det2 = AnomalyDetector(cfg_path, model_dir)
    assert det2._n_seen["tvoc_ppb"] == 3
    assert det2._n_seen["eco2_ppm"] == 3

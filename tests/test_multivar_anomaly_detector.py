# tests/test_multivar_anomaly_detector.py
import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.multivar_anomaly_detector import MultivarAnomalyDetector


def _config(tmp_path):
    cfg = tmp_path / "multivar_anomaly.yaml"
    cfg.write_text("""
multivar_anomaly:
  threshold: 0.75
  cold_start_readings: 5
  models:
    - id: test_model
      label: "Test model"
      description: "For testing."
      channels:
        - co_current
        - no2_current
""")
    return str(cfg)


def _fv(co=10.0, no2=5.0):
    return FeatureVector(timestamp=datetime.now(timezone.utc), co_current=co, no2_current=no2)


def test_learn_and_score_returns_none_before_cold_start(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    fv = _fv()
    scores = det.learn_and_score(fv)
    # Only 1 reading — cold_start=5 — should be None
    assert scores["test_model"] is None


def test_learn_and_score_returns_float_after_cold_start(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(6):
        scores = det.learn_and_score(_fv())
    assert isinstance(scores["test_model"], float)


def test_skips_reading_when_channel_is_none(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    fv = FeatureVector(timestamp=datetime.now(timezone.utc), co_current=10.0, no2_current=None)  # missing channel
    scores = det.learn_and_score(fv)
    assert scores["test_model"] is None


def test_anomalous_models_returns_ids_above_threshold(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    # Feed 6 identical readings then one different value
    for _ in range(6):
        det.learn_and_score(_fv(co=10.0, no2=5.0))
    scores = {"test_model": 0.9}
    result = det.anomalous_models(scores)
    assert "test_model" in result


def test_anomalous_models_excludes_below_threshold(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    scores = {"test_model": 0.3}
    assert det.anomalous_models(scores) == []


def test_baseline_returns_ema_after_readings(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(10):
        det.learn_and_score(_fv(co=10.0, no2=5.0))
    b = det.baselines("test_model")
    assert b["co_current"] is not None
    assert 8.0 < b["co_current"] < 12.0  # EMA should be near 10


def test_model_channels_returns_channel_list(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det.model_channels("test_model") == ["co_current", "no2_current"]


def test_model_label_returns_label(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det.model_label("test_model") == "Test model"


def test_pickle_persistence_survives_restart(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    for _ in range(6):
        det.learn_and_score(_fv())
    det._save_models()

    det2 = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    assert det2._n_seen["test_model"] == 6


def test_bootstrap_warms_model(tmp_path):
    det = MultivarAnomalyDetector(_config(tmp_path), tmp_path)
    channel_data = {
        "test_model": [
            {"co_current": 10.0, "no2_current": 5.0}
            for _ in range(10)
        ]
    }
    det.bootstrap(channel_data)
    assert det._n_seen["test_model"] == 10

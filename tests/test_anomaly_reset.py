"""Tests for AnomalyDetector.reset_channel() and live_scores()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_anomaly_config(path: Path) -> None:
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 2,
            "channels": ["tvoc_ppb", "eco2_ppm"],
        }
    }
    path.write_text(yaml.dump(cfg))


def test_reset_channel_clears_model_and_n_seen(tmp_path):
    """reset_channel reinitialises model and sets n_seen to 0."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")

    # Feed some readings so n_seen > 0
    det._n_seen["tvoc_ppb"] = 100
    det._save_models()
    assert (tmp_path / "models" / "tvoc_ppb.pkl").exists()

    det.reset_channel("tvoc_ppb")

    assert det._n_seen.get("tvoc_ppb", 0) == 0
    assert not (tmp_path / "models" / "tvoc_ppb.pkl").exists()


def test_reset_channel_unknown_channel_no_error(tmp_path):
    """reset_channel on an unknown channel name does not raise."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")
    det.reset_channel("nonexistent_channel")   # must not raise


def test_live_scores_returns_dict_per_channel(tmp_path):
    """live_scores() returns a dict keyed by channel name."""
    from mlss_monitor.anomaly_detector import AnomalyDetector

    cfg_path = tmp_path / "anomaly.yaml"
    _write_anomaly_config(cfg_path)
    det = AnomalyDetector(cfg_path, tmp_path / "models")

    # Before any readings EMA is empty — values should be None
    scores = det.live_scores()
    assert isinstance(scores, dict)
    assert "tvoc_ppb" in scores
    assert scores["tvoc_ppb"] is None   # no readings yet

    # After seeding EMA
    det._ema["tvoc_ppb"] = 350.0
    det._n_seen["tvoc_ppb"] = 600
    scores = det.live_scores()
    assert scores["tvoc_ppb"] == pytest.approx(350.0)

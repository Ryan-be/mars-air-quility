"""Tests for AnomalyDetector.bootstrap() and DetectionEngine.bootstrap_from_db()."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from mlss_monitor.anomaly_detector import AnomalyDetector
from mlss_monitor.detection_engine import DetectionEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_anomaly_config(tmp_path: Path, channels: list[str] | None = None) -> Path:
    if channels is None:
        channels = ["tvoc_ppb", "eco2_ppm"]
    cfg = {
        "anomaly": {
            "algorithm": "half_space_trees",
            "score_threshold": 0.7,
            "cold_start_readings": 5,
            "model_dir": str(tmp_path / "models"),
            "channels": channels,
        }
    }
    p = tmp_path / "anomaly.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _write_minimal_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (rules_path, anomaly_path, model_dir) suitable for DetectionEngine."""
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
            "channels": ["tvoc_ppb", "eco2_ppm", "temperature_c"],
        }
    }
    rules_path = tmp_path / "rules.yaml"
    anomaly_path = tmp_path / "anomaly.yaml"
    model_dir = tmp_path / "models"
    rules_path.write_text(yaml.dump(rules))
    anomaly_path.write_text(yaml.dump(anomaly))
    return rules_path, anomaly_path, model_dir


def _make_test_db(db_path: str) -> None:
    """Create a minimal SQLite DB matching the spec in the task description."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE hot_tier (
            timestamp TEXT,
            tvoc_ppb REAL, eco2_ppm REAL, temperature_c REAL,
            humidity_pct REAL, pm1_ug_m3 REAL, pm25_ug_m3 REAL, pm10_ug_m3 REAL,
            co_ppb REAL, no2_ppb REAL, nh3_ppb REAL
        );
        CREATE TABLE sensor_data (
            timestamp TEXT, tvoc INTEGER, eco2 INTEGER
        );
        INSERT INTO hot_tier VALUES
            ('2026-01-01 00:00:00', 120.0, 550.0, 22.0, 45.0, 3.0, 5.0, 8.0, 50.0, 10.0, 2.0);
        INSERT INTO sensor_data VALUES
            ('2025-12-31 00:00:00', 100, 500);
    """)
    conn.commit()
    conn.close()


# ── AnomalyDetector.bootstrap() ───────────────────────────────────────────────

def test_bootstrap_feeds_values_into_model(tmp_path):
    """bootstrap() should increment n_seen for each value fed per channel."""
    cfg_path = _write_anomaly_config(tmp_path, channels=["tvoc_ppb", "eco2_ppm"])
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    channel_data = {
        "tvoc_ppb": [100.0, 110.0, 120.0],
        "eco2_ppm": [500.0, 520.0],
    }
    det.bootstrap(channel_data)

    assert det._n_seen["tvoc_ppb"] == 3
    assert det._n_seen["eco2_ppm"] == 2


def test_bootstrap_skips_unknown_channels(tmp_path):
    """bootstrap() should silently skip channels not present in _models."""
    cfg_path = _write_anomaly_config(tmp_path, channels=["tvoc_ppb"])
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    # eco2_ppm and unknown_channel are not in _models (only tvoc_ppb is)
    channel_data = {
        "tvoc_ppb": [100.0, 200.0],
        "eco2_ppm": [500.0],          # not in channels list → not in _models
        "unknown_channel": [1.0, 2.0],
    }
    det.bootstrap(channel_data)

    assert det._n_seen["tvoc_ppb"] == 2
    # eco2_ppm and unknown_channel were skipped — no key created by bootstrap
    assert "unknown_channel" not in det._models


def test_bootstrap_saves_models(tmp_path):
    """bootstrap() should call _save_models() once after processing all channels."""
    cfg_path = _write_anomaly_config(tmp_path, channels=["tvoc_ppb"])
    model_dir = tmp_path / "models"
    det = AnomalyDetector(cfg_path, model_dir)

    with patch.object(det, "_save_models") as mock_save:
        det.bootstrap({"tvoc_ppb": [1.0, 2.0, 3.0]})
        mock_save.assert_called_once()


# ── DetectionEngine.bootstrap_from_db() ──────────────────────────────────────

def test_detection_engine_bootstrap_from_db(tmp_path):
    """bootstrap_from_db() should feed DB rows into the anomaly detector."""
    rules_path, anomaly_path, model_dir = _write_minimal_configs(tmp_path)
    db_path = str(tmp_path / "test.db")
    _make_test_db(db_path)

    engine = DetectionEngine(rules_path, anomaly_path, model_dir, dry_run=True)

    # n_seen should be 0 before bootstrapping
    assert engine._anomaly_detector._n_seen["tvoc_ppb"] == 0

    engine.bootstrap_from_db(db_path)

    # hot_tier has 1 row for tvoc_ppb, sensor_data has 1 row for tvoc → total 2
    assert engine._anomaly_detector._n_seen["tvoc_ppb"] > 0
    # eco2_ppm likewise: 1 hot_tier + 1 sensor_data = 2
    assert engine._anomaly_detector._n_seen["eco2_ppm"] > 0
    # temperature_c has 1 hot_tier row
    assert engine._anomaly_detector._n_seen["temperature_c"] > 0

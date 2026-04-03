"""Tests for fingerprint YAML loader."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


def _write_valid_yaml(tmp_path: Path) -> Path:
    cfg = {
        "sources": [
            {
                "id": "test_source",
                "label": "Test Source",
                "description": "A test fingerprint",
                "examples": "test",
                "sensors": {"tvoc": "elevated", "pm25": "normal"},
                "temporal": {"rise_rate": "fast"},
                "confidence_floor": 0.6,
                "description_template": "TVOC: {tvoc_current:.0f}",
                "action_template": "Do something.",
            }
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _write_malformed_yaml(tmp_path: Path) -> Path:
    """A fingerprint missing required 'id' field."""
    cfg = {
        "sources": [
            {"label": "No ID"},  # missing 'id'
            {
                "id": "valid_source",
                "label": "Valid",
                "description": "Valid",
                "examples": "valid",
                "sensors": {"tvoc": "elevated"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "",
                "action_template": "",
            },
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def test_loader_returns_fingerprints(tmp_path):
    """load_fingerprints() returns a list of Fingerprint objects."""
    from mlss_monitor.attribution.loader import load_fingerprints, Fingerprint

    cfg_path = _write_valid_yaml(tmp_path)
    fingerprints = load_fingerprints(cfg_path)
    assert len(fingerprints) == 1
    assert fingerprints[0].id == "test_source"
    assert fingerprints[0].label == "Test Source"
    assert fingerprints[0].confidence_floor == pytest.approx(0.6)


def test_loader_skips_malformed_fingerprint(tmp_path):
    """load_fingerprints() skips entries missing required fields, keeps valid ones."""
    from mlss_monitor.attribution.loader import load_fingerprints

    cfg_path = _write_malformed_yaml(tmp_path)
    fingerprints = load_fingerprints(cfg_path)
    assert len(fingerprints) == 1
    assert fingerprints[0].id == "valid_source"


def test_loader_raises_on_missing_file(tmp_path):
    """load_fingerprints() raises FileNotFoundError if file does not exist."""
    from mlss_monitor.attribution.loader import load_fingerprints

    with pytest.raises(FileNotFoundError):
        load_fingerprints(tmp_path / "nonexistent.yaml")


def test_fingerprint_has_sensor_and_temporal_dicts(tmp_path):
    """Fingerprint.sensors and .temporal are dicts preserved from YAML."""
    from mlss_monitor.attribution.loader import load_fingerprints

    cfg_path = _write_valid_yaml(tmp_path)
    fp = load_fingerprints(cfg_path)[0]
    assert fp.sensors == {"tvoc": "elevated", "pm25": "normal"}
    assert fp.temporal == {"rise_rate": "fast"}

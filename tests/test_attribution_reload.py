"""Tests for AttributionEngine.reload()."""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest


def _write_fingerprints(path: Path, sources: list[dict]) -> None:
    path.write_text(yaml.dump({"sources": sources}))


_BASE_FP = {
    "id": "test_fp",
    "label": "Test",
    "description": "A test fingerprint",
    "examples": "test",
    "sensors": {"tvoc": "elevated"},
    "temporal": {"rise_rate": "fast"},
    "confidence_floor": 0.5,
    "description_template": "TVOC: {tvoc_current:.0f}",
    "action_template": "Do something.",
}


def test_reload_picks_up_new_fingerprint(tmp_path):
    """reload() loads a fingerprint added to YAML after initial startup."""
    from mlss_monitor.attribution.engine import AttributionEngine

    fp_path = tmp_path / "fingerprints.yaml"
    _write_fingerprints(fp_path, [_BASE_FP])
    engine = AttributionEngine(fp_path)
    assert len(engine._fingerprints) == 1

    second = {**_BASE_FP, "id": "second_fp", "label": "Second"}
    _write_fingerprints(fp_path, [_BASE_FP, second])
    engine.reload()
    assert len(engine._fingerprints) == 2
    assert engine._fingerprints[1].id == "second_fp"


def test_reload_reflects_deletion(tmp_path):
    """reload() removes fingerprints deleted from YAML."""
    from mlss_monitor.attribution.engine import AttributionEngine

    fp_path = tmp_path / "fingerprints.yaml"
    _write_fingerprints(fp_path, [_BASE_FP])
    engine = AttributionEngine(fp_path)
    assert len(engine._fingerprints) == 1

    _write_fingerprints(fp_path, [])
    engine.reload()
    assert len(engine._fingerprints) == 0

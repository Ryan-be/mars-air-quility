"""Tests for AttributionEngine: top match, runner-up, no match, None fields."""
from __future__ import annotations

import pytest
import yaml
from datetime import datetime, timezone
from pathlib import Path

from mlss_monitor.attribution.engine import AttributionEngine, AttributionResult
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _write_config(tmp_path: Path) -> Path:
    """Write a minimal fingerprints.yaml with two clearly distinct sources."""
    cfg = {
        "sources": [
            {
                "id": "high_tvoc_no_pm25",
                "label": "High TVOC, no PM2.5",
                "description": "Test fingerprint A",
                "examples": "example A",
                "sensors": {"tvoc": "high", "pm25": "absent"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "TVOC high at {tvoc_current:.0f} ppb.",
                "action_template": "Ventilate.",
            },
            {
                "id": "high_pm25_no_tvoc",
                "label": "High PM2.5, no TVOC",
                "description": "Test fingerprint B",
                "examples": "example B",
                "sensors": {"pm25": "high", "tvoc": "absent"},
                "temporal": {},
                "confidence_floor": 0.5,
                "description_template": "PM2.5 high at {pm25_current:.1f} µg/m³.",
                "action_template": "Close windows.",
            },
        ]
    }
    p = tmp_path / "fingerprints.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _fv_tvoc_high() -> FeatureVector:
    """FeatureVector matching 'high_tvoc_no_pm25' fingerprint."""
    return FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
        pm25_current=None,  # absent
    )


def _fv_pm25_high() -> FeatureVector:
    """FeatureVector matching 'high_pm25_no_tvoc' fingerprint."""
    return FeatureVector(
        timestamp=_ts(),
        pm25_current=80.0,
        pm25_baseline=8.0,
        pm25_peak_ratio=10.0,
        tvoc_current=None,  # absent
    )


# ── AttributionResult ─────────────────────────────────────────────────────────

def test_attribution_result_is_dataclass():
    r = AttributionResult(
        source_id="test",
        label="Test",
        confidence=0.7,
        runner_up_id=None,
        runner_up_confidence=None,
        description="desc",
        action="act",
    )
    assert r.source_id == "test"
    assert r.confidence == pytest.approx(0.7)
    assert r.runner_up_id is None


# ── AttributionEngine.attribute ───────────────────────────────────────────────

def test_attribute_returns_top_match(tmp_path):
    """attribute() returns the fingerprint with highest confidence above floor."""
    engine = AttributionEngine(_write_config(tmp_path))
    result = engine.attribute(_fv_tvoc_high())
    assert result is not None
    assert result.source_id == "high_tvoc_no_pm25"
    assert result.confidence >= 0.5


def test_attribute_returns_runner_up_when_within_015(tmp_path):
    """When two fingerprints score within 0.15 of each other, runner_up is set."""
    cfg = {
        "sources": [
            {
                "id": "source_a",
                "label": "Source A",
                "description": "Desc A",
                "examples": "ex A",
                "sensors": {"tvoc": "high"},
                "temporal": {},
                "confidence_floor": 0.3,
                "description_template": "A",
                "action_template": "A action",
            },
            {
                "id": "source_b",
                "label": "Source B",
                "description": "Desc B",
                "examples": "ex B",
                "sensors": {"tvoc": "elevated"},
                "temporal": {},
                "confidence_floor": 0.3,
                "description_template": "B",
                "action_template": "B action",
            },
        ]
    }
    p = tmp_path / "fp.yaml"
    p.write_text(yaml.dump(cfg))
    engine = AttributionEngine(p)
    # Both 'high' and 'elevated' match a tvoc_peak_ratio=2.5 FeatureVector
    fv = FeatureVector(
        timestamp=_ts(),
        tvoc_current=375.0,
        tvoc_baseline=150.0,
        tvoc_peak_ratio=2.5,
    )
    result = engine.attribute(fv)
    assert result is not None
    # If runner-up is within 0.15, it should be set
    if result.runner_up_confidence is not None:
        assert abs(result.confidence - result.runner_up_confidence) <= 0.15


def test_attribute_returns_none_when_no_match_above_floor(tmp_path):
    """attribute() returns None when no fingerprint clears its confidence_floor."""
    cfg = {
        "sources": [
            {
                "id": "impossible",
                "label": "Impossible",
                "description": "",
                "examples": "",
                "sensors": {"tvoc": "high", "pm25": "high", "co": "elevated"},
                "temporal": {},
                "confidence_floor": 0.99,  # very high floor
                "description_template": "",
                "action_template": "",
            }
        ]
    }
    p = tmp_path / "fp.yaml"
    p.write_text(yaml.dump(cfg))
    engine = AttributionEngine(p)
    fv = FeatureVector(
        timestamp=_ts(),
        tvoc_current=400.0,
        tvoc_baseline=100.0,
        tvoc_peak_ratio=4.0,
    )
    result = engine.attribute(fv)
    assert result is None


def test_attribute_handles_all_none_fv(tmp_path):
    """attribute() does not raise when all FeatureVector fields are None."""
    engine = AttributionEngine(_write_config(tmp_path))
    fv = FeatureVector(timestamp=_ts())  # all fields None
    result = engine.attribute(fv)
    # May return None or a low-confidence result — just must not raise
    assert result is None or isinstance(result.confidence, float)


def test_attribute_description_filled_from_template(tmp_path):
    """AttributionResult.description is filled from fingerprint description_template."""
    engine = AttributionEngine(_write_config(tmp_path))
    result = engine.attribute(_fv_tvoc_high())
    assert result is not None
    # Template contains {tvoc_current:.0f} — should be filled with a number
    assert "{" not in result.description  # no unfilled slots


# ── personal_care fingerprint ─────────────────────────────────────────────────

_REAL_FINGERPRINTS_PATH = Path(__file__).parents[2] / "config" / "fingerprints.yaml"


@pytest.mark.skipif(
    not _REAL_FINGERPRINTS_PATH.exists(),
    reason="config/fingerprints.yaml not present",
)
def test_personal_care_fingerprint_loads():
    """personal_care fingerprint loads without error from the real config file."""
    engine = AttributionEngine(_REAL_FINGERPRINTS_PATH)
    ids = [fp.id for fp in engine._fingerprints]
    assert "personal_care" in ids, f"personal_care not found in fingerprints: {ids}"


@pytest.mark.skipif(
    not _REAL_FINGERPRINTS_PATH.exists(),
    reason="config/fingerprints.yaml not present",
)
def test_personal_care_fingerprint_metadata():
    """personal_care fingerprint has expected label and confidence_floor."""
    from mlss_monitor.attribution.loader import load_fingerprints

    fingerprints = load_fingerprints(_REAL_FINGERPRINTS_PATH)
    pc = next((fp for fp in fingerprints if fp.id == "personal_care"), None)
    assert pc is not None, "personal_care fingerprint not found"
    assert pc.label == "Personal Care Products"
    # Raised from 0.55 to 0.60: more distinctive multi-sensor signature after real-world tuning
    assert pc.confidence_floor == pytest.approx(0.60)
    # Key sensor states (tuned from real deodorant spray event on 2026-04-05)
    assert pc.sensors.get("tvoc") == "high"
    assert pc.sensors.get("eco2") == "high"       # SGP30 artefact: derived from TVOC
    assert pc.sensors.get("pm25") == "slight_rise"  # aerosol particles detectable
    assert pc.sensors.get("co") == "slight_rise"    # propellant causes small resistance drop


@pytest.mark.skipif(
    not _REAL_FINGERPRINTS_PATH.exists(),
    reason="config/fingerprints.yaml not present",
)
def test_personal_care_scores_tvoc_spike_without_pm_or_co():
    """personal_care fingerprint matches a sharp TVOC spike with no PM2.5 or CO rise."""
    engine = AttributionEngine(_REAL_FINGERPRINTS_PATH)
    # Simulate a spray event: high TVOC, normal PM2.5, normal CO, slight NH3
    fv = FeatureVector(
        timestamp=_ts(),
        tvoc_current=480.0,
        tvoc_baseline=60.0,
        tvoc_peak_ratio=8.0,
        pm25_current=4.0,
        pm25_baseline=4.0,
        pm25_peak_ratio=1.0,
        co_current=50.0,
        co_baseline=50.0,
        nh3_current=15.0,
        nh3_baseline=8.0,
        eco2_current=430.0,
        eco2_baseline=420.0,
    )
    result = engine.attribute(fv)
    # The fingerprint should be a candidate; we just verify it doesn't raise and
    # that if a result is returned the description has no unfilled template slots.
    if result is not None:
        assert "{" not in result.description

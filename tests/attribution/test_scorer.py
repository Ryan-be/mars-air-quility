"""Tests for attribution sensor and temporal scorers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mlss_monitor.attribution.loader import Fingerprint
from mlss_monitor.attribution.scorer import sensor_score, temporal_score, combine
from mlss_monitor.feature_vector import FeatureVector


def _ts():
    return datetime.now(timezone.utc)


def _fv(**kwargs) -> FeatureVector:
    return FeatureVector(timestamp=_ts(), **kwargs)


def _fp(sensors=None, temporal=None, floor=0.5) -> Fingerprint:
    return Fingerprint(
        id="test",
        label="Test",
        description="",
        examples="",
        sensors=sensors or {},
        temporal=temporal or {},
        confidence_floor=floor,
        description_template="",
        action_template="",
    )


# ── sensor_score ──────────────────────────────────────────────────────────────

def test_sensor_score_high_matches_high_peak_ratio():
    """'high' state matches when tvoc_peak_ratio >= 2.0."""
    fp = _fp(sensors={"tvoc": "high"})
    fv = _fv(tvoc_current=400.0, tvoc_baseline=150.0, tvoc_peak_ratio=2.7)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_elevated_matches_peak_ratio_1_4():
    """'elevated' matches when tvoc_peak_ratio >= 1.4."""
    fp = _fp(sensors={"tvoc": "elevated"})
    fv = _fv(tvoc_current=220.0, tvoc_baseline=150.0, tvoc_peak_ratio=1.47)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_normal_matches_low_ratio():
    """'normal' matches when peak_ratio < 1.4."""
    fp = _fp(sensors={"pm25": "normal"})
    fv = _fv(pm25_current=5.0, pm25_baseline=5.0, pm25_peak_ratio=1.0)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_absent_matches_none_current():
    """'absent' matches when the current value is None."""
    fp = _fp(sensors={"co": "absent"})
    fv = _fv(co_current=None)
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_absent_skipped_when_baseline_is_none_but_current_is_not():
    """'absent' returns None (skip) when baseline is unknown but sensor is active.

    Regression: previously the engine fell through to `current < 5` when
    co_baseline was None, causing a deodorant spray (co_current=3 ppb from an
    uncalibrated sensor) to be treated as 'absent' and wrongly boost the
    cooking fingerprint's sensor score.
    """
    fp = _fp(sensors={"co": "absent"})
    # co_current is a real non-zero reading; baseline not yet established
    fv = _fv(co_current=3.5, co_baseline=None, co_peak_ratio=None)
    # sensor_score should SKIP this criterion (denominator = 0) → return 0.0
    # rather than counting it as a match and returning 1.0
    assert sensor_score(fp, fv) == pytest.approx(0.0)


def test_sensor_score_absent_uses_baseline_when_available():
    """'absent' correctly uses baseline ratio when baseline is known."""
    fp = _fp(sensors={"co": "absent"})
    # co_current clearly below baseline → absent
    fv = _fv(co_current=25.0, co_baseline=30.0, co_peak_ratio=0.833)
    assert sensor_score(fp, fv) == pytest.approx(1.0)
    # co_current above baseline → not absent
    fv2 = _fv(co_current=35.0, co_baseline=30.0, co_peak_ratio=1.167)
    assert sensor_score(fp, fv2) == pytest.approx(0.0)


def test_sensor_score_partial_match():
    """Score is fraction of matched fields over evaluated fields."""
    fp = _fp(sensors={"tvoc": "high", "pm25": "high"})
    # tvoc matches (peak_ratio 2.5), pm25 doesn't match (peak_ratio 1.1)
    fv = _fv(
        tvoc_current=400.0, tvoc_baseline=150.0, tvoc_peak_ratio=2.5,
        pm25_current=6.0, pm25_baseline=5.0, pm25_peak_ratio=1.1,
    )
    assert sensor_score(fp, fv) == pytest.approx(0.5)


def test_sensor_score_skips_none_fields():
    """None FeatureVector fields are skipped — denominator excludes them."""
    fp = _fp(sensors={"tvoc": "elevated", "co": "elevated"})
    # tvoc matches; co is None (skip, don't penalise)
    fv = _fv(tvoc_current=220.0, tvoc_baseline=150.0, tvoc_peak_ratio=1.5,
             co_current=None)
    # Only 1 field evaluated, 1 matched → 1.0
    assert sensor_score(fp, fv) == pytest.approx(1.0)


def test_sensor_score_empty_sensors_returns_zero():
    """No sensor specs → 0.0 score."""
    fp = _fp(sensors={})
    fv = _fv(tvoc_current=400.0)
    assert sensor_score(fp, fv) == pytest.approx(0.0)


# ── temporal_score ────────────────────────────────────────────────────────────

def test_temporal_score_nh3_follows_tvoc_matches():
    """nh3_follows_tvoc: true matches when nh3_lag_behind_tvoc_seconds is within limit."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=45.0)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_nh3_follows_tvoc_fails_excess_lag():
    """nh3_follows_tvoc: true fails when lag exceeds nh3_max_lag_seconds."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=180.0)
    assert temporal_score(fp, fv) == pytest.approx(0.0)


def test_temporal_score_pm25_correlated_matches():
    """pm25_correlated_with_tvoc: true matches when fv.pm25_correlated_with_tvoc is True."""
    fp = _fp(temporal={"pm25_correlated_with_tvoc": True})
    fv = _fv(pm25_correlated_with_tvoc=True)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_skips_none_fv_fields():
    """Temporal score skips criteria when relevant FeatureVector field is None."""
    fp = _fp(temporal={"nh3_follows_tvoc": True, "nh3_max_lag_seconds": 120})
    fv = _fv(nh3_lag_behind_tvoc_seconds=None)
    # Field is None → skip, denominator = 0 → return 0.0 (no data, not penalised)
    assert temporal_score(fp, fv) == pytest.approx(0.0)


def test_temporal_score_rise_rate_matches_when_tvoc_is_rising():
    """rise_rate criteria evaluate TVOC slope correctly."""
    fp = _fp(temporal={"rise_rate": "fast"})
    fv = _fv(tvoc_slope_5m=10.0)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_sustain_max_minutes_matches_short_lived_event():
    """sustain_max_minutes criteria evaluate TVOC elevated duration."""
    fp = _fp(temporal={"sustain_max_minutes": 15})
    fv = _fv(tvoc_elevated_minutes=10.0)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_decay_rate_matches_when_tvoc_falling():
    """decay_rate criteria evaluate TVOC decay correctly."""
    fp = _fp(temporal={"decay_rate": "fast"})
    fv = _fv(tvoc_decay_rate=-5.0)
    assert temporal_score(fp, fv) == pytest.approx(1.0)


def test_temporal_score_empty_temporal_returns_zero():
    """No temporal criteria → 0.0."""
    fp = _fp(temporal={})
    fv = _fv()
    assert temporal_score(fp, fv) == pytest.approx(0.0)


# ── combine ───────────────────────────────────────────────────────────────────

def test_combine_weights_correctly():
    """combine() returns sensor×0.6 + temporal×0.4."""
    result = combine(sensor=1.0, temporal=1.0)
    assert result == pytest.approx(1.0)

    result = combine(sensor=1.0, temporal=0.0)
    assert result == pytest.approx(0.6)

    result = combine(sensor=0.0, temporal=1.0)
    assert result == pytest.approx(0.4)

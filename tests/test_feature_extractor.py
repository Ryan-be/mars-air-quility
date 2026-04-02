import pytest
from datetime import datetime, timezone
from mlss_monitor.feature_vector import FeatureVector


def test_feature_vector_all_none():
    fv = FeatureVector(timestamp=datetime.now(timezone.utc))
    assert fv.tvoc_current is None
    assert fv.tvoc_baseline is None
    assert fv.tvoc_slope_1m is None
    assert fv.tvoc_slope_5m is None
    assert fv.tvoc_slope_30m is None
    assert fv.tvoc_elevated_minutes is None
    assert fv.tvoc_peak_ratio is None
    assert fv.tvoc_is_declining is None
    assert fv.tvoc_decay_rate is None
    assert fv.tvoc_pulse_detected is None
    assert fv.nh3_lag_behind_tvoc_seconds is None
    assert fv.pm25_correlated_with_tvoc is None
    assert fv.co_correlated_with_tvoc is None
    assert fv.vpd_kpa is None


def test_feature_vector_with_values():
    fv = FeatureVector(
        timestamp=datetime.now(timezone.utc),
        tvoc_current=450.0,
        tvoc_baseline=200.0,
        tvoc_slope_1m=5.2,
        tvoc_is_declining=False,
        vpd_kpa=0.8,
    )
    assert fv.tvoc_current == 450.0
    assert fv.tvoc_baseline == 200.0
    assert fv.tvoc_slope_1m == 5.2
    assert fv.tvoc_is_declining is False
    assert fv.vpd_kpa == 0.8
    assert fv.eco2_current is None

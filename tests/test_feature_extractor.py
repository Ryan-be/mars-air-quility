import pytest
from datetime import datetime, timezone, timedelta
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.feature_extractor import (
    _slope, _elevated_minutes, _pulse_detected, _current, _peak_ratio,
)


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


def _make_tvoc_readings(values: list[float], seconds_between: int = 1) -> list[NormalisedReading]:
    """Build synthetic NormalisedReadings with tvoc_ppb set, oldest first."""
    now = datetime.now(timezone.utc)
    total = len(values)
    return [
        NormalisedReading(
            timestamp=now - timedelta(seconds=(total - 1 - i) * seconds_between),
            source="test",
            tvoc_ppb=v,
        )
        for i, v in enumerate(values)
    ]


# ── _current ─────────────────────────────────────────────────────────────────

def test_current_returns_latest_non_none():
    readings = _make_tvoc_readings([100.0, 150.0, 200.0])
    assert _current(readings, "tvoc_ppb") == 200.0


def test_current_skips_trailing_none():
    now = datetime.now(timezone.utc)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=2), source="t", tvoc_ppb=150.0),
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t", tvoc_ppb=None),
        NormalisedReading(timestamp=now, source="t", tvoc_ppb=None),
    ]
    assert _current(readings, "tvoc_ppb") == 150.0


def test_current_returns_none_when_all_none():
    now = datetime.now(timezone.utc)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t"),
        NormalisedReading(timestamp=now, source="t"),
    ]
    assert _current(readings, "tvoc_ppb") is None


# ── _slope ───────────────────────────────────────────────────────────────────

def test_slope_rising():
    # 60 readings, 1 ppb/sec rise → ~60 ppb/min
    readings = _make_tvoc_readings([float(i) for i in range(60)])
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s - 60.0) < 2.0


def test_slope_flat():
    readings = _make_tvoc_readings([100.0] * 60)
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s) < 0.1


def test_slope_returns_none_too_few_points():
    readings = _make_tvoc_readings([100.0])
    assert _slope(readings, "tvoc_ppb", window_seconds=60) is None


def test_slope_only_uses_window():
    # first 60 readings rise, last 60 flat — 1m slope should be ~0
    rising = [float(i) for i in range(60)]
    flat = [60.0] * 60
    readings = _make_tvoc_readings(rising + flat)
    s = _slope(readings, "tvoc_ppb", window_seconds=60)
    assert s is not None
    assert abs(s) < 2.0


# ── _elevated_minutes ─────────────────────────────────────────────────────────

def test_elevated_minutes_all_above():
    readings = _make_tvoc_readings([200.0] * 120)
    assert _elevated_minutes(readings, "tvoc_ppb", baseline=100.0) == pytest.approx(2.0, abs=0.1)


def test_elevated_minutes_breaks_on_dip():
    readings = _make_tvoc_readings([200.0] * 60 + [50.0] + [200.0] * 30)
    result = _elevated_minutes(readings, "tvoc_ppb", baseline=100.0)
    assert result == pytest.approx(30 / 60, abs=0.1)


def test_elevated_minutes_zero_when_all_below():
    readings = _make_tvoc_readings([50.0] * 60)
    assert _elevated_minutes(readings, "tvoc_ppb", baseline=100.0) == 0.0


# ── _pulse_detected ───────────────────────────────────────────────────────────

def test_pulse_detected_true():
    readings = _make_tvoc_readings([100.0] * 20 + [300.0] + [110.0] * 10)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is True


def test_pulse_detected_false_no_spike():
    readings = _make_tvoc_readings([100.0] * 30)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is False


def test_pulse_detected_false_still_at_peak():
    readings = _make_tvoc_readings([100.0] * 20 + [300.0] * 10)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=100.0) is False


def test_pulse_detected_none_when_no_baseline():
    readings = _make_tvoc_readings([100.0] * 30)
    assert _pulse_detected(readings, "tvoc_ppb", baseline=None) is None


# ── _peak_ratio ───────────────────────────────────────────────────────────────

def test_peak_ratio_calculation():
    assert _peak_ratio(300.0, 100.0) == pytest.approx(3.0)


def test_peak_ratio_none_when_baseline_zero():
    assert _peak_ratio(300.0, 0.0) is None


def test_peak_ratio_none_when_either_none():
    assert _peak_ratio(None, 100.0) is None
    assert _peak_ratio(300.0, None) is None


# ── End-to-end FeatureExtractor tests ────────────────────────────────────────

from mlss_monitor.feature_extractor import FeatureExtractor


def test_extract_per_sensor_tvoc_rising():
    readings = _make_tvoc_readings([float(i * 2) for i in range(60)])  # 0 → 118 ppb
    baselines = {"tvoc_ppb": 50.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.tvoc_current == pytest.approx(118.0)
    assert fv.tvoc_baseline == 50.0
    assert fv.tvoc_slope_1m is not None and fv.tvoc_slope_1m > 0
    assert fv.tvoc_is_declining is False
    assert fv.tvoc_decay_rate is None


def test_extract_per_sensor_declining_tvoc():
    readings = _make_tvoc_readings([float(200 - i) for i in range(60)])  # 200 → 141 ppb
    baselines = {"tvoc_ppb": 100.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.tvoc_is_declining is True
    assert fv.tvoc_decay_rate is not None and fv.tvoc_decay_rate < 0


def test_extract_all_none_when_no_readings():
    fv = FeatureExtractor().extract([], {})
    assert fv.tvoc_current is None
    assert fv.eco2_current is None
    assert fv.temperature_current is None


def test_extract_no_baseline_gives_none_for_ratio():
    readings = _make_tvoc_readings([200.0] * 30)
    fv = FeatureExtractor().extract(readings, {})
    assert fv.tvoc_baseline is None
    assert fv.tvoc_peak_ratio is None
    assert fv.tvoc_elevated_minutes is None

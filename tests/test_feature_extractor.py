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


# ── Task 3: Cross-sensor and derived features ────────────────────────────────

def _make_readings_with_fields(
    field_values: dict[str, list[float | None]],
    seconds_between: int = 1,
) -> list[NormalisedReading]:
    """Build synthetic readings with multiple fields set.

    field_values: {field_name: [v0, v1, ..., vN]} — all lists must be same length.
    """
    now = datetime.now(timezone.utc)
    keys = list(field_values.keys())
    n = len(field_values[keys[0]])
    readings = []
    for i in range(n):
        ts = now - timedelta(seconds=(n - 1 - i) * seconds_between)
        kwargs = {k: field_values[k][i] for k in keys}
        readings.append(NormalisedReading(timestamp=ts, source="test", **kwargs))
    return readings


# ── NH3 lag ──────────────────────────────────────────────────────────────────

def test_nh3_lag_detected():
    """NH3 peaks 30 seconds after TVOC peak → lag = 30.0."""
    tvoc_vals = [100.0] * 50 + [500.0] + [100.0] * 69  # peak at index 50
    nh3_vals  = [10.0]  * 80 + [80.0]  + [10.0]  * 39  # peak at index 80

    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is not None
    assert 25.0 <= fv.nh3_lag_behind_tvoc_seconds <= 35.0


def test_nh3_lag_none_when_nh3_before_tvoc():
    """NH3 peaked before TVOC → no lag (None)."""
    tvoc_vals = [100.0] * 80 + [500.0] + [100.0] * 39  # peak at index 80
    nh3_vals  = [10.0]  * 50 + [80.0]  + [10.0]  * 69  # peak at index 50
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is None


def test_nh3_lag_none_when_lag_too_large():
    """NH3 peaked 150 seconds after TVOC → beyond 120s limit → None."""
    tvoc_vals = [500.0] + [100.0] * 170  # peak at index 0
    nh3_vals  = [10.0]  * 150 + [80.0] + [10.0] * 20  # peak at index 150
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is None


# ── PM2.5 correlation ────────────────────────────────────────────────────────

def test_pm25_correlated_with_tvoc_true():
    """Both TVOC and PM2.5 rising → correlated = True."""
    n = 300  # 5 minutes
    tvoc_vals = [float(100 + i) for i in range(n)]
    pm25_vals = [float(10 + i * 0.1) for i in range(n)]
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "pm25_ug_m3": pm25_vals})
    baselines = {"tvoc_ppb": 100.0, "pm25_ug_m3": 10.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.pm25_correlated_with_tvoc is True


def test_pm25_correlated_with_tvoc_false():
    """TVOC rising, PM2.5 flat → not correlated."""
    n = 300
    tvoc_vals = [float(100 + i) for i in range(n)]
    pm25_vals = [10.0] * n
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "pm25_ug_m3": pm25_vals})
    baselines = {"tvoc_ppb": 100.0, "pm25_ug_m3": 10.0}
    fv = FeatureExtractor().extract(readings, baselines)
    assert fv.pm25_correlated_with_tvoc is False


def test_pm25_correlated_none_when_no_data():
    """No PM2.5 readings → None."""
    readings = _make_tvoc_readings([float(i) for i in range(60)])
    fv = FeatureExtractor().extract(readings, {})
    assert fv.pm25_correlated_with_tvoc is None


# ── VPD ──────────────────────────────────────────────────────────────────────

def test_vpd_computed_from_temp_and_humidity():
    now = datetime.now(timezone.utc)
    readings = [NormalisedReading(
        timestamp=now, source="test", temperature_c=21.0, humidity_pct=60.0
    )]
    fv = FeatureExtractor().extract(readings, {})
    # SVP at 21°C ≈ 2.487 kPa; VPD = 2.487 × 0.40 ≈ 0.995 kPa
    assert fv.vpd_kpa is not None
    assert 0.9 < fv.vpd_kpa < 1.1


def test_vpd_none_when_no_temperature():
    now = datetime.now(timezone.utc)
    readings = [NormalisedReading(timestamp=now, source="test", humidity_pct=60.0)]
    fv = FeatureExtractor().extract(readings, {})
    assert fv.vpd_kpa is None

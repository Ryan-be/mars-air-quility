import pytest
from datetime import datetime, timezone, timedelta
from mlss_monitor.feature_vector import FeatureVector
from mlss_monitor.data_sources.base import NormalisedReading
from mlss_monitor.feature_extractor import (
    _slope, _elevated_minutes, _pulse_detected, _current, _peak_ratio,
    _acceleration, _peak_time_offset_s, _rise_time_s, _slope_variance,
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

from mlss_monitor.feature_extractor import FeatureExtractor  # noqa: E402


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
    """NH3 concentration peaks 30 seconds after TVOC peak → lag = 30.0.

    MICS6814 reports NH3 as resistance (kΩ): lower resistance = higher
    concentration.  The NH3 concentration peak is therefore the *minimum*
    nh3_ppb value in the window.
    """
    tvoc_vals = [100.0] * 50 + [500.0] + [100.0] * 69  # TVOC peak at index 50
    # NH3 resistance drops to minimum at index 80 (30 s after TVOC peak)
    nh3_vals  = [30.0]  * 80 + [10.0]  + [30.0]  * 39  # resistance dip at index 80

    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is not None
    assert 25.0 <= fv.nh3_lag_behind_tvoc_seconds <= 35.0


def test_nh3_lag_none_when_nh3_before_tvoc():
    """NH3 concentration peaked before TVOC → no lag (None).

    NH3 resistance minimum (= concentration peak) occurs before the TVOC peak.
    """
    tvoc_vals = [100.0] * 80 + [500.0] + [100.0] * 39  # TVOC peak at index 80
    # NH3 resistance dip at index 50 — before TVOC peak
    nh3_vals  = [30.0]  * 50 + [10.0]  + [30.0]  * 69
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.nh3_lag_behind_tvoc_seconds is None


def test_nh3_lag_none_when_lag_too_large():
    """NH3 concentration peaked 150 seconds after TVOC → beyond 120s limit → None."""
    tvoc_vals = [500.0] + [100.0] * 170  # TVOC peak at index 0
    # NH3 resistance dip at index 150 — 150 s after TVOC peak
    nh3_vals  = [30.0]  * 150 + [10.0] + [30.0] * 20
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


# ── CO correlation (inverted channel) ────────────────────────────────────────

def test_co_correlated_with_tvoc_true_when_co_resistance_falls():
    """TVOC rising and CO *resistance* falling → co_correlated_with_tvoc = True.

    The MICS6814 CO channel is a reducing-gas resistor: higher CO concentration
    causes lower resistance.  So when a deodorant spray raises both TVOC and CO
    concentration, tvoc_ppb rises while co_ppb (raw resistance) falls.
    The extractor must treat a falling CO resistance as correlated with a rising
    TVOC, not anti-correlated.
    """
    n = 300  # 5 minutes of 1-second readings
    tvoc_vals = [float(100 + i) for i in range(n)]       # rising TVOC
    co_vals   = [float(30_000 - i * 50) for i in range(n)]  # falling CO resistance
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "co_ppb": co_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.co_correlated_with_tvoc is True


def test_co_correlated_with_tvoc_false_when_both_rise_in_resistance():
    """TVOC rising, CO resistance also rising (concentration falling) → False.

    If CO resistance rises while TVOC goes up, CO concentration is actually
    decreasing — that is *not* correlated with the TVOC spike.
    """
    n = 300
    tvoc_vals = [float(100 + i) for i in range(n)]        # rising TVOC
    co_vals   = [float(20_000 + i * 50) for i in range(n)]  # rising CO resistance = falling conc
    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "co_ppb": co_vals})
    fv = FeatureExtractor().extract(readings, {})
    assert fv.co_correlated_with_tvoc is False


# ── personal_care regression ──────────────────────────────────────────────────

def test_personal_care_deodorant_nh3_lag_detected():
    """Regression: deodorant spray scenario — NH3 resistance drops AFTER TVOC peak.

    Before the fix, _nh3_lag_behind_tvoc() searched for the maximum nh3_ppb
    value (highest resistance = ambient baseline), so it never found a lag and
    returned None, causing temporal_score to return 0.0 for personal_care.

    After the fix it searches for the minimum nh3_ppb (lowest resistance =
    highest NH3 concentration), and correctly detects a lag of ~60 s.
    """
    # Simulate: TVOC spikes at second 30 (index 30 in 1-s readings)
    # NH3 resistance drops to minimum at second 90 (60 s lag)
    n = 200
    tvoc_vals = [150.0] * 30 + [4500.0] + [150.0] * (n - 31)   # 20-30x spike
    # NH3 baseline resistance ~30 kΩ; drops to ~10 kΩ at index 90
    nh3_vals  = [30.0] * 90 + [10.0] + [30.0] * (n - 91)

    readings = _make_readings_with_fields({"tvoc_ppb": tvoc_vals, "nh3_ppb": nh3_vals})
    fv = FeatureExtractor().extract(readings, {})

    assert fv.nh3_lag_behind_tvoc_seconds is not None, (
        "nh3_lag_behind_tvoc_seconds must not be None during a deodorant spray "
        "(NH3 resistance dip after TVOC spike). Likely the function is still "
        "searching for a NH3 *maximum* instead of minimum."
    )
    assert 55.0 <= fv.nh3_lag_behind_tvoc_seconds <= 65.0


# ── New temporal feature helpers ─────────────────────────────────────────────

def test_acceleration_returns_none_when_slopes_none():
    assert _acceleration(None, 5.0) is None
    assert _acceleration(5.0, None) is None
    assert _acceleration(None, None) is None


def test_acceleration_positive_for_rising():
    # slope_1m > slope_5m → acceleration is positive (rate is speeding up)
    result = _acceleration(10.0, 4.0)
    assert result == pytest.approx(6.0)
    # slope_1m < slope_5m → negative acceleration
    assert _acceleration(2.0, 8.0) == pytest.approx(-6.0)


def test_peak_time_offset_s():
    now = datetime.now(timezone.utc)
    # Peak at index 2 (2 seconds after first reading)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=4), source="t", tvoc_ppb=100.0),
        NormalisedReading(timestamp=now - timedelta(seconds=3), source="t", tvoc_ppb=150.0),
        NormalisedReading(timestamp=now - timedelta(seconds=2), source="t", tvoc_ppb=300.0),
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t", tvoc_ppb=200.0),
        NormalisedReading(timestamp=now,                        source="t", tvoc_ppb=120.0),
    ]
    result = _peak_time_offset_s(readings, "tvoc_ppb")
    # Peak is 2 seconds after the first reading (first at now-4s, peak at now-2s)
    assert result == pytest.approx(2.0)


def test_peak_time_offset_s_none_when_single_reading():
    now = datetime.now(timezone.utc)
    readings = [NormalisedReading(timestamp=now, source="t", tvoc_ppb=100.0)]
    assert _peak_time_offset_s(readings, "tvoc_ppb") is None


def test_rise_time_s():
    now = datetime.now(timezone.utc)
    baseline = 100.0
    # First above baseline at now-4s (value 110), peak at now-2s (value 300)
    readings = [
        NormalisedReading(timestamp=now - timedelta(seconds=5), source="t", tvoc_ppb=90.0),   # below
        NormalisedReading(timestamp=now - timedelta(seconds=4), source="t", tvoc_ppb=110.0),  # first above
        NormalisedReading(timestamp=now - timedelta(seconds=3), source="t", tvoc_ppb=200.0),
        NormalisedReading(timestamp=now - timedelta(seconds=2), source="t", tvoc_ppb=300.0),  # peak
        NormalisedReading(timestamp=now - timedelta(seconds=1), source="t", tvoc_ppb=250.0),
    ]
    result = _rise_time_s(readings, "tvoc_ppb", baseline)
    # From now-4s to now-2s = 2 seconds
    assert result == pytest.approx(2.0)


def test_rise_time_s_none_when_no_baseline():
    readings = _make_tvoc_readings([100.0, 200.0, 300.0])
    assert _rise_time_s(readings, "tvoc_ppb", None) is None


def test_rise_time_s_none_when_never_above_baseline():
    readings = _make_tvoc_readings([50.0, 60.0, 70.0])
    assert _rise_time_s(readings, "tvoc_ppb", baseline=200.0) is None


def test_slope_variance_needs_three_windows():
    # Only 2 complete 60s windows → should return None
    readings = _make_tvoc_readings([float(i) for i in range(120)], seconds_between=1)
    # 120 readings at 1s each = 119s total → only 1 complete 60s window → None
    assert _slope_variance(readings, "tvoc_ppb", window_seconds=60) is None


def test_slope_variance_returns_value_with_enough_windows():
    # 4 complete 60s windows = 240 readings at 1s intervals (239s total)
    readings = _make_tvoc_readings([float(i) for i in range(241)], seconds_between=1)
    result = _slope_variance(readings, "tvoc_ppb", window_seconds=60)
    # All windows have the same slope → variance should be 0 (or very close)
    assert result is not None
    assert result >= 0.0


def test_slope_variance_none_when_empty():
    assert _slope_variance([], "tvoc_ppb") is None

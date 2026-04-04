from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import StatisticsError, linear_regression

from mlss_monitor.data_sources.base import SENSOR_FIELDS, NormalisedReading
from mlss_monitor.feature_vector import FeatureVector

# Maps NormalisedReading field name → FeatureVector field prefix
_SENSOR_MAP: tuple[tuple[str, str], ...] = (
    ("tvoc_ppb",      "tvoc"),
    ("eco2_ppm",      "eco2"),
    ("temperature_c", "temperature"),
    ("humidity_pct",  "humidity"),
    ("pm1_ug_m3",     "pm1"),
    ("pm25_ug_m3",    "pm25"),
    ("pm10_ug_m3",    "pm10"),
    ("co_ppb",        "co"),
    ("no2_ppb",       "no2"),
    ("nh3_ppb",       "nh3"),
)


# ── Private helpers (pure functions) ─────────────────────────────────────────

def _current(readings: list[NormalisedReading], field: str) -> float | None:
    """Return the most recent non-None value for field."""
    for r in reversed(readings):
        v = getattr(r, field)
        if v is not None:
            return float(v)
    return None


def _slope(
    readings: list[NormalisedReading], field: str, window_seconds: int
) -> float | None:
    """Linear slope in units/minute over the last window_seconds of data.

    Uses statistics.linear_regression (Python 3.10+).
    Returns None if fewer than 2 distinct-timestamp data points in the window.
    """
    if not readings:
        return None
    now_ts = readings[-1].timestamp
    cutoff = now_ts - timedelta(seconds=window_seconds)
    pairs = [
        ((r.timestamp - cutoff).total_seconds(), getattr(r, field))
        for r in readings
        if r.timestamp >= cutoff and getattr(r, field) is not None
    ]
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2:
        return None
    try:
        result = linear_regression(xs, ys)
    except StatisticsError:
        return None
    return round(result.slope * 60, 4)  # per second → per minute


def _elevated_minutes(
    readings: list[NormalisedReading], field: str, baseline: float
) -> float:
    """Count consecutive seconds (newest to oldest) where field > baseline.

    Stops at the first reading where value is None or <= baseline.
    Returns minutes (count / 60).
    """
    count = 0
    for r in reversed(readings):
        v = getattr(r, field)
        if v is None or v <= baseline:
            break
        count += 1
    return count / 60.0


def _pulse_detected(
    readings: list[NormalisedReading], field: str, baseline: float | None
) -> bool | None:
    """True if a spike-and-decay pattern is visible in readings.

    Pattern: max value > 1.5 × baseline AND current value < 0.8 × max.
    Returns None if baseline is None or no non-None values exist.
    """
    if baseline is None:
        return None
    values = [getattr(r, field) for r in readings if getattr(r, field) is not None]
    if len(values) < 2:
        return None
    peak = max(values)
    current = values[-1]
    return peak > 1.5 * baseline and current < 0.8 * peak


def _peak_ratio(current: float | None, baseline: float | None) -> float | None:
    """current / baseline. None if either is None or baseline is zero."""
    if current is None or baseline is None or baseline == 0:
        return None
    return round(current / baseline, 4)


def _vpd_kpa(temp_c: float | None, humidity_pct: float | None) -> float | None:
    """Vapour pressure deficit in kPa."""
    if temp_c is None or humidity_pct is None or humidity_pct <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return round(svp * (1 - humidity_pct / 100), 4)


def _sensor_features(
    readings: list[NormalisedReading],
    field: str,
    prefix: str,
    baseline: float | None,
) -> dict:
    """Compute all 10 per-sensor features for one sensor channel.

    Returns a dict keyed by FeatureVector field names.
    """
    current = _current(readings, field)
    slope_1m = _slope(readings, field, window_seconds=60)
    slope_5m = _slope(readings, field, window_seconds=300)
    slope_30m = _slope(readings, field, window_seconds=1800)
    elev_min = _elevated_minutes(readings, field, baseline) if baseline is not None else None
    peak_ratio = _peak_ratio(current, baseline)
    is_declining = (slope_1m < 0) if slope_1m is not None else None
    decay_rate = slope_1m if (slope_1m is not None and slope_1m < 0) else None
    pulse = _pulse_detected(readings, field, baseline)

    return {
        f"{prefix}_current":          current,
        f"{prefix}_baseline":         baseline,
        f"{prefix}_slope_1m":         slope_1m,
        f"{prefix}_slope_5m":         slope_5m,
        f"{prefix}_slope_30m":        slope_30m,
        f"{prefix}_elevated_minutes": elev_min,
        f"{prefix}_peak_ratio":       peak_ratio,
        f"{prefix}_is_declining":     is_declining,
        f"{prefix}_decay_rate":       decay_rate,
        f"{prefix}_pulse_detected":   pulse,
    }


def _nh3_lag_behind_tvoc(
    readings: list[NormalisedReading], max_lag_seconds: float = 120.0
) -> float | None:
    """Return NH3 lag behind TVOC peak in seconds, or None.

    Looks for the peak of each sensor in the readings window.
    Returns the lag only if TVOC peaked before NH3 and lag <= max_lag_seconds.
    """
    tvoc_peak_ts: datetime | None = None
    tvoc_peak_val: float = 0.0
    nh3_peak_ts: datetime | None = None
    nh3_peak_val: float = 0.0

    for r in readings:
        if r.tvoc_ppb is not None and r.tvoc_ppb > tvoc_peak_val:
            tvoc_peak_val = r.tvoc_ppb
            tvoc_peak_ts = r.timestamp
        if r.nh3_ppb is not None and r.nh3_ppb > nh3_peak_val:
            nh3_peak_val = r.nh3_ppb
            nh3_peak_ts = r.timestamp

    if tvoc_peak_ts is None or nh3_peak_ts is None:
        return None

    lag = (nh3_peak_ts - tvoc_peak_ts).total_seconds()
    if 0 <= lag <= max_lag_seconds:
        return lag
    return None


def _sensors_correlated(
    readings: list[NormalisedReading],
    field_a: str,
    field_b: str,
    window_seconds: int = 300,
) -> bool | None:
    """True if both sensors have positive slope over window_seconds.

    Returns None if either sensor has no data in the window.
    """
    slope_a = _slope(readings, field_a, window_seconds)
    slope_b = _slope(readings, field_b, window_seconds)
    if slope_a is None or slope_b is None:
        return None
    return slope_a > 0 and slope_b > 0


# ── FeatureExtractor ─────────────────────────────────────────────────────────

class FeatureExtractor:
    """Converts a hot-tier snapshot + cold-tier baselines into a FeatureVector."""

    def extract(
        self,
        hot_readings: list[NormalisedReading],
        baselines: dict[str, float | None],
    ) -> FeatureVector:
        """
        Args:
            hot_readings: NormalisedReading list from hot tier, oldest first.
            baselines: dict keyed by NormalisedReading field names, e.g.
                       {"tvoc_ppb": 180.0, "eco2_ppm": 600.0, ...}
                       Values may be None (no baseline available yet).
        Returns:
            FeatureVector with all computable features populated; rest None.
        """
        fields: dict = {}

        # Per-sensor features
        for nr_field, fv_prefix in _SENSOR_MAP:
            baseline = baselines.get(nr_field)
            fields.update(_sensor_features(hot_readings, nr_field, fv_prefix, baseline))

        # Cross-sensor and derived
        fields["nh3_lag_behind_tvoc_seconds"] = _nh3_lag_behind_tvoc(hot_readings)
        fields["pm25_correlated_with_tvoc"] = _sensors_correlated(
            hot_readings, "tvoc_ppb", "pm25_ug_m3"
        )
        fields["co_correlated_with_tvoc"] = _sensors_correlated(
            hot_readings, "tvoc_ppb", "co_ppb"
        )
        fields["vpd_kpa"] = _vpd_kpa(
            _current(hot_readings, "temperature_c"),
            _current(hot_readings, "humidity_pct"),
        )

        return FeatureVector(timestamp=datetime.now(timezone.utc), **fields)

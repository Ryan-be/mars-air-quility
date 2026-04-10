"""Sensor and temporal scoring functions for the attribution layer.

All functions are pure — no IO, no side effects.
"""
from __future__ import annotations

from mlss_monitor.attribution.loader import Fingerprint
from mlss_monitor.feature_vector import FeatureVector

# Maps sensor name (as used in fingerprints.yaml) to FeatureVector field prefix.
_SENSOR_PREFIX: dict[str, str] = {
    "tvoc":        "tvoc",
    "eco2":        "eco2",
    "temperature": "temperature",
    "humidity":    "humidity",
    "pm1":         "pm1",
    "pm25":        "pm25",
    "pm10":        "pm10",
    "co":          "co",
    "no2":         "no2",
    "nh3":         "nh3",
}


def _peak_ratio(fv: FeatureVector, prefix: str):
    """Return the peak_ratio field for `prefix`, or None if unavailable."""
    return getattr(fv, f"{prefix}_peak_ratio", None)


def _current(fv: FeatureVector, prefix: str):
    return getattr(fv, f"{prefix}_current", None)


def _slope_5m(fv: FeatureVector, prefix: str):
    return getattr(fv, f"{prefix}_slope_5m", None)


def _state_matches(state: str, fv: FeatureVector, prefix: str):
    """Return True/False/None.

    None means the relevant FeatureVector field is None — caller should skip
    this criterion rather than penalise it.
    """
    ratio = _peak_ratio(fv, prefix)
    current = _current(fv, prefix)

    if state == "high":
        if ratio is None:
            return None
        return ratio >= 2.0

    if state == "elevated":
        if ratio is None:
            return None
        return ratio >= 1.4

    if state == "slight_rise":
        slope = _slope_5m(fv, prefix)
        if ratio is None or slope is None:
            return None
        return slope > 0 and ratio >= 1.1

    if state == "normal":
        if ratio is None:
            return None
        return ratio < 1.4

    if state == "absent":
        # None current counts as absent
        if current is None:
            return True
        baseline = getattr(fv, f"{prefix}_baseline", None)
        if baseline is None:
            return None
        if baseline == 0:
            return current < 5
        return current < baseline * 0.9

    if state == "rising":
        slope = _slope_5m(fv, prefix)
        if slope is None:
            return None
        return slope > 0

    return None


def sensor_score(fp: Fingerprint, fv: FeatureVector) -> float:
    """Fraction of non-None sensor fields that match the fingerprint spec.

    Returns 0.0 when there are no sensor criteria (empty sensors dict).
    """
    if not fp.sensors:
        return 0.0

    matched = 0
    evaluated = 0
    for sensor_name, expected_state in fp.sensors.items():
        prefix = _SENSOR_PREFIX.get(sensor_name)
        if prefix is None:
            continue  # unknown sensor, skip
        result = _state_matches(expected_state, fv, prefix)
        if result is None:
            continue  # no data — skip
        evaluated += 1
        if result:
            matched += 1

    if evaluated == 0:
        return 0.0
    return matched / evaluated


def _rise_rate_matches(rate: str, fv: FeatureVector) -> bool | None:
    slope = fv.tvoc_slope_5m
    if slope is None:
        return None
    if rate in {"fast", "moderate", "slow"}:
        return slope > 0
    return None


def _decay_rate_matches(rate: str, fv: FeatureVector) -> bool | None:
    decay = fv.tvoc_decay_rate
    if decay is None:
        return None
    if rate in {"fast", "moderate", "slow"}:
        return decay < 0
    return None


def _sustain_minutes_matches(key: str, threshold: float, fv: FeatureVector) -> bool | None:
    elapsed = fv.tvoc_elevated_minutes
    if elapsed is None:
        return None
    if key == "sustain_min_minutes":
        return elapsed >= threshold
    if key == "sustain_max_minutes":
        return elapsed <= threshold
    return None


def temporal_score(fp: Fingerprint, fv: FeatureVector) -> float:
    """Fraction of evaluable temporal criteria that match the FeatureVector.

    Currently evaluates:
      - rise_rate / decay_rate via TVOC slope and decay fields
      - sustain_min_minutes / sustain_max_minutes via TVOC elevated duration
      - nh3_follows_tvoc + nh3_max_lag_seconds
      - pm25_correlated_with_tvoc
      - co_correlated_with_tvoc

    Returns 0.0 when no criteria can be evaluated.
    """
    t = fp.temporal
    if not t:
        return 0.0

    matched = 0
    evaluated = 0

    if "rise_rate" in t:
        result = _rise_rate_matches(t["rise_rate"], fv)
        evaluated += 1
        if result is not None:
            matched += 1 if result else 0

    if "decay_rate" in t:
        result = _decay_rate_matches(t["decay_rate"], fv)
        evaluated += 1
        if result is not None:
            matched += 1 if result else 0

    if "sustain_min_minutes" in t:
        result = _sustain_minutes_matches("sustain_min_minutes", t["sustain_min_minutes"], fv)
        evaluated += 1
        if result is not None:
            matched += 1 if result else 0

    if "sustain_max_minutes" in t:
        result = _sustain_minutes_matches("sustain_max_minutes", t["sustain_max_minutes"], fv)
        evaluated += 1
        if result is not None:
            matched += 1 if result else 0

    # nh3_follows_tvoc
    if "nh3_follows_tvoc" in t:
        lag = fv.nh3_lag_behind_tvoc_seconds
        evaluated += 1
        if lag is not None:
            max_lag = t.get("nh3_max_lag_seconds", 120)
            if t["nh3_follows_tvoc"] is True:
                matched += 1 if lag <= max_lag else 0
            else:
                matched += 1 if lag > max_lag else 0

    # pm25_correlated_with_tvoc
    if "pm25_correlated_with_tvoc" in t:
        corr = fv.pm25_correlated_with_tvoc
        evaluated += 1
        if corr is not None:
            matched += 1 if (corr == t["pm25_correlated_with_tvoc"]) else 0

    # co_correlated_with_tvoc
    if "co_correlated_with_tvoc" in t:
        corr = fv.co_correlated_with_tvoc
        evaluated += 1
        if corr is not None:
            matched += 1 if (corr == t["co_correlated_with_tvoc"]) else 0

    if evaluated == 0:
        return 0.0
    return matched / evaluated


def combine(sensor: float, temporal: float) -> float:
    """Combine sensor and temporal scores: sensor×0.6 + temporal×0.4."""
    return sensor * 0.6 + temporal * 0.4

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FeatureVector:
    """Pre-computed temporal features for the detection and attribution layers.

    All fields default to None. None means insufficient data — detection rules
    and attribution scoring skip None fields gracefully.
    """
    timestamp: datetime

    # ── TVOC (ppb) ────────────────────────────────────────────────────────────
    tvoc_current:           float | None = None
    tvoc_baseline:          float | None = None
    tvoc_slope_1m:          float | None = None  # ppb/min
    tvoc_slope_5m:          float | None = None
    tvoc_slope_30m:         float | None = None
    tvoc_elevated_minutes:  float | None = None
    tvoc_peak_ratio:        float | None = None  # current / baseline
    tvoc_is_declining:      bool  | None = None
    tvoc_decay_rate:        float | None = None  # ppb/min, negative when declining
    tvoc_pulse_detected:    bool  | None = None

    # ── eCO2 (ppm) ───────────────────────────────────────────────────────────
    eco2_current:           float | None = None
    eco2_baseline:          float | None = None
    eco2_slope_1m:          float | None = None
    eco2_slope_5m:          float | None = None
    eco2_slope_30m:         float | None = None
    eco2_elevated_minutes:  float | None = None
    eco2_peak_ratio:        float | None = None
    eco2_is_declining:      bool  | None = None
    eco2_decay_rate:        float | None = None
    eco2_pulse_detected:    bool  | None = None

    # ── Temperature (°C) ─────────────────────────────────────────────────────
    temperature_current:          float | None = None
    temperature_baseline:         float | None = None
    temperature_slope_1m:         float | None = None
    temperature_slope_5m:         float | None = None
    temperature_slope_30m:        float | None = None
    temperature_elevated_minutes: float | None = None
    temperature_peak_ratio:       float | None = None
    temperature_is_declining:     bool  | None = None
    temperature_decay_rate:       float | None = None
    temperature_pulse_detected:   bool  | None = None

    # ── Humidity (%) ─────────────────────────────────────────────────────────
    humidity_current:           float | None = None
    humidity_baseline:          float | None = None
    humidity_slope_1m:          float | None = None
    humidity_slope_5m:          float | None = None
    humidity_slope_30m:         float | None = None
    humidity_elevated_minutes:  float | None = None
    humidity_peak_ratio:        float | None = None
    humidity_is_declining:      bool  | None = None
    humidity_decay_rate:        float | None = None
    humidity_pulse_detected:    bool  | None = None

    # ── PM1 (µg/m³) ──────────────────────────────────────────────────────────
    pm1_current:            float | None = None
    pm1_baseline:           float | None = None
    pm1_slope_1m:           float | None = None
    pm1_slope_5m:           float | None = None
    pm1_slope_30m:          float | None = None
    pm1_elevated_minutes:   float | None = None
    pm1_peak_ratio:         float | None = None
    pm1_is_declining:       bool  | None = None
    pm1_decay_rate:         float | None = None
    pm1_pulse_detected:     bool  | None = None

    # ── PM2.5 (µg/m³) ────────────────────────────────────────────────────────
    pm25_current:           float | None = None
    pm25_baseline:          float | None = None
    pm25_slope_1m:          float | None = None
    pm25_slope_5m:          float | None = None
    pm25_slope_30m:         float | None = None
    pm25_elevated_minutes:  float | None = None
    pm25_peak_ratio:        float | None = None
    pm25_is_declining:      bool  | None = None
    pm25_decay_rate:        float | None = None
    pm25_pulse_detected:    bool  | None = None

    # ── PM10 (µg/m³) ─────────────────────────────────────────────────────────
    pm10_current:           float | None = None
    pm10_baseline:          float | None = None
    pm10_slope_1m:          float | None = None
    pm10_slope_5m:          float | None = None
    pm10_slope_30m:         float | None = None
    pm10_elevated_minutes:  float | None = None
    pm10_peak_ratio:        float | None = None
    pm10_is_declining:      bool  | None = None
    pm10_decay_rate:        float | None = None
    pm10_pulse_detected:    bool  | None = None

    # ── CO (ppb) ─────────────────────────────────────────────────────────────
    co_current:           float | None = None
    co_baseline:          float | None = None
    co_slope_1m:          float | None = None
    co_slope_5m:          float | None = None
    co_slope_30m:         float | None = None
    co_elevated_minutes:  float | None = None
    co_peak_ratio:        float | None = None
    co_is_declining:      bool  | None = None
    co_decay_rate:        float | None = None
    co_pulse_detected:    bool  | None = None

    # ── NO2 (ppb) ────────────────────────────────────────────────────────────
    no2_current:           float | None = None
    no2_baseline:          float | None = None
    no2_slope_1m:          float | None = None
    no2_slope_5m:          float | None = None
    no2_slope_30m:         float | None = None
    no2_elevated_minutes:  float | None = None
    no2_peak_ratio:        float | None = None
    no2_is_declining:      bool  | None = None
    no2_decay_rate:        float | None = None
    no2_pulse_detected:    bool  | None = None

    # ── NH3 (ppb) ────────────────────────────────────────────────────────────
    nh3_current:           float | None = None
    nh3_baseline:          float | None = None
    nh3_slope_1m:          float | None = None
    nh3_slope_5m:          float | None = None
    nh3_slope_30m:         float | None = None
    nh3_elevated_minutes:  float | None = None
    nh3_peak_ratio:        float | None = None
    nh3_is_declining:      bool  | None = None
    nh3_decay_rate:        float | None = None
    nh3_pulse_detected:    bool  | None = None

    # ── Cross-sensor ─────────────────────────────────────────────────────────
    nh3_lag_behind_tvoc_seconds: float | None = None  # 0–120 s; None = no correlated spike
    pm25_correlated_with_tvoc:   bool  | None = None
    co_correlated_with_tvoc:     bool  | None = None

    # ── Derived ──────────────────────────────────────────────────────────────
    vpd_kpa: float | None = None

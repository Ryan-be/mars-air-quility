# mlss_monitor/inference_evidence.py
"""Pure functions for building structured inference evidence.

All interpretation logic (snapshot, ratios, trend labels, descriptions,
action text) lives here. No IO, no DB access. The DetectionEngine calls
these functions and stores the results in save_inference(evidence=...).
The JS layer renders the pre-computed fields — it never calculates.
"""
from __future__ import annotations

from mlss_monitor.feature_vector import FeatureVector

# ── Channel metadata ──────────────────────────────────────────────────────────
# slope_field: FeatureVector field name for 1-minute slope (None = not available)
# slope_thresh: units/min above which a reading is considered "rising" or "falling"

_CHANNEL_META: dict[str, dict] = {
    "tvoc_current":        {"label": "TVOC",        "unit": "ppb",    "slope_field": "tvoc_slope_1m",        "slope_thresh": 5.0},
    "eco2_current":        {"label": "eCO2",        "unit": "ppm",    "slope_field": "eco2_slope_1m",        "slope_thresh": 10.0},
    "temperature_current": {"label": "Temperature", "unit": "°C",     "slope_field": "temperature_slope_1m", "slope_thresh": 0.1},
    "humidity_current":    {"label": "Humidity",    "unit": "%",      "slope_field": "humidity_slope_1m",    "slope_thresh": 0.5},
    "pm1_current":         {"label": "PM1",         "unit": "µg/m³", "slope_field": "pm1_slope_1m",         "slope_thresh": 1.0},
    "pm25_current":        {"label": "PM2.5",       "unit": "µg/m³", "slope_field": "pm25_slope_1m",        "slope_thresh": 1.0},
    "pm10_current":        {"label": "PM10",        "unit": "µg/m³", "slope_field": "pm10_slope_1m",        "slope_thresh": 1.0},
    "co_current":          {"label": "CO",          "unit": "ppb",    "slope_field": "co_slope_1m",          "slope_thresh": 2.0},
    "no2_current":         {"label": "NO2",         "unit": "ppb",    "slope_field": "no2_slope_1m",         "slope_thresh": 2.0},
    "nh3_current":         {"label": "NH3",         "unit": "ppb",    "slope_field": "nh3_slope_1m",         "slope_thresh": 2.0},
    "vpd_kpa":             {"label": "VPD",         "unit": "kPa",    "slope_field": None,                   "slope_thresh": None},
}

# ── Per-channel anomaly action text ──────────────────────────────────────────

_CHANNEL_ACTIONS: dict[str, str] = {
    "tvoc_ppb":      "Identify chemical sources (cleaning products, paints, adhesives). Ventilate if TVOC stays elevated.",
    "eco2_ppm":      "Open windows or improve ventilation. High CO2 reduces cognitive performance and causes fatigue.",
    "temperature_c": "Adjust heating or cooling to return to the comfort zone (18–25°C).",
    "humidity_pct":  "Use a dehumidifier or humidifier to reach the target range (40–60%).",
    "pm1_ug_m3":     "Identify fine particle sources (candles, incense, cooking smoke). Consider an air purifier with HEPA filter.",
    "pm25_ug_m3":    "Identify fine particle sources and ventilate. Consider running an air purifier.",
    "pm10_ug_m3":    "Check for coarse dust or pollen sources. A HEPA air purifier can help.",
    "co_ppb":        "Identify CO sources (gas appliances, combustion). Ventilate immediately. At high levels, evacuate and call emergency services.",
    "no2_ppb":       "Check gas appliances and ventilation. Prolonged elevated NO2 can irritate the airways.",
    "nh3_ppb":       "Check for ammonia sources (cleaning products, fertilisers, animal waste). Ventilate promptly.",
}

# ── Per-model composite action text ──────────────────────────────────────────

_MODEL_ACTIONS: dict[str, str] = {
    "combustion_signature": (
        "Identify any open flames, candles, or cooking sources. Ventilate immediately. "
        "CO, NO2 and particulates are moving together — this is consistent with combustion."
    ),
    "particle_distribution": (
        "Check for unusual particulate sources. The PM1/PM2.5/PM10 ratio distribution "
        "is abnormal — this may indicate combustion smoke (PM1≈PM2.5) or unusually high "
        "coarse dust (PM10>>PM2.5). Open windows if outdoor air quality allows."
    ),
    "ventilation_quality": (
        "Open a window or run a fan. eCO2, TVOC and NH3 are building up together — "
        "the space needs fresh air. All three rising jointly is a strong ventilation signal."
    ),
    "gas_relationship": (
        "Inspect the MICS6814 gas sensor. CO, NO2 and NH3 have broken their normal "
        "correlation. This can indicate sensor drift, a fault, or a genuinely unusual "
        "gas mixture. If no obvious source, consider recalibrating the sensor."
    ),
    "thermal_moisture": (
        "Check your heating or cooling system. Temperature, humidity and VPD are stressed "
        "together — this pattern is consistent with HVAC failure or extreme outdoor conditions "
        "infiltrating the space. Inspect HVAC filters and seals."
    ),
}

_GENERIC_ACTION = (
    "Monitor the readings. If the anomaly persists, investigate possible sources "
    "and consider improving ventilation."
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _slope_trend(fv: FeatureVector, slope_field: str | None, thresh: float | None) -> str:
    if slope_field is None or thresh is None:
        return "stable"
    slope = getattr(fv, slope_field, None)
    if slope is None:
        return "stable"
    if slope > thresh:
        return "rising"
    if slope < -thresh:
        return "falling"
    return "stable"


def _ratio_band(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= 3.0:
        return "high"
    if ratio >= 1.5:
        return "elevated"
    return "normal"


# ── Public API ────────────────────────────────────────────────────────────────

def build_sensor_snapshot(
    fv: FeatureVector,
    channels: list[str],
    baselines: dict[str, float | None],
) -> list[dict]:
    """Build a structured list of sensor readings for embedding in inference evidence.

    Each entry contains label, value, unit, baseline, ratio, ratio_band, and trend.
    Channels whose FeatureVector value is None are silently skipped.
    The JS layer renders this list directly — no calculation in the browser.

    Args:
        fv: current FeatureVector snapshot.
        channels: FeatureVector field names to include (e.g. "tvoc_current").
        baselines: {channel: ema_baseline} — may contain None values.
    """
    snapshot = []
    for ch in channels:
        meta = _CHANNEL_META.get(ch)
        if meta is None:
            continue
        value = getattr(fv, ch, None)
        if value is None:
            continue
        baseline = baselines.get(ch)
        ratio: float | None = None
        if baseline is not None and baseline > 0:
            ratio = round(value / baseline, 2)
        snapshot.append({
            "channel": ch,
            "label": meta["label"],
            "value": round(float(value), 2),
            "unit": meta["unit"],
            "baseline": round(float(baseline), 2) if baseline is not None else None,
            "ratio": ratio,
            "ratio_band": _ratio_band(ratio),
            "trend": _slope_trend(fv, meta["slope_field"], meta["slope_thresh"]),
        })
    return snapshot


def anomaly_description(
    snapshot: list[dict],
    model_label: str | None = None,
) -> str:
    """Generate a human-readable description from a sensor snapshot.

    For single-channel anomalies (model_label=None), describes the one sensor.
    For composite models, identifies the most elevated dimension.
    """
    if not snapshot:
        return "A statistical anomaly was detected."

    trend_text = {
        "rising":  ", and rising",
        "falling": ", and falling",
        "stable":  "",
    }

    if model_label is None:
        # Single channel
        s = snapshot[0]
        t = trend_text.get(s.get("trend", "stable"), "")
        if s.get("ratio") is not None and s.get("baseline") is not None:
            return (
                f"{s['label']} at {s['value']} {s['unit']} — "
                f"{s['ratio']}× your typical {s['baseline']} {s['unit']}{t}."
            )
        return f"{s['label']} reading of {s['value']} {s['unit']} is statistically unusual{t}."

    # Composite model — find most elevated dimension
    ranked = sorted(
        [s for s in snapshot if s.get("ratio") is not None],
        key=lambda s: s["ratio"],
        reverse=True,
    )
    dims = ", ".join(s["label"] for s in snapshot)
    if ranked:
        worst = ranked[0]
        worst_text = (
            f"Most elevated: {worst['label']} at {worst['value']} {worst['unit']} "
            f"({worst['ratio']}× typical {worst['baseline']} {worst['unit']})."
        )
    else:
        worst_text = ""

    return (
        f"A {model_label} anomaly was detected across {dims}. {worst_text}"
    ).strip()


def anomaly_action(
    model_id: str | None = None,
    channel: str | None = None,
) -> str:
    """Return a contextual recommended action string.

    Pass model_id for composite model anomalies, channel for per-channel ones.
    """
    if model_id and model_id in _MODEL_ACTIONS:
        return _MODEL_ACTIONS[model_id]
    if channel and channel in _CHANNEL_ACTIONS:
        return _CHANNEL_ACTIONS[channel]
    return _GENERIC_ACTION

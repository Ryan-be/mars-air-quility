"""
Environment inference engine.

Continuously analyses sensor data to detect pollution events, environmental
anomalies, and trends.  Each detector function examines recent readings and,
when a notable event is found, persists an inference to the database.

Detectors are designed to be called periodically (e.g. every 60 s) from the
background logging thread in app.py.
"""

import logging
import math
import sqlite3
from datetime import datetime, timedelta

from config import config
from database.db_logger import (
    DB_FILE,
    get_recent_inference_by_type,
    get_thresholds,
    get_thresholds_for_evidence,
    save_inference,
)

log = logging.getLogger(__name__)

# ── Event type registry ──────────────────────────────────────────────────────
# Each event type belongs to a category used for dashboard filtering.

EVENT_TYPES = {
    # Alerts — immediate environmental concerns
    "tvoc_spike":            "alert",
    "eco2_danger":           "alert",
    "eco2_elevated":         "alert",
    "correlated_pollution":  "alert",
    "sustained_poor_air":    "alert",
    "mould_risk":            "warning",
    "pm25_spike":            "alert",
    "pm25_elevated":         "alert",
    "pm10_elevated":         "warning",
    # Warnings — temperature, humidity, VPD concerns
    "temp_high":             "warning",
    "temp_low":              "warning",
    "humidity_high":         "warning",
    "humidity_low":          "warning",
    "vpd_low":               "warning",
    "vpd_high":              "warning",
    "rapid_temp_change":     "warning",
    "rapid_humidity_change": "warning",
    # Summaries — periodic reports
    "hourly_summary":        "summary",
    "daily_summary":         "summary",
    # Patterns — detected trends and recurring behaviours
    "daily_pattern":         "pattern",
    "overnight_buildup":     "pattern",
}

# Annotation-context events are dynamic (annotation_context_<id>)
_ANNOTATION_PREFIX = "annotation_context_"

CATEGORIES = {
    "alert":   "Alerts",
    "warning": "Warnings",
    "summary": "Summaries",
    "pattern": "Patterns",
    "anomaly": "Anomalies",
    "other":   "Other",
}


def event_category(event_type):
    """Return the category for an event type."""
    if event_type.startswith(_ANNOTATION_PREFIX):
        return "pattern"
    if event_type.startswith("anomaly_"):
        return "anomaly"
    return EVENT_TYPES.get(event_type, "other")


MIN_READINGS = 6

# ── Thresholds (loaded from DB, with hardcoded fallbacks) ────────────────────

_DEFAULTS = {
    "tvoc_high": 500, "tvoc_moderate": 250,
    "eco2_cognitive": 1000, "eco2_danger": 2000,
    "temp_high": 28.0, "temp_low": 15.0,
    "hum_high": 70.0, "hum_low": 30.0,
    "vpd_low": 0.4, "vpd_high": 1.6,
    "spike_factor": 2.0, "min_readings": 6,
    "mould_hum": 70.0, "mould_temp": 20.0, "mould_hours": 4,
    "pm25_moderate": 12.0, "pm25_high": 35.0,
    "pm10_high": 50.0, "pm_spike_factor": 3.0,
}

# Module-level cache, refreshed each analysis cycle
_thresholds = dict(_DEFAULTS)


def _refresh_thresholds():
    """Reload thresholds from the database."""
    global _thresholds
    try:
        db_vals = get_thresholds()
        _thresholds = {**_DEFAULTS, **db_vals}
    except Exception:
        _thresholds = dict(_DEFAULTS)


def _t(key):
    """Get a threshold value by key."""
    return _thresholds.get(key, _DEFAULTS.get(key))


def _vpd_kpa(temp_c, rh):
    if temp_c is None or rh is None or rh <= 0:
        return None
    svp = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return svp * (1 - rh / 100)


def _fetch_recent(minutes=30):
    """Fetch sensor_data rows from the last N minutes, oldest first."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    cur.execute(
        "SELECT * FROM sensor_data WHERE timestamp >= ? ORDER BY timestamp ASC",
        (since,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _fetch_annotations(minutes=60):
    """Fetch rows with annotations from the last N minutes."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    cur.execute(
        "SELECT * FROM sensor_data "
        "WHERE annotation IS NOT NULL AND timestamp >= ? "
        "ORDER BY timestamp ASC",
        (since,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _mean(values):
    return sum(values) / len(values) if values else 0


def _std(values):
    if len(values) < 2:
        return 0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _slope(values):
    """Simple linear slope (units per reading)."""
    n = len(values)
    if n < 2:
        return 0
    x_mean = (n - 1) / 2
    y_mean = _mean(values)
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0


# ── Individual detectors ─────────────────────────────────────────────────────

def _detect_tvoc_spike(rows):
    """Detect sudden TVOC spikes above the rolling mean."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return
    tvocs = [r["tvoc"] for r in rows if r["tvoc"] is not None]
    if len(tvocs) < min_readings:
        return

    baseline = _mean(tvocs[:-3])  # mean excluding last 3
    recent = _mean(tvocs[-3:])
    peak = max(tvocs[-3:])

    baseline = max(baseline, 50)  # floor to avoid false positives on near-zero baselines

    spike_factor = _t("spike_factor")
    tvoc_moderate = _t("tvoc_moderate")
    tvoc_high = _t("tvoc_high")

    if recent > baseline * spike_factor and peak > tvoc_moderate:
        if get_recent_inference_by_type("tvoc_spike", hours=1):
            return
        confidence = min(0.95, 0.5 + (recent / baseline - spike_factor) * 0.15)
        annotation_context = _get_annotation_context(rows[-6:])
        thresholds_used = get_thresholds_for_evidence(
            ["tvoc_high", "tvoc_moderate", "spike_factor"])
        save_inference(
            event_type="tvoc_spike",
            severity="warning" if peak > tvoc_high else "info",
            title=f"TVOC spike detected — {int(peak)} ppb",
            description=(
                f"TVOC rose sharply from a baseline of ~{int(baseline)} ppb to "
                f"{int(peak)} ppb. This suggests a new volatile organic compound "
                f"source has been introduced — common causes include cooking, "
                f"cleaning products, paint, adhesives, or a new piece of furniture."
            ),
            action=(
                "Open a window or turn on ventilation. If you can identify the "
                "source (e.g. cleaning spray), remove or contain it. Levels should "
                "return to baseline within 15–60 minutes with adequate airflow."
            ),
            evidence={
                "baseline_tvoc": f"{int(baseline)} ppb",
                "peak_tvoc": f"{int(peak)} ppb",
                "spike_ratio": f"{recent / baseline:.1f}x baseline",
                "readings_analysed": str(len(tvocs)),
                "_thresholds": thresholds_used,
            },
            confidence=round(confidence, 2),
            start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
            end_id=rows[-1]["id"],
            annotation=annotation_context,
        )


def _detect_eco2_threshold(rows):
    """Detect eCO₂ crossing cognitive or danger thresholds."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return
    eco2s = [r["eco2"] for r in rows if r["eco2"] is not None]
    if len(eco2s) < min_readings:
        return

    current = eco2s[-1]
    recent_mean = _mean(eco2s[-5:])
    eco2_danger = _t("eco2_danger")
    eco2_cognitive = _t("eco2_cognitive")

    if recent_mean >= eco2_danger:
        etype = "eco2_danger"
        sev = "critical"
        title = f"CO₂ dangerously high — {int(current)} ppm"
        desc = (
            f"eCO₂ has reached {int(current)} ppm (average {int(recent_mean)} ppm "
            f"over last 5 readings). Above {int(eco2_danger)} ppm causes headaches, "
            f"drowsiness, and significant cognitive impairment."
        )
        action = "Ventilate immediately — open windows and doors. Leave the room if symptoms appear."
        confidence = 0.9
    elif recent_mean >= eco2_cognitive:
        etype = "eco2_elevated"
        sev = "warning"
        title = f"CO₂ elevated — {int(current)} ppm"
        desc = (
            f"eCO₂ has reached {int(current)} ppm. Above {int(eco2_cognitive)} ppm "
            f"studies show measurable decline in decision-making and concentration. "
            f"The room likely needs better ventilation."
        )
        action = "Open a window or activate the fan. Consider taking a break in fresh air."
        confidence = 0.8
    else:
        return

    if get_recent_inference_by_type(etype, hours=1):
        return

    annotation_context = _get_annotation_context(rows[-6:])
    thresholds_used = get_thresholds_for_evidence(["eco2_cognitive", "eco2_danger"])
    save_inference(
        event_type=etype,
        severity=sev,
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_eco2": f"{int(current)} ppm",
            "5_reading_avg": f"{int(recent_mean)} ppm",
            "threshold": f"{int(eco2_danger) if sev == 'critical' else int(eco2_cognitive)} ppm",
            "trend": f"{'rising' if _slope(eco2s[-6:]) > 0 else 'stable/falling'}",
            "_thresholds": thresholds_used,
        },
        confidence=round(confidence, 2),
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
        annotation=annotation_context,
    )


def _detect_temperature_extreme(rows):
    """Detect temperature outside comfort zone."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return
    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    if len(temps) < min_readings:
        return

    recent_mean = _mean(temps[-5:])
    current = temps[-1]
    temp_high = _t("temp_high")
    temp_low = _t("temp_low")

    if recent_mean > temp_high:
        etype = "temp_high"
        sev = "warning"
        title = f"Temperature high — {current:.1f}°C"
        desc = (
            f"Temperature has been averaging {recent_mean:.1f}°C over the last "
            f"5 readings, above the {temp_high}°C comfort threshold. This can "
            f"stress plants and reduce cognitive performance."
        )
        action = "Improve ventilation or use cooling. Check if heat sources (lights, equipment) can be reduced."
    elif recent_mean < temp_low:
        etype = "temp_low"
        sev = "warning"
        title = f"Temperature low — {current:.1f}°C"
        desc = (
            f"Temperature has been averaging {recent_mean:.1f}°C, below the "
            f"{temp_low}°C threshold. Low temperatures slow plant growth and "
            f"can be uncomfortable for occupants."
        )
        action = "Consider heating the space or reducing ventilation to retain warmth."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    thresholds_used = get_thresholds_for_evidence(["temp_high", "temp_low"])
    save_inference(
        event_type=etype,
        severity=sev,
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_temp": f"{current:.1f}°C",
            "5_reading_avg": f"{recent_mean:.1f}°C",
            "threshold": f"{temp_high if 'high' in etype else temp_low}°C",
            "_thresholds": thresholds_used,
        },
        confidence=0.85,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_humidity_extreme(rows):
    """Detect humidity outside ideal range."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return
    hums = [r["humidity"] for r in rows if r["humidity"] is not None]
    if len(hums) < min_readings:
        return

    recent_mean = _mean(hums[-5:])
    current = hums[-1]
    hum_high = _t("hum_high")
    hum_low = _t("hum_low")

    if recent_mean > hum_high:
        etype = "humidity_high"
        title = f"Humidity high — {current:.0f}%"
        desc = (
            f"Humidity averaging {recent_mean:.0f}% over last 5 readings. "
            f"Above {hum_high:.0f}% promotes mould growth and dust mites. "
            f"Combined with warm temperatures this creates ideal conditions "
            f"for fungal issues."
        )
        action = "Increase ventilation or use a dehumidifier. Check for water leaks or standing water."
    elif recent_mean < hum_low:
        etype = "humidity_low"
        title = f"Humidity low — {current:.0f}%"
        desc = (
            f"Humidity averaging {recent_mean:.0f}% over last 5 readings. "
            f"Below {hum_low:.0f}% causes dry skin, irritated airways, and static. "
            f"Plants may show leaf curling and wilting."
        )
        action = "Use a humidifier, place water trays near heat sources, or mist plants."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    thresholds_used = get_thresholds_for_evidence(["hum_high", "hum_low"])
    save_inference(
        event_type=etype,
        severity="info",
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_humidity": f"{current:.0f}%",
            "5_reading_avg": f"{recent_mean:.0f}%",
            "threshold": f"{hum_high if 'high' in etype else hum_low}%",
            "_thresholds": thresholds_used,
        },
        confidence=0.8,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_vpd_extreme(rows):
    """Detect VPD outside optimal range for plants."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return
    vpds = []
    for r in rows:
        v = _vpd_kpa(r.get("temperature"), r.get("humidity"))
        if v is not None:
            vpds.append(v)
    if len(vpds) < min_readings:
        return

    recent_mean = _mean(vpds[-5:])
    current = vpds[-1]
    vpd_low = _t("vpd_low")
    vpd_high = _t("vpd_high")

    if recent_mean < vpd_low:
        etype = "vpd_low"
        title = f"VPD too low — {current:.2f} kPa"
        desc = (
            f"VPD averaging {recent_mean:.2f} kPa. Below {vpd_low} kPa the air "
            f"is nearly saturated, slowing transpiration and creating conditions "
            f"for mould, powdery mildew, and root rot."
        )
        action = "Increase temperature or decrease humidity. Improve air circulation around plants."
    elif recent_mean > vpd_high:
        etype = "vpd_high"
        title = f"VPD too high — {current:.2f} kPa"
        desc = (
            f"VPD averaging {recent_mean:.2f} kPa. Above {vpd_high} kPa plants "
            f"close stomata to conserve water, halting photosynthesis and causing "
            f"leaf tip burn and wilting."
        )
        action = "Increase humidity (misting, humidifier) or reduce temperature. Avoid direct heat on plants."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    thresholds_used = get_thresholds_for_evidence(["vpd_low", "vpd_high"])
    save_inference(
        event_type=etype,
        severity="info",
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_vpd": f"{current:.2f} kPa",
            "5_reading_avg": f"{recent_mean:.2f} kPa",
            "threshold": f"{vpd_low if 'low' in etype else vpd_high} kPa",
            "_thresholds": thresholds_used,
        },
        confidence=0.75,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_mould_risk(rows):
    """Detect sustained warm + humid conditions that promote mould growth."""
    min_readings = int(_t("min_readings"))
    if len(rows) < min_readings:
        return

    mould_hum = _t("mould_hum")
    mould_temp = _t("mould_temp")
    mould_hours = _t("mould_hours")

    # Count how many consecutive recent readings exceed both thresholds
    consecutive = 0
    for r in reversed(rows):
        temp = r.get("temperature")
        hum = r.get("humidity")
        if temp is not None and hum is not None and hum >= mould_hum and temp >= mould_temp:
            consecutive += 1
        else:
            break

    if consecutive < min_readings:
        return

    # Estimate hours from reading count (LOG_INTERVAL is typically 10s)
    log_interval = int(config.get("LOG_INTERVAL", "10"))
    sustained_hours = (consecutive * log_interval) / 3600

    if sustained_hours < mould_hours:
        return

    if get_recent_inference_by_type("mould_risk", hours=6):
        return

    # Calculate dew point for the most recent readings to enrich evidence
    recent = rows[-min(consecutive, 10):]
    temps = [r["temperature"] for r in recent if r["temperature"] is not None]
    hums = [r["humidity"] for r in recent if r["humidity"] is not None]
    avg_temp = _mean(temps)
    avg_hum = _mean(hums)

    # Dew point (Magnus formula)
    a, b = 17.625, 243.04
    alpha = math.log(avg_hum / 100) + a * avg_temp / (b + avg_temp)
    dew_point = (b * alpha) / (a - alpha)
    dew_margin = avg_temp - dew_point

    sev = "critical" if sustained_hours >= mould_hours * 2 or avg_hum >= 80 else "warning"

    thresholds_used = get_thresholds_for_evidence(["mould_hum", "mould_temp", "mould_hours"])
    save_inference(
        event_type="mould_risk",
        severity=sev,
        title=f"Mould risk — {avg_hum:.0f}% RH for {sustained_hours:.1f}h",
        description=(
            f"Humidity has been above {mould_hum:.0f}% with temperature above "
            f"{mould_temp:.0f}°C for approximately {sustained_hours:.1f} hours "
            f"({consecutive} consecutive readings). These warm, humid conditions "
            f"are ideal for mould growth, particularly Aspergillus and Cladosporium "
            f"species. The dew point margin is only {dew_margin:.1f}°C — surfaces "
            f"cooler than {dew_point:.1f}°C will have condensation."
        ),
        action=(
            "Reduce humidity urgently: run a dehumidifier, increase ventilation, "
            "and check for water sources (leaks, standing water, drying clothes). "
            "Inspect corners, window frames, and behind furniture for early mould. "
            "Target humidity below 60% to stop mould progression."
        ),
        evidence={
            "avg_humidity": f"{avg_hum:.0f}%",
            "avg_temperature": f"{avg_temp:.1f}°C",
            "sustained_hours": f"{sustained_hours:.1f} hrs",
            "consecutive_readings": str(consecutive),
            "dew_point": f"{dew_point:.1f}°C",
            "dew_margin": f"{dew_margin:.1f}°C",
            "_thresholds": thresholds_used,
        },
        confidence=round(min(0.95, 0.7 + sustained_hours * 0.03), 2),
        start_id=rows[-consecutive]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_correlated_pollution(rows):
    """Detect when TVOC and eCO₂ rise together (common source)."""
    if len(rows) < 10:
        return
    tvocs = [r["tvoc"] for r in rows if r["tvoc"] is not None]
    eco2s = [r["eco2"] for r in rows if r["eco2"] is not None]
    n = min(len(tvocs), len(eco2s))
    if n < 10:
        return

    tvocs = tvocs[-n:]
    eco2s = eco2s[-n:]

    tvoc_slope = _slope(tvocs)
    eco2_slope = _slope(eco2s)

    # Both rising significantly
    if tvoc_slope > 5 and eco2_slope > 10 and tvocs[-1] > _t("tvoc_moderate") and eco2s[-1] > 800:
        if get_recent_inference_by_type("correlated_pollution", hours=2):
            return

        # Pearson correlation
        t_mean, e_mean = _mean(tvocs), _mean(eco2s)
        num = sum((t - t_mean) * (e - e_mean) for t, e in zip(tvocs, eco2s))
        den_t = math.sqrt(sum((t - t_mean) ** 2 for t in tvocs))
        den_e = math.sqrt(sum((e - e_mean) ** 2 for e in eco2s))
        r = num / (den_t * den_e) if den_t and den_e else 0

        if r < 0.6:
            return

        annotation_context = _get_annotation_context(rows[-10:])
        thresholds_used = get_thresholds_for_evidence(
            ["tvoc_moderate", "eco2_cognitive"])
        save_inference(
            event_type="correlated_pollution",
            severity="warning",
            title="TVOC and CO₂ rising together — likely shared source",
            description=(
                f"Both TVOC ({int(tvocs[-1])} ppb) and eCO₂ ({int(eco2s[-1])} ppm) "
                f"are rising simultaneously with a correlation of {r:.2f}. This "
                f"strongly suggests a single pollution source — such as cooking, "
                f"a running vehicle nearby, combustion, or multiple people in a "
                f"poorly ventilated space."
            ),
            action=(
                "Identify and remove the source. Ventilate the space. If cooking, "
                "use an extractor hood. If people-related, increase ventilation rate."
            ),
            evidence={
                "tvoc_current": f"{int(tvocs[-1])} ppb",
                "eco2_current": f"{int(eco2s[-1])} ppm",
                "correlation_r": f"{r:.2f}",
                "tvoc_trend": f"+{tvoc_slope:.1f} ppb/reading",
                "eco2_trend": f"+{eco2_slope:.1f} ppm/reading",
                "_thresholds": thresholds_used,
            },
            confidence=round(min(0.95, 0.6 + r * 0.2), 2),
            start_id=rows[-10]["id"],
            end_id=rows[-1]["id"],
            annotation=annotation_context,
        )


def _detect_rapid_change(rows):
    """Detect rapid temperature or humidity swings."""
    if len(rows) < MIN_READINGS:
        return

    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    hums = [r["humidity"] for r in rows if r["humidity"] is not None]

    # Check temperature swings (> 3°C in the analysis window)
    if len(temps) >= MIN_READINGS:
        t_range = max(temps[-MIN_READINGS:]) - min(temps[-MIN_READINGS:])
        if t_range > 3.0:
            if not get_recent_inference_by_type("rapid_temp_change", hours=1):
                save_inference(
                    event_type="rapid_temp_change",
                    severity="info",
                    title=f"Rapid temperature swing — {t_range:.1f}°C range",
                    description=(
                        f"Temperature varied by {t_range:.1f}°C in the last "
                        f"{MIN_READINGS} readings ({min(temps[-MIN_READINGS:]):.1f}°C "
                        f"to {max(temps[-MIN_READINGS:]):.1f}°C). Rapid swings can "
                        f"stress plants and indicate drafts, intermittent heating, "
                        f"or a door/window being opened."
                    ),
                    action="Check for drafts, open doors/windows, or thermostat cycling.",
                    evidence={
                        "temp_min": f"{min(temps[-MIN_READINGS:]):.1f}°C",
                        "temp_max": f"{max(temps[-MIN_READINGS:]):.1f}°C",
                        "range": f"{t_range:.1f}°C",
                    },
                    confidence=0.7,
                    start_id=rows[-MIN_READINGS]["id"],
                    end_id=rows[-1]["id"],
                )

    # Check humidity swings (> 15% in the analysis window)
    if len(hums) >= MIN_READINGS:
        h_range = max(hums[-MIN_READINGS:]) - min(hums[-MIN_READINGS:])
        if h_range > 15.0:
            if not get_recent_inference_by_type("rapid_humidity_change", hours=1):
                save_inference(
                    event_type="rapid_humidity_change",
                    severity="info",
                    title=f"Rapid humidity swing — {h_range:.0f}% range",
                    description=(
                        f"Humidity varied by {h_range:.0f}% in the last "
                        f"{MIN_READINGS} readings ({min(hums[-MIN_READINGS:]):.0f}% "
                        f"to {max(hums[-MIN_READINGS:]):.0f}%). This could indicate "
                        f"ventilation changes, shower/cooking steam, or a humidifier "
                        f"cycling on and off."
                    ),
                    action="Check if a humidifier, shower, or cooking activity is the cause.",
                    evidence={
                        "humidity_min": f"{min(hums[-MIN_READINGS:]):.0f}%",
                        "humidity_max": f"{max(hums[-MIN_READINGS:]):.0f}%",
                        "range": f"{h_range:.0f}%",
                    },
                    confidence=0.65,
                    start_id=rows[-MIN_READINGS]["id"],
                    end_id=rows[-1]["id"],
                )


def _detect_sustained_poor_air(rows):
    """Detect prolonged poor air quality (TVOC or eCO₂ high for many readings)."""
    if len(rows) < 12:
        return
    window = rows[-12:]
    tvocs = [r["tvoc"] for r in window if r["tvoc"] is not None]
    eco2s = [r["eco2"] for r in window if r["eco2"] is not None]
    tvoc_moderate = _t("tvoc_moderate")

    tvoc_high_count = sum(1 for v in tvocs if v > tvoc_moderate)
    eco2_high_count = sum(1 for v in eco2s if v > 800)

    if tvoc_high_count >= 10 or eco2_high_count >= 10:
        if get_recent_inference_by_type("sustained_poor_air", hours=3):
            return
        thresholds_used = get_thresholds_for_evidence(
            ["tvoc_moderate", "eco2_cognitive"])
        save_inference(
            event_type="sustained_poor_air",
            severity="warning",
            title="Sustained poor air quality",
            description=(
                f"Air quality has been degraded for an extended period: "
                f"TVOC exceeded {int(tvoc_moderate)} ppb in {tvoc_high_count}/{len(tvocs)} "
                f"readings, eCO₂ exceeded 800 ppm in {eco2_high_count}/{len(eco2s)} "
                f"readings (last 12 data points). This is not a spike — it suggests "
                f"a persistent source or insufficient ventilation."
            ),
            action=(
                "This needs more than just opening a window briefly. Consider: "
                "running the fan continuously, identifying a persistent VOC source "
                "(new furniture, paint, carpet), or increasing the room's base "
                "ventilation rate."
            ),
            evidence={
                "tvoc_high_readings": f"{tvoc_high_count}/{len(tvocs)}",
                "eco2_high_readings": f"{eco2_high_count}/{len(eco2s)}",
                "avg_tvoc": f"{int(_mean(tvocs))} ppb",
                "avg_eco2": f"{int(_mean(eco2s))} ppm",
                "_thresholds": thresholds_used,
            },
            confidence=0.85,
            start_id=window[0]["id"],
            end_id=window[-1]["id"],
        )


def _detect_pm25_spike(rows):
    """Detect a sudden spike in PM2.5 above the rolling mean."""
    pm_rows = [r for r in rows if r.get("pm2_5") is not None]
    min_readings = max(4, int(_t("min_readings")))
    if len(pm_rows) < min_readings:
        return

    values = [r["pm2_5"] for r in pm_rows]
    baseline = _mean(values[:-3])
    recent   = _mean(values[-3:])
    peak     = max(values[-3:])

    baseline = max(baseline, 5.0)  # floor to avoid false positives near zero

    spike_factor = _t("pm_spike_factor")
    pm25_moderate = _t("pm25_moderate")
    pm25_high     = _t("pm25_high")

    if recent > baseline * spike_factor and peak > pm25_moderate:
        if get_recent_inference_by_type("pm25_spike", hours=1):
            return
        confidence = min(0.95, 0.5 + (recent / baseline - spike_factor) * 0.1)
        annotation_context = _get_annotation_context(rows[-6:])
        thresholds_used = get_thresholds_for_evidence(
            ["pm25_moderate", "pm25_high", "pm_spike_factor"])
        save_inference(
            event_type="pm25_spike",
            severity="warning" if peak > pm25_high else "info",
            title=f"PM2.5 spike detected — {int(peak)} µg/m³",
            description=(
                f"PM2.5 rose sharply from a baseline of ~{baseline:.1f} µg/m³ to "
                f"{int(peak)} µg/m³. Common causes include cooking (especially "
                f"frying or grilling), candles, incense, a nearby road event, "
                f"or a dust disturbance."
            ),
            action=(
                "Increase ventilation immediately. If cooking is the source, "
                "use the extractor fan and open a window. Levels should return "
                "to baseline within 15–30 minutes with adequate airflow."
            ),
            evidence={
                "baseline_pm25": f"{baseline:.1f} µg/m³",
                "peak_pm25": f"{int(peak)} µg/m³",
                "spike_ratio": f"{recent / baseline:.1f}x baseline",
                "readings_analysed": str(len(values)),
                "_thresholds": thresholds_used,
            },
            confidence=round(confidence, 2),
            start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
            end_id=rows[-1]["id"],
            annotation=annotation_context,
        )


def _detect_pm_elevated(rows):
    """Detect sustained elevated PM2.5 or PM10 over the last 30 minutes."""
    pm_rows = [r for r in rows if r.get("pm2_5") is not None]
    if len(pm_rows) < 6:
        return

    pm25_vals = [r["pm2_5"] for r in pm_rows]
    pm10_vals = [r["pm10"] for r in pm_rows if r.get("pm10") is not None]

    pm25_moderate = _t("pm25_moderate")
    pm25_high     = _t("pm25_high")
    pm10_high     = _t("pm10_high")

    mean_pm25 = _mean(pm25_vals)
    mean_pm10 = _mean(pm10_vals) if pm10_vals else None

    # PM2.5 elevated
    pm25_high_count = sum(1 for v in pm25_vals if v > pm25_moderate)
    if pm25_high_count >= len(pm25_vals) * 0.7:
        if not get_recent_inference_by_type("pm25_elevated", hours=2):
            sev = "warning" if mean_pm25 > pm25_high else "info"
            thresholds_used = get_thresholds_for_evidence(["pm25_moderate", "pm25_high"])
            annotation_context = _get_annotation_context(rows[-6:])
            save_inference(
                event_type="pm25_elevated",
                severity=sev,
                title=f"PM2.5 elevated — avg {mean_pm25:.1f} µg/m³",
                description=(
                    f"PM2.5 has been above the 'good' threshold ({pm25_moderate} µg/m³) "
                    f"for {pm25_high_count}/{len(pm25_vals)} readings in the last 30 minutes "
                    f"(avg {mean_pm25:.1f} µg/m³). This is not a spike — it suggests a "
                    f"persistent particle source or poor outdoor air infiltrating indoors."
                ),
                action=(
                    "Check for ongoing combustion sources (candles, incense, gas cooking). "
                    "If outdoor air quality is poor, keep windows closed and run ventilation "
                    "with a HEPA filter if available."
                ),
                evidence={
                    "avg_pm25": f"{mean_pm25:.1f} µg/m³",
                    "high_readings": f"{pm25_high_count}/{len(pm25_vals)}",
                    "_thresholds": thresholds_used,
                },
                confidence=0.8,
                start_id=rows[0]["id"],
                end_id=rows[-1]["id"],
                annotation=annotation_context,
            )

    # PM10 elevated (separate event)
    if pm10_vals and mean_pm10 is not None and mean_pm10 > pm10_high:
        if not get_recent_inference_by_type("pm10_elevated", hours=2):
            thresholds_used = get_thresholds_for_evidence(["pm10_high"])
            annotation_context = _get_annotation_context(rows[-6:])
            save_inference(
                event_type="pm10_elevated",
                severity="warning",
                title=f"PM10 elevated — avg {mean_pm10:.1f} µg/m³",
                description=(
                    f"PM10 (coarse particles ≤10 µm) has averaged {mean_pm10:.1f} µg/m³ "
                    f"over the past 30 minutes, exceeding the WHO 24-hour guideline of "
                    f"{pm10_high} µg/m³. Sources include dust, pollen, mould spores, "
                    f"and soil tracked indoors."
                ),
                action=(
                    "Vacuum with a HEPA filter, close windows if outdoor air is dusty, "
                    "and check for dust or mould sources in the room."
                ),
                evidence={
                    "avg_pm10": f"{mean_pm10:.1f} µg/m³",
                    "who_guideline": f"{pm10_high} µg/m³",
                    "_thresholds": thresholds_used,
                },
                confidence=0.75,
                start_id=rows[0]["id"],
                end_id=rows[-1]["id"],
                annotation=annotation_context,
            )


def _detect_annotation_context_event(rows):
    """When user adds an annotation near a notable reading, create a contextual inference."""
    annotated = _fetch_annotations(minutes=10)
    if not annotated:
        return

    for row in annotated:
        event_key = f"annotation_context_{row['id']}"
        if get_recent_inference_by_type(event_key, hours=24):
            continue

        tvoc = row.get("tvoc", 0)
        eco2 = row.get("eco2", 0)
        temp = row.get("temperature", 0)
        annotation = row.get("annotation", "")

        if tvoc > _t("tvoc_moderate") or eco2 > 800 or temp > _t("temp_high") or temp < _t("temp_low"):
            conditions = []
            if tvoc > _t("tvoc_moderate"):
                conditions.append(f"TVOC {tvoc} ppb")
            if eco2 > 800:
                conditions.append(f"eCO₂ {eco2} ppm")
            if temp > _t("temp_high"):
                conditions.append(f"temp {temp:.1f}°C")
            if temp < _t("temp_low"):
                conditions.append(f"temp {temp:.1f}°C")

            save_inference(
                event_type=event_key,
                severity="info",
                title=f"Annotated event: {annotation[:60]}",
                description=(
                    f"You annotated a data point at {row['timestamp']} with: "
                    f'"{annotation}". At that time conditions were notable: '
                    f"{', '.join(conditions)}. This annotation has been linked to "
                    f"the environmental data for future reference."
                ),
                action="Review this annotation alongside the sensor data to build a pattern of known events.",
                evidence={
                    "annotation": annotation,
                    "timestamp": row["timestamp"],
                    "tvoc": f"{tvoc} ppb",
                    "eco2": f"{eco2} ppm",
                    "temperature": f"{temp:.1f}°C",
                },
                confidence=0.6,
                start_id=row["id"],
                end_id=row["id"],
                annotation=annotation,
            )


def _get_annotation_context(rows):
    """Return combined annotation text from recent rows, if any."""
    annotations = [
        r["annotation"] for r in rows
        if r.get("annotation")
    ]
    return " | ".join(annotations) if annotations else None


# ── Long-term detectors (1 hour) ─────────────────────────────────────────────

def _hourly_summary(rows):
    """Analyse the last hour of data and produce a summary inference."""
    if len(rows) < 20:
        return
    if get_recent_inference_by_type("hourly_summary", hours=1):
        return

    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    hums  = [r["humidity"]    for r in rows if r["humidity"] is not None]
    tvocs = [r["tvoc"]        for r in rows if r["tvoc"] is not None]
    eco2s = [r["eco2"]        for r in rows if r["eco2"] is not None]

    if not temps or not hums or not tvocs or not eco2s:
        return

    # Compute statistics
    temp_mean, temp_std = _mean(temps), _std(temps)
    hum_mean,  hum_std  = _mean(hums),  _std(hums)
    tvoc_mean            = _mean(tvocs)
    eco2_mean            = _mean(eco2s)
    tvoc_peak            = max(tvocs)
    eco2_peak            = max(eco2s)
    temp_slope           = _slope(temps)
    hum_slope            = _slope(hums)
    tvoc_slope           = _slope(tvocs)
    eco2_slope           = _slope(eco2s)

    # Build trend descriptions
    def _trend_word(slope, threshold=0.05):
        if slope > threshold:
            return "rising"
        if slope < -threshold:
            return "falling"
        return "stable"

    temp_trend = _trend_word(temp_slope, 0.02)
    hum_trend  = _trend_word(hum_slope, 0.1)
    tvoc_trend = _trend_word(tvoc_slope, 0.5)
    eco2_trend = _trend_word(eco2_slope, 1.0)

    # Determine severity
    issues = []
    if tvoc_mean > _t("tvoc_moderate"):
        issues.append(f"avg TVOC {int(tvoc_mean)} ppb (above {int(_t('tvoc_moderate'))})")
    if eco2_mean > 800:
        issues.append(f"avg eCO₂ {int(eco2_mean)} ppm (above 800)")
    if temp_mean > _t("temp_high") or temp_mean < _t("temp_low"):
        issues.append(f"avg temp {temp_mean:.1f}°C (outside {_t('temp_low')}–{_t('temp_high')})")
    if hum_mean > _t("hum_high") or hum_mean < _t("hum_low"):
        issues.append(f"avg humidity {hum_mean:.0f}% (outside {_t('hum_low'):.0f}–{_t('hum_high'):.0f})")

    sev = "warning" if len(issues) >= 2 else "info"
    quality = "Poor" if len(issues) >= 2 else "Fair" if issues else "Good"

    # Stability assessment
    stability_issues = []
    if temp_std > 2.0:
        stability_issues.append(f"temperature varied ±{temp_std:.1f}°C")
    if hum_std > 8.0:
        stability_issues.append(f"humidity varied ±{hum_std:.0f}%")
    stability = ("Unstable — " + ", ".join(stability_issues)) if stability_issues else "Stable"

    desc_parts = [
        f"Over the past hour ({len(rows)} readings):",
        f"Temperature: {temp_mean:.1f}°C (±{temp_std:.1f}), {temp_trend}.",
        f"Humidity: {hum_mean:.0f}% (±{hum_std:.0f}), {hum_trend}.",
        f"TVOC: avg {int(tvoc_mean)} ppb, peak {int(tvoc_peak)} ppb, {tvoc_trend}.",
        f"eCO₂: avg {int(eco2_mean)} ppm, peak {int(eco2_peak)} ppm, {eco2_trend}.",
    ]
    if issues:
        desc_parts.append(f"Issues: {'; '.join(issues)}.")
    if stability_issues:
        desc_parts.append(f"Stability: {stability}.")

    annotation_context = _get_annotation_context(rows)
    save_inference(
        event_type="hourly_summary",
        severity=sev,
        title=f"Hourly summary — {quality} air quality",
        description=" ".join(desc_parts),
        action=(
            "Address the issues noted above. " + ("; ".join(issues) + "." if issues else "")
            if issues else "No action needed — environment is within normal ranges."
        ),
        evidence={
            "period": "1 hour",
            "readings": str(len(rows)),
            "temp_avg": f"{temp_mean:.1f}°C",
            "temp_range": f"{min(temps):.1f} – {max(temps):.1f}°C",
            "temp_trend": temp_trend,
            "humidity_avg": f"{hum_mean:.0f}%",
            "humidity_trend": hum_trend,
            "tvoc_avg": f"{int(tvoc_mean)} ppb",
            "tvoc_peak": f"{int(tvoc_peak)} ppb",
            "tvoc_trend": tvoc_trend,
            "eco2_avg": f"{int(eco2_mean)} ppm",
            "eco2_peak": f"{int(eco2_peak)} ppm",
            "eco2_trend": eco2_trend,
            "stability": stability,
            "overall": quality,
        },
        confidence=0.9,
        start_id=rows[0]["id"],
        end_id=rows[-1]["id"],
        annotation=annotation_context,
    )


# ── Long-term detectors (24 hours) ──────────────────────────────────────────

def _daily_summary(rows):
    """Analyse the last 24 hours and produce a daily environment report."""
    if len(rows) < 100:
        return
    if get_recent_inference_by_type("daily_summary", hours=23):
        return

    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    hums  = [r["humidity"]    for r in rows if r["humidity"] is not None]
    tvocs = [r["tvoc"]        for r in rows if r["tvoc"] is not None]
    eco2s = [r["eco2"]        for r in rows if r["eco2"] is not None]

    if not all([temps, hums, tvocs, eco2s]):
        return

    # Basic stats
    temp_mean, temp_min, temp_max = _mean(temps), min(temps), max(temps)
    hum_mean, hum_min, hum_max = _mean(hums), min(hums), max(hums)
    tvoc_mean, tvoc_peak = _mean(tvocs), max(tvocs)
    eco2_mean, eco2_peak = _mean(eco2s), max(eco2s)

    # Time in bad zones
    tvoc_high_pct = sum(1 for v in tvocs if v > _t("tvoc_moderate")) / len(tvocs) * 100
    eco2_high_pct = sum(1 for v in eco2s if v > 800) / len(eco2s) * 100
    temp_out_pct  = sum(1 for v in temps if v > _t("temp_high") or v < _t("temp_low")) / len(temps) * 100
    hum_out_pct   = sum(1 for v in hums if v > _t("hum_high") or v < _t("hum_low")) / len(hums) * 100

    # VPD analysis
    vpds = [_vpd_kpa(r.get("temperature"), r.get("humidity")) for r in rows]
    vpds = [v for v in vpds if v is not None]
    vpd_mean = _mean(vpds) if vpds else None
    vpd_opt_pct = sum(1 for v in vpds if 0.4 <= v <= 1.6) / len(vpds) * 100 if vpds else 0

    # Overall score (simple weighted)
    score = 100
    if tvoc_high_pct > 0:
        score -= tvoc_high_pct * 0.3
    if eco2_high_pct > 0:
        score -= eco2_high_pct * 0.3
    if temp_out_pct > 0:
        score -= temp_out_pct * 0.2
    if hum_out_pct > 0:
        score -= hum_out_pct * 0.2
    score = max(0, min(100, round(score)))

    if score >= 80:
        quality, sev = "Good", "info"
    elif score >= 50:
        quality, sev = "Fair", "info"
    else:
        quality, sev = "Poor", "warning"

    # Annotations count
    annotated = [r for r in rows if r.get("annotation")]
    anno_str = f" You added {len(annotated)} annotation(s) during this period." if annotated else ""

    # Build description
    desc = (
        f"24-hour environment report ({len(rows)} readings). "
        f"Temperature: {temp_mean:.1f}°C avg (range {temp_min:.1f}–{temp_max:.1f}°C), "
        f"outside comfort zone {temp_out_pct:.0f}% of the time. "
        f"Humidity: {hum_mean:.0f}% avg (range {hum_min:.0f}–{hum_max:.0f}%), "
        f"outside ideal range {hum_out_pct:.0f}% of the time. "
        f"TVOC: avg {int(tvoc_mean)} ppb, peak {int(tvoc_peak)} ppb, "
        f"above moderate ({int(_t('tvoc_moderate'))} ppb) for {tvoc_high_pct:.0f}% of readings. "
        f"eCO₂: avg {int(eco2_mean)} ppm, peak {int(eco2_peak)} ppm, "
        f"above 800 ppm for {eco2_high_pct:.0f}% of readings."
    )
    if vpd_mean is not None:
        desc += (
            f" VPD: avg {vpd_mean:.2f} kPa, "
            f"in optimal range {vpd_opt_pct:.0f}% of the time."
        )
    desc += (
        f" Overall environment score: {score}/100 ({quality}).{anno_str}"
    )

    # Action items
    actions = []
    if tvoc_high_pct > 20:
        actions.append(f"TVOC was elevated {tvoc_high_pct:.0f}% of the day — investigate persistent VOC sources")
    if eco2_high_pct > 20:
        actions.append(f"eCO₂ was high {eco2_high_pct:.0f}% of the day — improve base ventilation rate")
    if temp_out_pct > 30:
        actions.append(f"Temperature was outside comfort zone {temp_out_pct:.0f}% of the day — check heating/cooling")
    if hum_out_pct > 30:
        actions.append(
            f"Humidity was outside ideal range {hum_out_pct:.0f}% of the day"
            " — consider humidifier/dehumidifier"
        )
    if vpds and vpd_opt_pct < 50:
        actions.append(
            f"VPD was optimal only {vpd_opt_pct:.0f}% of the day"
            " — adjust temp/humidity for plant health"
        )
    action = (
        ". ".join(actions) + "." if actions
        else "Environment was generally within acceptable ranges — no action needed."
    )

    annotation_context = _get_annotation_context(annotated) if annotated else None
    save_inference(
        event_type="daily_summary",
        severity=sev,
        title=f"Daily report — {score}/100 ({quality})",
        description=desc,
        action=action,
        evidence={
            "period": "24 hours",
            "readings": str(len(rows)),
            "score": f"{score}/100",
            "temp_avg": f"{temp_mean:.1f}°C",
            "temp_range": f"{temp_min:.1f} – {temp_max:.1f}°C",
            "temp_out_of_range": f"{temp_out_pct:.0f}%",
            "humidity_avg": f"{hum_mean:.0f}%",
            "humidity_range": f"{hum_min:.0f} – {hum_max:.0f}%",
            "humidity_out_of_range": f"{hum_out_pct:.0f}%",
            "tvoc_avg": f"{int(tvoc_mean)} ppb",
            "tvoc_peak": f"{int(tvoc_peak)} ppb",
            "tvoc_above_moderate": f"{tvoc_high_pct:.0f}%",
            "eco2_avg": f"{int(eco2_mean)} ppm",
            "eco2_peak": f"{int(eco2_peak)} ppm",
            "eco2_above_800": f"{eco2_high_pct:.0f}%",
            "vpd_avg": f"{vpd_mean:.2f} kPa" if vpd_mean else "N/A",
            "vpd_optimal_time": f"{vpd_opt_pct:.0f}%",
            "annotations": str(len(annotated)),
        },
        confidence=0.95,
        start_id=rows[0]["id"],
        end_id=rows[-1]["id"],
        annotation=annotation_context,
    )


def _detect_daily_patterns(rows):
    """Detect recurring patterns in the 24h data (e.g. regular spikes at certain times)."""
    if len(rows) < 100:
        return
    if get_recent_inference_by_type("daily_pattern", hours=23):
        return

    # Bucket readings by hour
    hourly_tvoc = {}
    hourly_eco2 = {}
    for r in rows:
        try:
            hour = datetime.fromisoformat(r["timestamp"]).hour
        except (ValueError, TypeError):
            continue
        if r.get("tvoc") is not None:
            hourly_tvoc.setdefault(hour, []).append(r["tvoc"])
        if r.get("eco2") is not None:
            hourly_eco2.setdefault(hour, []).append(r["eco2"])

    # Find hours with notably high averages
    overall_tvoc_mean = _mean([v for vs in hourly_tvoc.values() for v in vs])
    overall_eco2_mean = _mean([v for vs in hourly_eco2.values() for v in vs])

    problem_hours = []
    for hour in sorted(hourly_tvoc.keys()):
        h_tvoc = _mean(hourly_tvoc.get(hour, []))
        h_eco2 = _mean(hourly_eco2.get(hour, []))
        if (h_tvoc > overall_tvoc_mean * 1.5 and h_tvoc > _t("tvoc_moderate")) or \
           (h_eco2 > overall_eco2_mean * 1.5 and h_eco2 > 800):
            problem_hours.append({
                "hour": hour,
                "tvoc_avg": int(h_tvoc),
                "eco2_avg": int(h_eco2),
            })

    if not problem_hours:
        return

    hours_str = ", ".join(f"{h['hour']:02d}:00" for h in problem_hours)
    details = "; ".join(
        f"{h['hour']:02d}:00 (TVOC {h['tvoc_avg']} ppb, eCO₂ {h['eco2_avg']} ppm)"
        for h in problem_hours
    )

    save_inference(
        event_type="daily_pattern",
        severity="info",
        title=f"Recurring pollution pattern — peaks at {hours_str}",
        description=(
            f"Analysis of the last 24 hours shows air quality consistently "
            f"degrades at certain times of day: {details}. "
            f"This suggests a recurring activity (cooking, commute, heating "
            f"schedule, occupancy pattern) is responsible. Identifying the cause "
            f"lets you pre-emptively ventilate."
        ),
        action=(
            f"Consider starting ventilation 15 minutes before the typical "
            f"spike times ({hours_str}). Add annotations at these times to help "
            f"identify the specific activity."
        ),
        evidence={
            "peak_hours": hours_str,
            "details": details,
            "overall_tvoc_avg": f"{int(overall_tvoc_mean)} ppb",
            "overall_eco2_avg": f"{int(overall_eco2_mean)} ppm",
            "hours_analysed": str(len(hourly_tvoc)),
        },
        confidence=0.7,
        start_id=rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_overnight_trend(rows):
    """Detect overnight build-up (eCO₂/TVOC rising while likely sleeping)."""
    if len(rows) < 100:
        return
    if get_recent_inference_by_type("overnight_buildup", hours=23):
        return

    # Filter to 23:00–07:00 window
    night_rows = []
    for r in rows:
        try:
            hour = datetime.fromisoformat(r["timestamp"]).hour
        except (ValueError, TypeError):
            continue
        if hour >= 23 or hour < 7:
            night_rows.append(r)

    if len(night_rows) < 20:
        return

    eco2s = [r["eco2"] for r in night_rows if r["eco2"] is not None]
    tvocs = [r["tvoc"] for r in night_rows if r["tvoc"] is not None]
    if len(eco2s) < 20:
        return

    eco2_start = _mean(eco2s[:5])
    eco2_end = _mean(eco2s[-5:])
    eco2_rise = eco2_end - eco2_start

    if eco2_rise > 200 and eco2_end > 800:
        save_inference(
            event_type="overnight_buildup",
            severity="warning" if eco2_end > _t("eco2_cognitive") else "info",
            title=f"Overnight CO₂ build-up — rose by {int(eco2_rise)} ppm",
            description=(
                f"eCO₂ rose from ~{int(eco2_start)} ppm to ~{int(eco2_end)} ppm "
                f"between 23:00 and 07:00. This is a common pattern in bedrooms "
                f"with closed windows — one sleeping adult produces ~200 mL/min of "
                f"CO₂. By morning, levels can significantly exceed the 1000 ppm "
                f"cognitive impairment threshold, leading to poor sleep quality "
                f"and grogginess."
            ),
            action=(
                "Consider cracking a window at night or running a quiet fan on a "
                "low setting. Even a small gap provides enough air exchange to keep "
                "CO₂ below 1000 ppm in most rooms."
            ),
            evidence={
                "period": "23:00 – 07:00",
                "eco2_at_start": f"{int(eco2_start)} ppm",
                "eco2_at_end": f"{int(eco2_end)} ppm",
                "eco2_rise": f"+{int(eco2_rise)} ppm",
                "night_readings": str(len(night_rows)),
                "tvoc_avg": f"{int(_mean(tvocs))} ppb" if tvocs else "N/A",
            },
            confidence=0.85,
            start_id=night_rows[0]["id"],
            end_id=night_rows[-1]["id"],
        )


# ── Public entry points ──────────────────────────────────────────────────────

def run_analysis():
    """Run short-term detectors against recent data. Call every ~60s."""
    try:
        _refresh_thresholds()
        rows = _fetch_recent(minutes=30)
        if len(rows) < MIN_READINGS:
            return

        _detect_tvoc_spike(rows)
        _detect_eco2_threshold(rows)
        _detect_temperature_extreme(rows)
        _detect_humidity_extreme(rows)
        _detect_vpd_extreme(rows)
        _detect_mould_risk(rows)
        _detect_correlated_pollution(rows)
        _detect_rapid_change(rows)
        _detect_sustained_poor_air(rows)
        _detect_pm25_spike(rows)
        _detect_pm_elevated(rows)
        _detect_annotation_context_event(rows)
    except Exception as e:
        log.error("Inference engine error: %s", e)


def run_hourly_analysis():
    """Run 1-hour detectors. Call every ~60 minutes."""
    try:
        _refresh_thresholds()
        rows = _fetch_recent(minutes=60)
        if len(rows) < 20:
            return
        _hourly_summary(rows)
    except Exception as e:
        log.error("Hourly inference error: %s", e)


def run_daily_analysis():
    """Run 24-hour detectors. Call every ~24 hours."""
    try:
        _refresh_thresholds()
        rows = _fetch_recent(minutes=1440)
        if len(rows) < 100:
            return
        _daily_summary(rows)
        _detect_daily_patterns(rows)
        _detect_overnight_trend(rows)
    except Exception as e:
        log.error("Daily inference error: %s", e)


def run_startup_analysis():
    """Run on application startup — backfill any missing long-term analyses."""
    log.info("Inference engine: running startup analysis on historical data…")
    try:
        _refresh_thresholds()
        # Hourly: run if no hourly summary exists in the last hour
        if not get_recent_inference_by_type("hourly_summary", hours=1):
            rows_1h = _fetch_recent(minutes=60)
            if len(rows_1h) >= 20:
                _hourly_summary(rows_1h)
                log.info("Inference engine: generated hourly summary from historical data")

        # Daily: run if no daily summary exists in the last 23 hours
        if not get_recent_inference_by_type("daily_summary", hours=23):
            rows_24h = _fetch_recent(minutes=1440)
            if len(rows_24h) >= 100:
                _daily_summary(rows_24h)
                _detect_daily_patterns(rows_24h)
                _detect_overnight_trend(rows_24h)
                log.info("Inference engine: generated daily summary from historical data")
    except Exception as e:
        log.error("Startup inference error: %s", e)

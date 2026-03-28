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
    save_inference,
)

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

TVOC_HIGH       = 500   # ppb — WHO "high"
TVOC_MODERATE   = 250   # ppb — WHO "good" ceiling
ECO2_COGNITIVE  = 1000  # ppm — cognitive impairment
ECO2_DANGER     = 2000  # ppm — headaches / drowsiness
TEMP_HIGH       = 28.0  # °C
TEMP_LOW        = 15.0  # °C
HUM_HIGH        = 70.0  # %
HUM_LOW         = 30.0  # %
VPD_LOW         = 0.4   # kPa — mould risk
VPD_HIGH        = 1.6   # kPa — plant stress
SPIKE_FACTOR    = 2.0   # multiplier above rolling mean for spike detection
MIN_READINGS    = 6     # need at least this many points for analysis


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
    if len(rows) < MIN_READINGS:
        return
    tvocs = [r["tvoc"] for r in rows if r["tvoc"] is not None]
    if len(tvocs) < MIN_READINGS:
        return

    baseline = _mean(tvocs[:-3])  # mean excluding last 3
    recent = _mean(tvocs[-3:])
    peak = max(tvocs[-3:])

    if baseline < 50:
        baseline = 50  # floor to avoid false positives on near-zero baselines

    if recent > baseline * SPIKE_FACTOR and peak > TVOC_MODERATE:
        if get_recent_inference_by_type("tvoc_spike", hours=1):
            return
        confidence = min(0.95, 0.5 + (recent / baseline - SPIKE_FACTOR) * 0.15)
        annotation_context = _get_annotation_context(rows[-6:])
        save_inference(
            event_type="tvoc_spike",
            severity="warning" if peak > TVOC_HIGH else "info",
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
            },
            confidence=round(confidence, 2),
            start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
            end_id=rows[-1]["id"],
            annotation=annotation_context,
        )


def _detect_eco2_threshold(rows):
    """Detect eCO₂ crossing cognitive or danger thresholds."""
    if len(rows) < MIN_READINGS:
        return
    eco2s = [r["eco2"] for r in rows if r["eco2"] is not None]
    if len(eco2s) < MIN_READINGS:
        return

    current = eco2s[-1]
    recent_mean = _mean(eco2s[-5:])

    if recent_mean >= ECO2_DANGER:
        etype = "eco2_danger"
        sev = "critical"
        title = f"CO₂ dangerously high — {int(current)} ppm"
        desc = (
            f"eCO₂ has reached {int(current)} ppm (average {int(recent_mean)} ppm "
            f"over last 5 readings). Above {ECO2_DANGER} ppm causes headaches, "
            f"drowsiness, and significant cognitive impairment."
        )
        action = "Ventilate immediately — open windows and doors. Leave the room if symptoms appear."
        confidence = 0.9
    elif recent_mean >= ECO2_COGNITIVE:
        etype = "eco2_elevated"
        sev = "warning"
        title = f"CO₂ elevated — {int(current)} ppm"
        desc = (
            f"eCO₂ has reached {int(current)} ppm. Above {ECO2_COGNITIVE} ppm "
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
    save_inference(
        event_type=etype,
        severity=sev,
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_eco2": f"{int(current)} ppm",
            "5_reading_avg": f"{int(recent_mean)} ppm",
            "threshold": f"{ECO2_DANGER if sev == 'critical' else ECO2_COGNITIVE} ppm",
            "trend": f"{'rising' if _slope(eco2s[-6:]) > 0 else 'stable/falling'}",
        },
        confidence=round(confidence, 2),
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
        annotation=annotation_context,
    )


def _detect_temperature_extreme(rows):
    """Detect temperature outside comfort zone."""
    if len(rows) < MIN_READINGS:
        return
    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    if len(temps) < MIN_READINGS:
        return

    recent_mean = _mean(temps[-5:])
    current = temps[-1]

    if recent_mean > TEMP_HIGH:
        etype = "temp_high"
        sev = "warning"
        title = f"Temperature high — {current:.1f}°C"
        desc = (
            f"Temperature has been averaging {recent_mean:.1f}°C over the last "
            f"5 readings, above the {TEMP_HIGH}°C comfort threshold. This can "
            f"stress plants and reduce cognitive performance."
        )
        action = "Improve ventilation or use cooling. Check if heat sources (lights, equipment) can be reduced."
    elif recent_mean < TEMP_LOW:
        etype = "temp_low"
        sev = "warning"
        title = f"Temperature low — {current:.1f}°C"
        desc = (
            f"Temperature has been averaging {recent_mean:.1f}°C, below the "
            f"{TEMP_LOW}°C threshold. Low temperatures slow plant growth and "
            f"can be uncomfortable for occupants."
        )
        action = "Consider heating the space or reducing ventilation to retain warmth."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    save_inference(
        event_type=etype,
        severity=sev,
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_temp": f"{current:.1f}°C",
            "5_reading_avg": f"{recent_mean:.1f}°C",
            "threshold": f"{TEMP_HIGH if 'high' in etype else TEMP_LOW}°C",
        },
        confidence=0.85,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_humidity_extreme(rows):
    """Detect humidity outside ideal range."""
    if len(rows) < MIN_READINGS:
        return
    hums = [r["humidity"] for r in rows if r["humidity"] is not None]
    if len(hums) < MIN_READINGS:
        return

    recent_mean = _mean(hums[-5:])
    current = hums[-1]

    if recent_mean > HUM_HIGH:
        etype = "humidity_high"
        title = f"Humidity high — {current:.0f}%"
        desc = (
            f"Humidity averaging {recent_mean:.0f}% over last 5 readings. "
            f"Above {HUM_HIGH}% promotes mould growth and dust mites. "
            f"Combined with warm temperatures this creates ideal conditions "
            f"for fungal issues."
        )
        action = "Increase ventilation or use a dehumidifier. Check for water leaks or standing water."
    elif recent_mean < HUM_LOW:
        etype = "humidity_low"
        title = f"Humidity low — {current:.0f}%"
        desc = (
            f"Humidity averaging {recent_mean:.0f}% over last 5 readings. "
            f"Below {HUM_LOW}% causes dry skin, irritated airways, and static. "
            f"Plants may show leaf curling and wilting."
        )
        action = "Use a humidifier, place water trays near heat sources, or mist plants."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    save_inference(
        event_type=etype,
        severity="info",
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_humidity": f"{current:.0f}%",
            "5_reading_avg": f"{recent_mean:.0f}%",
            "threshold": f"{HUM_HIGH if 'high' in etype else HUM_LOW}%",
        },
        confidence=0.8,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
        end_id=rows[-1]["id"],
    )


def _detect_vpd_extreme(rows):
    """Detect VPD outside optimal range for plants."""
    if len(rows) < MIN_READINGS:
        return
    vpds = []
    for r in rows:
        v = _vpd_kpa(r.get("temperature"), r.get("humidity"))
        if v is not None:
            vpds.append(v)
    if len(vpds) < MIN_READINGS:
        return

    recent_mean = _mean(vpds[-5:])
    current = vpds[-1]

    if recent_mean < VPD_LOW:
        etype = "vpd_low"
        title = f"VPD too low — {current:.2f} kPa"
        desc = (
            f"VPD averaging {recent_mean:.2f} kPa. Below {VPD_LOW} kPa the air "
            f"is nearly saturated, slowing transpiration and creating conditions "
            f"for mould, powdery mildew, and root rot."
        )
        action = "Increase temperature or decrease humidity. Improve air circulation around plants."
    elif recent_mean > VPD_HIGH:
        etype = "vpd_high"
        title = f"VPD too high — {current:.2f} kPa"
        desc = (
            f"VPD averaging {recent_mean:.2f} kPa. Above {VPD_HIGH} kPa plants "
            f"close stomata to conserve water, halting photosynthesis and causing "
            f"leaf tip burn and wilting."
        )
        action = "Increase humidity (misting, humidifier) or reduce temperature. Avoid direct heat on plants."
    else:
        return

    if get_recent_inference_by_type(etype, hours=2):
        return

    save_inference(
        event_type=etype,
        severity="info",
        title=title,
        description=desc,
        action=action,
        evidence={
            "current_vpd": f"{current:.2f} kPa",
            "5_reading_avg": f"{recent_mean:.2f} kPa",
            "threshold": f"{VPD_LOW if 'low' in etype else VPD_HIGH} kPa",
        },
        confidence=0.75,
        start_id=rows[-6]["id"] if len(rows) >= 6 else rows[0]["id"],
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
    if tvoc_slope > 5 and eco2_slope > 10 and tvocs[-1] > TVOC_MODERATE and eco2s[-1] > 800:
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

    tvoc_high_count = sum(1 for v in tvocs if v > TVOC_MODERATE)
    eco2_high_count = sum(1 for v in eco2s if v > 800)

    if tvoc_high_count >= 10 or eco2_high_count >= 10:
        if get_recent_inference_by_type("sustained_poor_air", hours=3):
            return
        save_inference(
            event_type="sustained_poor_air",
            severity="warning",
            title="Sustained poor air quality",
            description=(
                f"Air quality has been degraded for an extended period: "
                f"TVOC exceeded {TVOC_MODERATE} ppb in {tvoc_high_count}/{len(tvocs)} "
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
            },
            confidence=0.85,
            start_id=window[0]["id"],
            end_id=window[-1]["id"],
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

        if tvoc > TVOC_MODERATE or eco2 > 800 or temp > TEMP_HIGH or temp < TEMP_LOW:
            conditions = []
            if tvoc > TVOC_MODERATE:
                conditions.append(f"TVOC {tvoc} ppb")
            if eco2 > 800:
                conditions.append(f"eCO₂ {eco2} ppm")
            if temp > TEMP_HIGH:
                conditions.append(f"temp {temp:.1f}°C")
            if temp < TEMP_LOW:
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


# ── Public entry point ────────────────────────────────────────────────────────

def run_analysis():
    """Run all detectors against recent data. Call periodically."""
    try:
        rows = _fetch_recent(minutes=30)
        if len(rows) < MIN_READINGS:
            return

        _detect_tvoc_spike(rows)
        _detect_eco2_threshold(rows)
        _detect_temperature_extreme(rows)
        _detect_humidity_extreme(rows)
        _detect_vpd_extreme(rows)
        _detect_correlated_pollution(rows)
        _detect_rapid_change(rows)
        _detect_sustained_poor_air(rows)
        _detect_annotation_context_event(rows)
    except Exception as e:
        log.error("Inference engine error: %s", e)

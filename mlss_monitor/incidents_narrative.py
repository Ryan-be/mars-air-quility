"""Generate English-prose incident narratives from alert sequences.

Pure functions only — no DB, no Flask. Given a list of alert dicts and the
incident record, return ``{observed, inferred, impact}`` strings suitable for
direct rendering in the UI.

The narrative is deliberately *timestamped* and references specific events
rather than emitting template text like "N event(s) detected".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["build_narrative"]

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

_IMPACT_BY_SEV = {
    "critical": "Immediate attention required — critical air-quality event.",
    "warning":  "Elevated readings — monitor conditions and consider ventilation.",
    "info":     "Informational event — conditions within acceptable range.",
}

# Pearson |r| threshold above which a dep is considered a genuine correlation.
_STRONG_R_THRESHOLD = 0.5
# Short human-friendly names for sensor keys used in alert_signal_deps.
_SENSOR_LABELS = {
    "tvoc_ppb":       "TVOC",
    "eco2_ppm":       "eCO2",
    "temperature_c":  "temperature",
    "humidity_pct":   "humidity",
    "pm1_ug_m3":      "PM1",
    "pm25_ug_m3":     "PM2.5",
    "pm10_ug_m3":     "PM10",
    "co_ppb":         "CO",
    "no2_ppb":        "NO2",
    "nh3_ppb":        "NH3",
}


def _sensor_label(sensor: str) -> str:
    """Map an alert_signal_deps.sensor key to a short human name."""
    return _SENSOR_LABELS.get(sensor, sensor)


def _dominant_sensors(alerts: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Count primary alerts whose strongest signal_dep exceeds the threshold,
    grouped by sensor. Returns [(sensor_key, count), ...] sorted by count desc.

    Note: we count each alert's *contribution* to each sensor that has a
    strong |r|, so an alert with strong correlation to both TVOC and eCO2
    contributes +1 to both. This lets us surface cross-sensor co-movement
    in the follow-up step.
    """
    counts: dict[str, int] = {}
    for a in alerts:
        for d in (a.get("signal_deps") or []):
            r = d.get("r")
            sensor = d.get("sensor")
            if r is None or sensor is None:
                continue
            if abs(r) >= _STRONG_R_THRESHOLD:
                counts[sensor] = counts.get(sensor, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _severity_trajectory(alerts: list[dict[str, Any]]) -> str:
    """Describe whether severity escalated, de-escalated, or stayed flat
    across the chronological sequence. Returns a short English phrase, or
    '' if nothing noteworthy."""
    ordered = sorted(alerts, key=lambda a: a.get("created_at", ""))
    severities = [_SEVERITY_ORDER.get(a.get("severity", "info"), 0) for a in ordered]
    if len(severities) < 2:
        return ""
    # "Strict escalation" — never decreases and first < last.
    if severities[0] < severities[-1] and all(b >= a for a, b in zip(severities, severities[1:])):
        lo = [k for k, v in _SEVERITY_ORDER.items() if v == severities[0]][0]
        hi = [k for k, v in _SEVERITY_ORDER.items() if v == severities[-1]][0]
        return f"Severity escalated from {lo} to {hi}."
    if severities[0] > severities[-1]:
        return "Severity de-escalated during the incident."
    # Mixed / flat
    return ""


def _build_correlation(alerts: list[dict[str, Any]]) -> str:
    """Explain *why* the alerts in an incident appear linked.

    Sources of signal, in order of priority:
      1. A single dominant sensor that correlates strongly with most alerts
         — the likely common trigger.
      2. Two dominant sensors together — a cross-sensor co-movement pattern
         (e.g. TVOC + eCO2 rising together suggests human activity).
      3. Severity trajectory — escalation vs flat vs de-escalation.
      4. Fallback — temporal clustering only, no shared-signal story.
    """
    primary = _primary(alerts) or alerts
    if not primary:
        return ""

    dominants = _dominant_sensors(primary)
    n_primary = len(primary)
    parts: list[str] = []

    if dominants:
        # A sensor qualifies as "dominant" if it shows up in >= 50% of alerts.
        strong = [(s, c) for s, c in dominants if c >= max(2, n_primary // 2)]
        if len(strong) >= 2:
            a_name = _sensor_label(strong[0][0])
            b_name = _sensor_label(strong[1][0])
            parts.append(
                f"{a_name} and {b_name} moved together across the incident "
                f"(strong correlation in {strong[0][1]} and {strong[1][1]} of "
                f"{n_primary} alerts respectively) — a cross-sensor signature "
                f"suggesting a shared cause."
            )
        elif len(strong) == 1:
            s, c = strong[0]
            parts.append(
                f"Events appear linked by {_sensor_label(s)}: "
                f"{c} of {n_primary} alerts show strong correlation (|r| \u2265 0.5) "
                f"with {_sensor_label(s)}."
            )
        else:
            # Correlations exist but no sensor dominates the cluster.
            top = _sensor_label(dominants[0][0])
            parts.append(
                f"No single dominant sensor; strongest shared signal is "
                f"{top} ({dominants[0][1]}/{n_primary} alerts)."
            )
    else:
        parts.append(
            "No strong per-sensor correlations detected. "
            "Alerts are linked only by temporal clustering within the incident window."
        )

    traj = _severity_trajectory(primary)
    if traj:
        parts.append(traj)

    return " ".join(parts)


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("T", " "))
    except ValueError:
        return None


def _fmt_hhmm(s: str) -> str:
    dt = _parse_ts(s)
    return dt.strftime("%H:%M") if dt else ""


def _minutes_between(a: str, b: str) -> int | None:
    da, db = _parse_ts(a), _parse_ts(b)
    if not da or not db:
        return None
    return int((db - da).total_seconds() / 60)


def _primary(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in alerts if a.get("is_primary")]


def build_narrative(
    incident: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> dict[str, str]:
    """Return {observed, inferred, impact, correlation} for the given incident + alerts."""
    if not alerts:
        return {
            "observed": "No events recorded for this incident.",
            "inferred": "",
            "impact": "",
            "correlation": "",
        }

    primary = _primary(alerts) or alerts
    primary = sorted(primary, key=lambda a: a.get("created_at", ""))
    first = primary[0]
    last = primary[-1]

    duration_min = _minutes_between(
        incident.get("started_at", "") or first.get("created_at", ""),
        incident.get("ended_at", "") or last.get("created_at", ""),
    )

    # ── Observed ───────────────────────────────────────────────────────
    start_hhmm = _fmt_hhmm(incident.get("started_at", "") or first.get("created_at", ""))
    if duration_min is None or duration_min <= 0:
        observed = f"Event recorded at {start_hhmm}."
    else:
        observed = (
            f"{len(primary)} correlated event(s) starting {start_hhmm}, "
            f"spanning {duration_min} min."
        )

    # ── Inferred — name the first two events and the gap between them ─
    def _describe(a: dict[str, Any]) -> str:
        return (a.get("title") or a.get("event_type") or "event").strip()

    parts: list[str] = []
    parts.append(f"{_describe(first)} at {_fmt_hhmm(first.get('created_at', ''))}.")
    if len(primary) >= 2:
        gap = _minutes_between(first.get("created_at", ""), primary[1].get("created_at", ""))
        gap_phrase = f"{gap} min later" if gap is not None and gap > 0 else "Concurrently"
        parts.append(f"{gap_phrase}, {_describe(primary[1])}.")
    if len(primary) >= 3:
        tail_count = len(primary) - 2
        parts.append(f"{tail_count} further event(s) through {_fmt_hhmm(last.get('created_at', ''))}.")

    inferred = " ".join(parts)

    # ── Impact — map from max severity ────────────────────────────────
    max_sev = max(
        (a.get("severity", "info") for a in alerts),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
        default="info",
    )
    impact = _IMPACT_BY_SEV.get(max_sev, "")
    correlation = _build_correlation(alerts)

    return {
        "observed": observed,
        "inferred": inferred,
        "impact": impact,
        "correlation": correlation,
    }

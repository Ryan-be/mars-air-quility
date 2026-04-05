"""Narrative engine — pure analysis and text generation functions.

All functions are stateless and have no IO, no database calls, and no Flask
imports. They accept plain Python dicts/lists and return strings or dicts.
This makes them trivially testable and safe to call from any context.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FINGERPRINT_ADVICE: dict[str, str] = {
    "cooking": (
        "Opening a window or running an extractor fan while cooking "
        "would reduce peak readings."
    ),
    "combustion": (
        "Identify and ventilate the source. "
        "Check for open flames or smouldering materials."
    ),
    "biological_offgas": (
        "Increase ventilation. "
        "Check for damp areas, plants, or organic materials."
    ),
    "chemical_offgassing": (
        "Ventilate promptly. "
        "Check for cleaning products, new furniture, or paint."
    ),
    "external_pollution": (
        "Close windows during high external pollution periods. "
        "Check your local air quality index."
    ),
    "personal_care": (
        "Ventilate briefly after use. "
        "Consider fragrance-free or low-VOC alternatives if events are frequent."
    ),
}

_TREND_SENTENCES = {
    "up": "{label} baseline is {pct:.1f}% higher than a week ago",
    "down": "{label} baseline is {pct:.1f}% lower than a week ago",
}

_COLOUR_THRESHOLDS = (10.0, 25.0)  # green ≤ 10%, amber 10–25%, red > 25%

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_utc(ts: str) -> datetime:
    """Parse a UTC ISO 8601 string (with or without Z) to a datetime.

    Handles the non-standard T24:00:00 notation (end-of-day) by converting it
    to the equivalent T00:00:00 on the following day.
    """
    ts = ts.rstrip("Z")
    # Handle T24:00:00 — not valid ISO 8601 but used in some test fixtures
    if "T24:" in ts:
        date_part = ts.split("T")[0]
        base = datetime.fromisoformat(date_part).replace(tzinfo=timezone.utc)
        return base + timedelta(days=1)
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# compute_longest_clean_period
# ---------------------------------------------------------------------------

def compute_longest_clean_period(
    inferences: list[dict],
    window_start: str,
    window_end: str,
) -> dict:
    """Return the longest contiguous gap (no inference events) in the window.

    Returns a dict with keys: hours (float), start (ISO str), end (ISO str).
    If there are no events the entire window is the clean period.
    """
    t_start = _parse_utc(window_start)
    t_end = _parse_utc(window_end)

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not inferences:
        hours = (t_end - t_start).total_seconds() / 3600
        return {"hours": hours, "start": _fmt(t_start), "end": _fmt(t_end)}

    # Sort events by time and build boundary list
    times = sorted(
        _parse_utc(inf["created_at"])
        for inf in inferences
        if inf.get("created_at")
    )

    if not times:
        hours = (t_end - t_start).total_seconds() / 3600
        return {"hours": hours, "start": _fmt(t_start), "end": _fmt(t_end)}

    boundaries = [t_start] + times + [t_end]

    longest_hours = 0.0
    longest_start = t_start
    longest_end = t_end

    for i in range(len(boundaries) - 1):
        gap_start = boundaries[i]
        gap_end = boundaries[i + 1]
        gap_hours = (gap_end - gap_start).total_seconds() / 3600
        if gap_hours > longest_hours:
            longest_hours = gap_hours
            longest_start = gap_start
            longest_end = gap_end

    return {
        "hours": longest_hours,
        "start": _fmt(longest_start),
        "end": _fmt(longest_end),
    }


# ---------------------------------------------------------------------------
# compute_pattern_heatmap
# ---------------------------------------------------------------------------

def compute_pattern_heatmap(inferences: list[dict]) -> dict:
    """Count events per day-of-week × hour-of-day cell.

    Key format: "{day}_{hour}" where day 0=Monday, hour 0–23 (UTC).
    Only cells with at least one event are included (sparse dict).
    """
    counts: dict[str, int] = {}
    for inf in inferences:
        ts = inf.get("created_at")
        if not ts:
            continue
        dt = _parse_utc(ts)
        key = f"{dt.weekday()}_{dt.hour}"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# detect_drift_flags
# ---------------------------------------------------------------------------

def detect_drift_flags(
    baselines_now: dict[str, float | None],
    baselines_7d_ago: dict[str, float | None],
    threshold: float = 0.15,
) -> list[dict]:
    """Flag channels whose EMA baseline has shifted more than `threshold` (15%).

    Returns a list of dicts: {channel, shift_pct, direction, message}.
    Empty list if no drift detected.
    """
    flags = []
    for channel, now in baselines_now.items():
        then = baselines_7d_ago.get(channel)
        if now is None or then is None or then == 0:
            continue
        shift = abs(now - then) / abs(then)
        if shift > threshold:
            direction = "up" if now > then else "down"
            shift_pct = round(shift * 100, 1)
            flags.append({
                "channel": channel,
                "shift_pct": shift_pct,
                "direction": direction,
                "message": (
                    f"{channel} baseline has shifted {shift_pct}% {direction} over 7 days. "
                    "This could mean sensor drift, or a new persistent background source. "
                    "Worth checking."
                ),
            })
    return flags


# ---------------------------------------------------------------------------
# compute_trend_indicators
# ---------------------------------------------------------------------------

def compute_trend_indicators(
    baselines_now: dict[str, float | None],
    baselines_7d_ago: dict[str, float | None],
    channel_meta: dict[str, dict],
) -> list[dict]:
    """Return a trend indicator dict for each channel present in both baselines.

    Skips channels where either baseline is None or zero, or where the channel
    is not in channel_meta.
    Colour: green ≤ 10%, amber 10–25%, red > 25% change.
    """
    indicators = []
    for channel, meta in channel_meta.items():
        now = baselines_now.get(channel)
        then = baselines_7d_ago.get(channel)
        if now is None or then is None or then == 0:
            continue
        pct = abs(now - then) / abs(then) * 100
        direction = "up" if now > then else "down"
        if pct <= _COLOUR_THRESHOLDS[0]:
            colour = "green"
        elif pct <= _COLOUR_THRESHOLDS[1]:
            colour = "amber"
        else:
            colour = "red"
        template = _TREND_SENTENCES[direction]
        base_sentence = template.format(label=meta["label"], pct=pct)
        suffix = " — worth monitoring." if colour == "amber" else (
            " — significant change, investigate." if colour == "red" else "."
        )
        indicators.append({
            "channel": channel,
            "label": meta["label"],
            "unit": meta.get("unit", ""),
            "current_baseline": round(now, 2),
            "week_ago_baseline": round(then, 2),
            "pct_change": round(pct, 1),
            "direction": direction,
            "colour": colour,
            "sentence": base_sentence + suffix,
        })
    return indicators


# ---------------------------------------------------------------------------
# generate_period_summary
# ---------------------------------------------------------------------------

def generate_period_summary(
    inferences: list[dict],
    trend_indicators: list[dict],
    dominant_source: str | None,
) -> str:
    """Generate a 2–3 sentence plain-English summary of the analysis period."""
    n = len(inferences)

    if n == 0:
        intro = "No detection events occurred during this period — air quality was clean throughout."
    elif n == 1:
        intro = "One detection event occurred during this period."
    else:
        alerts = sum(1 for inf in inferences if inf.get("severity") == "critical")
        warnings = sum(1 for inf in inferences if inf.get("severity") == "warning")
        parts = []
        if alerts:
            parts.append(f"{alerts} alert{'s' if alerts > 1 else ''}")
        if warnings:
            parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
        event_desc = " and ".join(parts) if parts else f"{n} events"
        intro = f"{n} detection events occurred, including {event_desc}."

    source_sentence = ""
    if dominant_source:
        if n == 1:
            source_sentence = f" {dominant_source.capitalize()} was the attributed source."
        else:
            source_sentence = f" {dominant_source.capitalize()} was the most commonly attributed source."

    trend_colours = [t.get("colour") for t in trend_indicators]
    if "red" in trend_colours:
        trend_sentence = " Sensor baselines show significant shifts — check the trend indicators below."
    elif "amber" in trend_colours:
        trend_sentence = " Some sensor baselines are drifting — worth monitoring."
    else:
        trend_sentence = " Sensor baselines are stable."

    return intro + source_sentence + trend_sentence


# ---------------------------------------------------------------------------
# generate_fingerprint_narrative
# ---------------------------------------------------------------------------

def generate_fingerprint_narrative(
    source_id: str,
    label: str,
    events: list[dict],
    avg_confidence: float,
    typical_hours: list[int],
) -> str:
    """Generate a 2–3 sentence narrative card for a source fingerprint."""
    if not events:
        return f"No {label} events were detected in this period."

    n = len(events)
    count_str = f"{n} time{'s' if n > 1 else ''}"

    # Confidence characterisation
    if avg_confidence >= 0.80:
        conf_str = "strong confidence"
    elif avg_confidence >= 0.65:
        conf_str = "moderate confidence"
    else:
        conf_str = "lower confidence"

    # Time-of-day summary
    if typical_hours:
        # Group consecutive hours into ranges
        sorted_hours = sorted(set(typical_hours))
        ranges = []
        start = sorted_hours[0]
        prev = sorted_hours[0]
        for h in sorted_hours[1:]:
            if h == prev + 1:
                prev = h
            else:
                ranges.append((start, prev))
                start = prev = h
        ranges.append((start, prev))
        time_parts = [
            f"{s:02d}:00–{e + 1:02d}:00" if s != e else f"{s:02d}:00"
            for s, e in ranges
        ]
        time_str = f"Typically detected around {', '.join(time_parts)}."
    else:
        time_str = ""

    advice = _FINGERPRINT_ADVICE.get(source_id, "")

    sentences = [
        f"{label} was detected {count_str} with {conf_str} (avg {avg_confidence:.0%}).",
    ]
    if time_str:
        sentences.append(time_str)
    if advice:
        sentences.append(advice)

    return " ".join(sentences)


# ---------------------------------------------------------------------------
# generate_anomaly_model_narrative
# ---------------------------------------------------------------------------

def generate_anomaly_model_narrative(
    model_id: str,
    label: str,
    event_count: int,
    description: str,
) -> str:
    """Generate a 2–3 sentence narrative card for a composite multivariate model."""
    count_str = f"{event_count} time{'s' if event_count != 1 else ''}"
    return (
        f"The {label} model flagged {count_str} during this period. "
        f"{description} "
        "Review the detection events below for full details."
    )

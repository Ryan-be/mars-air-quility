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
    """Return {observed, inferred, impact} for the given incident + alerts."""
    if not alerts:
        return {
            "observed": "No events recorded for this incident.",
            "inferred": "",
            "impact": "",
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

    return {"observed": observed, "inferred": inferred, "impact": impact}

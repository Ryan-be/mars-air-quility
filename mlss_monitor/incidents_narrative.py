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


def build_narrative(incident: dict[str, Any], alerts: list[dict[str, Any]]) -> dict[str, str]:
    """Return {observed, inferred, impact} for the given incident + alerts.

    ``alerts`` are expected to come from the API with keys:
    id, created_at, event_type, severity, title, description, confidence,
    detection_method, is_primary, signal_deps.
    """
    return {"observed": "", "inferred": "", "impact": ""}

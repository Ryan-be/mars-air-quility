"""Shared helpers for grow-related API routes.

Currently two things needed in multiple route modules:
  - serialise_validation_errors: turn pydantic v2 ValidationError errors
    into a JSON-serialisable form (ctx.error can be an Exception instance
    that Flask's jsonify can't serialise, so we stringify it).
  - RANGE_TO_HOURS: range-string → hour-cutoff map for /history and
    /photos. Kept in one place because the frontend uses one set of range
    buttons across both panels.

Note: the PIDUpdate `_min_le_max` cross-field validator is duplicated
between contracts/PIDUpdate and api_grow_settings._ProfileUpdate but uses
different field names in each — extracting it cleanly is more invasive
than the duplication is worth right now. Deferred.
"""
from typing import Optional


def serialise_validation_errors(errors: list) -> list:
    """Strip non-JSON-serialisable Exception instances from pydantic errors.

    pydantic v2 puts a raw `ValueError` instance under `ctx.error` when a
    `model_validator` raises — Flask's jsonify can't serialise that.
    Convert it to a string so the client gets a useful detail block
    instead of a 500.
    """
    cleaned = []
    for err in errors:
        item = dict(err)
        ctx = item.get("ctx")
        if isinstance(ctx, dict) and isinstance(ctx.get("error"), Exception):
            item["ctx"] = {**ctx, "error": str(ctx["error"])}
        cleaned.append(item)
    return cleaned


# Range vocabulary shared by GET /api/grow/units/<id>/history and
# GET /api/grow/units/<id>/photos. Keep these in sync — the frontend
# uses one set of range buttons across both panels.
RANGE_TO_HOURS: dict[str, Optional[int]] = {
    "24h": 24,
    "7d": 168,
    "30d": 720,
    "90d": 2160,
    "all": None,
}

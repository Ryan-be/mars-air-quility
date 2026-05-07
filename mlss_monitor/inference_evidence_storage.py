"""Read/write inference evidence with typed columns + extras JSON.

Background: ``inferences.evidence`` was a single JSON-encoded TEXT
column for a heterogeneous dict (different keys per ``event_type``).
Reads consistently looked up the same handful of fields
(``attribution_source``, ``attribution_confidence``, ``runner_up_id``,
``runner_up_confidence``, ``detection_method``); the rest is genuinely
event-specific (``feature_vector``, ``thresholds_used``,
``range_start``/``range_end``, ``baseline_*`` numbers, etc.).

The 5 read-consistently fields have been promoted to typed columns on
``inferences``; everything else lives in a smaller ``evidence_extras``
JSON column. Reads become indexable AND the ~24 callers building
``evidence={...}`` literal dicts in ``inference_engine.py`` and
``detection_engine.py`` don't have to change — :func:`persist_evidence`
splits the dict at write time.

The legacy ``evidence`` TEXT column is *retained* for one release per
``DATABASE.md``'s deprecation policy. :func:`persist_evidence` writes
both representations in lockstep; :func:`load_evidence` prefers the
new columns and only falls back to the legacy JSON for pre-migration
rows. A future commit will drop the legacy column.

See ``docs/JSON_STORAGE_AUDIT.md`` for the broader audit trail.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


# Fields read consistently across call sites — get typed columns.
#
# NB: callers in ``inference_engine.py`` and ``detection_engine.py``
# use both ``runner_up`` (no suffix) and ``runner_up_id`` for the
# same value. :func:`split_evidence` aliases the former to the latter
# so the typed column is always populated regardless of which spelling
# the call site happened to use.
_TYPED_FIELDS: tuple[str, ...] = (
    "attribution_source",       # str   — fingerprint id of best match
    "attribution_confidence",   # float — model confidence 0–1
    "runner_up_id",             # str   — second-best fingerprint (for surfacing)
    "runner_up_confidence",     # float — its confidence
    "detection_method",         # str   — 'ml' | 'statistical' | 'rule'
)


def split_evidence(evidence: dict | None) -> tuple[dict, dict]:
    """Split an evidence dict into (typed_fields, extras).

    ``typed_fields`` keys are exactly the suffixes of ``_TYPED_FIELDS``
    (e.g. ``"attribution_source"``); the corresponding column is
    ``"evidence_" + key``. ``extras`` carries every other key from the
    original dict (the genuinely-heterogeneous per-``event_type`` stuff).

    The ``runner_up`` alias used by some callers is mapped to
    ``runner_up_id`` so the typed column lands populated regardless.

    Returns (``{}``, ``{}``) for a ``None`` or empty input — never
    ``None`` — so callers can unconditionally iterate.
    """
    if not evidence:
        return {}, {}
    typed: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for key, value in evidence.items():
        if key == "runner_up":
            # Alias — collapse to the canonical typed-column key.
            typed["runner_up_id"] = value
        elif key in _TYPED_FIELDS:
            typed[key] = value
        else:
            extras[key] = value
    return typed, extras


def persist_evidence(
    conn: sqlite3.Connection,
    inference_id: int,
    evidence: dict | None,
) -> None:
    """Write the evidence representation for an existing inferences row.

    Updates BOTH:
      * the new typed columns (``evidence_attribution_source`` etc.)
        and the JSON ``evidence_extras`` blob holding leftover keys;
      * the legacy ``evidence`` TEXT column with the full original dict
        as JSON (for the one-release deprecation window).

    Pass ``evidence=None`` to clear all six columns to NULL.

    Caller is responsible for ``conn.commit()``. This mirrors the
    existing pattern in :mod:`mlss_monitor.incident_signature_storage`,
    keeping the write transactional with any surrounding row insert.
    """
    typed, extras = split_evidence(evidence)
    legacy_json = json.dumps(evidence) if evidence else None
    extras_json = json.dumps(extras) if extras else None

    set_clauses = ["evidence=?", "evidence_extras=?"]
    values: list[Any] = [legacy_json, extras_json]
    for field in _TYPED_FIELDS:
        set_clauses.append(f"evidence_{field}=?")
        values.append(typed.get(field))
    values.append(inference_id)

    conn.execute(
        f"UPDATE inferences SET {', '.join(set_clauses)} WHERE id=?",
        values,
    )


def load_evidence(
    conn: sqlite3.Connection,
    inference_id: int,
) -> dict | None:
    """Load evidence for an inference, reconstructing the original dict.

    Resolution order:
      1. If any of the new typed columns are populated OR
         ``evidence_extras`` is non-NULL, build the dict from those
         (the new source of truth).
      2. Otherwise, if the legacy ``evidence`` TEXT column is
         populated (i.e. this is a pre-migration row), JSON-decode
         that and return it. Returns ``None`` rather than raising on
         malformed legacy JSON — readers should treat ``None`` as
         "no comparable evidence".
      3. Otherwise (or if the inference doesn't exist) ``None``.
    """
    cols = ", ".join(f"evidence_{field}" for field in _TYPED_FIELDS)
    row = conn.execute(
        f"SELECT evidence, evidence_extras, {cols} FROM inferences WHERE id=?",
        (inference_id,),
    ).fetchone()
    if row is None:
        return None
    return rebuild_evidence_from_row(
        legacy_json=row[0], extras_json=row[1], typed_values=row[2:],
    )


def rebuild_evidence_from_row(
    *,
    legacy_json: str | None,
    extras_json: str | None,
    typed_values: tuple | list,
) -> dict | None:
    """Reconstruct an evidence dict from already-fetched column values.

    Use this when the calling code already has all six values in hand
    (e.g. from a ``SELECT *`` already issued for unrelated reasons) so
    you don't pay for a second SQLite roundtrip just to read evidence.

    ``typed_values`` must be a sequence of length ``len(_TYPED_FIELDS)``
    in the same order as ``_TYPED_FIELDS``.

    Same fallback semantics as :func:`load_evidence`.
    """
    # Pre-migration row: only the legacy column has anything to offer.
    if all(v is None for v in typed_values) and not extras_json:
        if not legacy_json:
            return None
        try:
            decoded = json.loads(legacy_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(decoded, dict):
            return None
        return decoded

    # New representation wins. Re-merge typed columns on top of extras
    # so the original dict shape is reconstructed for legacy callers.
    out: dict[str, Any] = {}
    if extras_json:
        try:
            extras = json.loads(extras_json)
            if isinstance(extras, dict):
                out.update(extras)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for field, value in zip(_TYPED_FIELDS, typed_values):
        if value is not None:
            out[field] = value
    return out or None


# Public re-export of the typed-field column names so callers building
# row dicts (e.g. db_logger.get_inferences) know which columns to pop.
TYPED_FIELDS: tuple[str, ...] = _TYPED_FIELDS

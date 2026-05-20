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

The legacy ``evidence`` TEXT column has been dropped — the typed
columns + ``evidence_extras`` blob are now the single source of truth.
The historic-data back-fill happened in commit ``d0a1d07``; this
commit completes the deprecation cycle started in commit ``85ce40e``.

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

    Updates the typed columns (``evidence_attribution_source`` etc.)
    and the JSON ``evidence_extras`` blob holding leftover keys.

    Pass ``evidence=None`` to clear all six columns to NULL.

    Caller is responsible for ``conn.commit()``. This mirrors the
    existing pattern in :mod:`mlss_monitor.incident_signature_storage`,
    keeping the write transactional with any surrounding row insert.

    Outbox: enqueues a row pointer for the updated inferences row inside
    the caller's transaction. The outbox table coalesces on
    (table_name, pk) so callers that wrap this in their own
    ``@tee_to_outbox``-decorated helper (e.g. :func:`save_inference`'s
    ``_save_inference_to_db``) get the INSERT + UPDATE collapsed into a
    single pointer, which is the right behaviour — the shipper reads
    current state at ship-time.
    """
    typed, extras = split_evidence(evidence)
    extras_json = json.dumps(extras) if extras else None

    set_clauses = ["evidence_extras=?"]
    values: list[Any] = [extras_json]
    for field in _TYPED_FIELDS:
        set_clauses.append(f"evidence_{field}=?")
        values.append(typed.get(field))
    values.append(inference_id)

    conn.execute(
        f"UPDATE inferences SET {', '.join(set_clauses)} WHERE id=?",
        values,
    )
    # Mirror the live UPDATE to the outbox inside the same transaction.
    # Local import keeps the dependency direction clean: outbox is part
    # of mlss_monitor.backup, and this module already lives under
    # mlss_monitor — no new top-level cycle introduced.
    from mlss_monitor.backup import outbox  # pylint: disable=import-outside-toplevel
    outbox.enqueue_row(conn, table="inferences", pk=inference_id)


def load_evidence(
    conn: sqlite3.Connection,
    inference_id: int,
) -> dict | None:
    """Load evidence for an inference, reconstructing the original dict.

    Builds the dict from the typed columns + ``evidence_extras`` JSON.
    Returns ``None`` if the inference doesn't exist OR if every
    evidence-related column is NULL (no evidence to report).
    """
    cols = ", ".join(f"evidence_{field}" for field in _TYPED_FIELDS)
    row = conn.execute(
        f"SELECT evidence_extras, {cols} FROM inferences WHERE id=?",
        (inference_id,),
    ).fetchone()
    if row is None:
        return None
    return rebuild_evidence_from_row(
        extras_json=row[0], typed_values=row[1:],
    )


def rebuild_evidence_from_row(
    *,
    extras_json: str | None,
    typed_values: tuple | list,
) -> dict | None:
    """Reconstruct an evidence dict from already-fetched column values.

    Use this when the calling code already has all six values in hand
    (e.g. from a ``SELECT *`` already issued for unrelated reasons) so
    you don't pay for a second SQLite roundtrip just to read evidence.

    ``typed_values`` must be a sequence of length ``len(_TYPED_FIELDS)``
    in the same order as ``_TYPED_FIELDS``.

    Returns ``None`` if every value is NULL.
    """
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

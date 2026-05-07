"""Read/write incident similarity-feature vectors.

Background: ``incidents.signature`` was a JSON-encoded TEXT column
holding the output of
:func:`mlss_monitor.incident_grouper.build_incident_similarity_vector`
(a fixed 32-element ``list[float]`` per the docstring at
``incident_grouper.py:283-291``). The blob was used purely for cosine
similarity scans in ``api_incidents._find_similar``.

It has been promoted to a typed sub-table
``incident_signature_features (incident_id, feature_idx, value)`` so
the per-feature values are real ``REAL`` floats, indexable, and
queryable per-feature (e.g. "incidents whose pm_density bucket is
extreme") — see ``docs/JSON_STORAGE_AUDIT.md`` for rationale.

The legacy ``incidents.signature`` TEXT column is *retained* for one
release per ``DATABASE.md``'s deprecation policy. ``save_signature``
writes both, ``load_signature`` prefers the sub-table and only falls
back to the JSON column for incidents created before the migration.
A future commit will drop the column.
"""
from __future__ import annotations

import json
import sqlite3


def save_signature(
    conn: sqlite3.Connection,
    incident_id: str,
    vector: list[float],
) -> None:
    """Replace any existing signature rows for ``incident_id`` with
    ``vector``, indexed by position.

    Also writes the JSON-stringified vector into the legacy
    ``incidents.signature`` column for backward compatibility — a
    follow-up release will remove this once all live deployments have
    cycled through at least one regroup_all() run on the new schema.

    Caller is responsible for ``conn.commit()``. This mirrors the
    existing pattern used by ``incident_grouper.regroup_all`` which
    batches an entire regroup into a single transaction.
    """
    conn.execute(
        "DELETE FROM incident_signature_features WHERE incident_id=?",
        (incident_id,),
    )
    if vector:
        conn.executemany(
            "INSERT INTO incident_signature_features "
            "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
            [(incident_id, idx, float(value))
             for idx, value in enumerate(vector)],
        )
    # Legacy column — kept for one release. Always rewritten so the
    # legacy + new representations stay consistent during the
    # deprecation window.
    conn.execute(
        "UPDATE incidents SET signature=? WHERE id=?",
        (json.dumps(list(vector)), incident_id),
    )


def load_signature(
    conn: sqlite3.Connection,
    incident_id: str,
) -> list[float]:
    """Load the signature vector for ``incident_id``.

    Tries the new typed sub-table first; falls back to the legacy
    JSON-stringified ``incidents.signature`` column if the sub-table
    has no rows for this incident (i.e. it was created before the
    promotion migration ran). Returns ``[]`` if neither holds data,
    or if the legacy JSON is malformed — readers should treat ``[]``
    as "no comparable signature available" (cosine similarity over
    a zero-length vector returns 0.0 by convention in
    :func:`incident_grouper.cosine_similarity`).
    """
    rows = conn.execute(
        "SELECT value FROM incident_signature_features "
        "WHERE incident_id=? ORDER BY feature_idx",
        (incident_id,),
    ).fetchall()
    if rows:
        return [r[0] for r in rows]

    # Fallback: read legacy JSON column for pre-migration incidents.
    row = conn.execute(
        "SELECT signature FROM incidents WHERE id=?",
        (incident_id,),
    ).fetchone()
    if row is None or not row[0]:
        return []
    try:
        decoded = json.loads(row[0])
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [float(v) for v in decoded]

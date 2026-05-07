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

The legacy ``incidents.signature`` TEXT column has been dropped — the
sub-table is now the single source of truth. The historic-data
back-fill happened in commit ``d0a1d07``; this commit completes the
deprecation cycle started in commit ``9c745fe``.
"""
from __future__ import annotations

import sqlite3


def save_signature(
    conn: sqlite3.Connection,
    incident_id: str,
    vector: list[float],
) -> None:
    """Replace any existing signature rows for ``incident_id`` with
    ``vector``, indexed by position.

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


def load_signature(
    conn: sqlite3.Connection,
    incident_id: str,
) -> list[float]:
    """Load the signature vector for ``incident_id``.

    Returns ``[]`` if the incident has no signature rows — readers
    should treat ``[]`` as "no comparable signature available" (cosine
    similarity over a zero-length vector returns 0.0 by convention in
    :func:`incident_grouper.cosine_similarity`).
    """
    rows = conn.execute(
        "SELECT value FROM incident_signature_features "
        "WHERE incident_id=? ORDER BY feature_idx",
        (incident_id,),
    ).fetchall()
    return [r[0] for r in rows]

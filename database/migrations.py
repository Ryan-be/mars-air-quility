"""One-shot historic-data migrations for the typed-column promotions.

These run on startup (after init_db.create_db) and back-fill the
typed columns for rows that pre-date the schema migration. Each is
idempotent — re-running is safe (it only touches rows where the
typed cols are still NULL but the legacy JSON column has data).

Background: two recent commits promoted JSON-in-TEXT columns to
typed storage with a lazy-migration pattern (new writes go to typed
cols + legacy column; reads prefer typed cols and fall back to legacy):

  * ``incidents.signature`` (JSON) → ``incident_signature_features``
    sub-table (Commit ``9c745fe``)
  * ``inferences.evidence`` (JSON) → ``evidence_attribution_*``,
    ``evidence_runner_up_*``, ``evidence_detection_method``,
    ``evidence_extras`` columns (Commit ``85ce40e``)

Pre-existing rows have NULL in the typed cols + JSON in the legacy
col. Reads work transparently via the fallback path, but historic
rows can't be queried by the typed cols and we can't drop the legacy
column until they're migrated. This module performs the back-fill.

After confirming all rows are migrated, the legacy columns can be
dropped (planned next commit).
"""
from __future__ import annotations

import json
import logging
import sqlite3

log = logging.getLogger(__name__)


def migrate_incident_signatures(conn: sqlite3.Connection) -> int:
    """Back-fill incident_signature_features for rows that still have
    only the legacy incidents.signature JSON populated.

    Returns count of incidents migrated. Idempotent — only touches
    incidents whose signature column is non-empty AND who have zero
    rows in incident_signature_features.

    Robust to corrupt legacy data: invalid JSON, wrong type, or
    non-numeric values → log + skip the offending incident, never
    raise. A handful of corrupt historic rows shouldn't block startup.
    """
    rows = conn.execute(
        "SELECT i.id, i.signature FROM incidents i "
        "WHERE i.signature IS NOT NULL "
        "AND i.signature != '' "
        "AND i.signature != '[]' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM incident_signature_features s "
        "  WHERE s.incident_id = i.id"
        ")"
    ).fetchall()

    migrated = 0
    for row in rows:
        incident_id, signature_json = row
        try:
            vector = json.loads(signature_json)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning(
                "failed to parse signature for incident %s: %s",
                incident_id, exc,
            )
            continue
        if not isinstance(vector, list):
            log.warning(
                "incident %s signature is not a list (%s); skipping",
                incident_id, type(vector).__name__,
            )
            continue
        try:
            for idx, value in enumerate(vector):
                conn.execute(
                    "INSERT INTO incident_signature_features "
                    "(incident_id, feature_idx, value) VALUES (?, ?, ?)",
                    (incident_id, idx, float(value)),
                )
        except (TypeError, ValueError) as exc:
            log.warning(
                "incident %s signature has non-numeric value: %s",
                incident_id, exc,
            )
            # Roll back partial inserts for this incident so the
            # idempotency invariant (sub-table empty for this id ⇔
            # row needs migration) holds on retry.
            conn.execute(
                "DELETE FROM incident_signature_features "
                "WHERE incident_id=?",
                (incident_id,),
            )
            continue
        migrated += 1

    if migrated > 0:
        conn.commit()
        log.info(
            "migrated %d incident signatures from legacy JSON to typed sub-table",
            migrated,
        )
    return migrated


def migrate_inference_evidence(conn: sqlite3.Connection) -> int:
    """Back-fill the typed evidence_* columns + evidence_extras for
    inferences rows that still have only the legacy evidence JSON
    populated.

    Returns count of inferences migrated. Idempotent — only touches
    rows where evidence_extras IS NULL AND all 5 typed cols are NULL
    AND the legacy evidence column is non-empty.

    Robust to corrupt legacy data: invalid JSON or wrong type → log
    + skip, never raise.
    """
    # Imported locally to avoid a circular import at module-load time
    # (database.migrations is imported from database.init_db, and
    # mlss_monitor in turn may import database.* helpers).
    from mlss_monitor.inference_evidence_storage import split_evidence

    rows = conn.execute(
        "SELECT id, evidence FROM inferences "
        "WHERE evidence IS NOT NULL "
        "AND evidence != '' "
        "AND evidence_extras IS NULL "
        "AND evidence_attribution_source IS NULL "
        "AND evidence_attribution_confidence IS NULL "
        "AND evidence_runner_up_id IS NULL "
        "AND evidence_runner_up_confidence IS NULL "
        "AND evidence_detection_method IS NULL"
    ).fetchall()

    migrated = 0
    for row in rows:
        inference_id, evidence_json = row
        try:
            evidence = json.loads(evidence_json)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning(
                "failed to parse evidence for inference %s: %s",
                inference_id, exc,
            )
            continue
        if not isinstance(evidence, dict):
            log.warning(
                "inference %s evidence is not a dict (%s); skipping",
                inference_id, type(evidence).__name__,
            )
            continue
        try:
            typed, extras = split_evidence(evidence)
            extras_json = json.dumps(extras) if extras else None
            conn.execute(
                "UPDATE inferences SET "
                "evidence_attribution_source=?, "
                "evidence_attribution_confidence=?, "
                "evidence_runner_up_id=?, "
                "evidence_runner_up_confidence=?, "
                "evidence_detection_method=?, "
                "evidence_extras=? "
                "WHERE id=?",
                (
                    typed.get("attribution_source"),
                    typed.get("attribution_confidence"),
                    typed.get("runner_up_id"),
                    typed.get("runner_up_confidence"),
                    typed.get("detection_method"),
                    extras_json,
                    inference_id,
                ),
            )
            migrated += 1
        except (TypeError, ValueError) as exc:
            log.warning(
                "failed to migrate evidence for inference %s: %s",
                inference_id, exc,
            )
            continue

    if migrated > 0:
        conn.commit()
        log.info(
            "migrated %d inference evidence rows from legacy JSON to typed cols",
            migrated,
        )
    return migrated


def run_all_migrations(db_path: str) -> dict[str, int]:
    """Run all pending data migrations. Called from create_db on startup.

    Returns ``{migration_name: count}`` so the caller can log a summary.

    The empty case is fast: each migration's WHERE clause filters out
    already-migrated rows via existing indexes / IS NULL predicates,
    so on a fresh or already-migrated DB this is a single index lookup
    per migration that returns zero rows immediately.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        return {
            "incident_signatures": migrate_incident_signatures(conn),
            "inference_evidence": migrate_inference_evidence(conn),
        }
    finally:
        conn.close()

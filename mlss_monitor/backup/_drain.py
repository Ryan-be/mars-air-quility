"""Drain functions: read outbox entries, ship to Postgres / S3.

Stateless. Each public function takes a SQLite connection + a client
and returns ``True`` if any work was shipped this batch (False if the
queue was empty). Errors propagate — the caller (the run loop in
``mlss_monitor/backup/worker.py``) catches them and transitions the
worker to BACKOFF.

Module-private (leading underscore) — only the BackupWorker imports
from here. Splitting these out of ``worker.py`` keeps the worker's
state-machine + lifecycle code readable on its own; a reader debugging
"why didn't this row ship?" can grep this module without scrolling
past 600 LOC of thread orchestration.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

from mlss_monitor.backup import outbox
from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES, parse_pk

log = logging.getLogger(__name__)


def _read_live_row(
    conn: sqlite3.Connection,
    table: str,
    pk_columns: list[str],
    pk_values: tuple,
) -> dict | None:
    """SELECT * FROM {table} WHERE pk match. Returns a dict of
    column→value, or None if the row no longer exists.

    None means the row was deleted between enqueue and ship; the drain
    loop logs it and drops the outbox entry without shipping. The PG
    side keeps its previously-shipped copy (append-mostly delete
    semantics — operator-cleared rows on the Pi do NOT propagate)."""
    conn.row_factory = sqlite3.Row
    where = " AND ".join(f"{c} = ?" for c in pk_columns)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {where}",
        pk_values,
    ).fetchone()
    return dict(row) if row else None


# ── Files pipeline: target_key → bucket suffix ─────────────────────

def _bucket_suffix_for_key(target_key: str) -> str:
    """Derive the S3 bucket suffix from the target_key prefix.

    target_key shapes (from Phase 2 file pipeline writers):
      - 'unit_NNN/YYYY-MM-DD/...jpg'         → 'photos'
      - 'anomaly/<channel>/<iso>.pkl'        → 'anomaly'
      - 'multivar_anomaly/<model>/<iso>.pkl' → 'multivar-anomaly'
      - 'attribution/classifier/<iso>.pkl'   → 'attribution'

    Note the underscore→hyphen normalisation for ``multivar_anomaly``:
    S3 bucket naming uses hyphens by convention (DNS-compatible names),
    but the on-disk filesystem layout uses underscores to match the
    Python module names (``mlss_monitor.multivar_anomaly``). Don't
    "fix" this — both forms are intentional.

    Raises ``ValueError`` on unknown prefix. The drain loop catches
    this and logs + drops the outbox entry so a schema-drift prefix
    can't permanently block the queue.

    Order note: ``multivar_anomaly/`` check goes before ``anomaly/``
    for clarity, though prefix matching wouldn't actually collide
    ("multivar_anomaly" does not start with "anomaly").
    """
    if target_key.startswith("unit_"):
        return "photos"
    if target_key.startswith("multivar_anomaly/"):
        return "multivar-anomaly"
    if target_key.startswith("anomaly/"):
        return "anomaly"
    if target_key.startswith("attribution/"):
        return "attribution"
    raise ValueError(f"Cannot derive S3 bucket for target_key {target_key!r}")


# ── DB pipeline drain helpers ──────────────────────────────────────

def _ship_delete_scope_batch(
    sqlite_conn: sqlite3.Connection,
    pg_client,
) -> bool:
    """Ship one peek's worth of delete_scope entries to Postgres.

    Per-entry ship + delete + commit (rather than ship-all then
    delete-all) so a mid-batch Postgres failure on the third entry
    doesn't waste the first two ships — they're already removed
    from the outbox by the time we attempt the third.

    The explicit ``sqlite_conn.commit()`` after each delete is
    load-bearing: the run loop opens the connection with
    ``contextlib.closing`` which does NOT auto-commit on exit.
    Without it, a successful drain whose connection is then closed
    would silently lose every outbox delete and re-ship the same
    entries forever.

    Errors from ``pg_client.delete_scope`` propagate. The run
    loop catches them and transitions to BACKOFF. The failing
    entry stays in the outbox for retry on the next drain cycle.

    Returns True if any entries were processed (shipped + deleted),
    False if the queue was empty.
    """
    entries = outbox.peek_delete_scope(sqlite_conn, limit=100)
    if not entries:
        return False
    for entry in entries:
        scope = json.loads(entry["scope_json"])
        pg_client.delete_scope(
            table=entry["table_name"], scope=scope,
        )
        # Delete THIS entry's outbox row only after its ship
        # succeeded. Per-iteration commit boundary so a later
        # failure doesn't re-ship earlier entries.
        outbox.delete_delete_scope(sqlite_conn, ids=[entry["id"]])
        sqlite_conn.commit()
    return True


def _ship_row_batch(
    sqlite_conn: sqlite3.Connection,
    pg_client,
) -> bool:
    """Ship one peek's worth of row pointers to Postgres.

    Per-table ship + delete (rather than ship-all-tables then
    delete-all) so a mid-batch Postgres failure on table B doesn't
    waste outbox entries for already-shipped table A. Re-shipping
    rows IS idempotent on the server (``ON CONFLICT UPDATE``) but
    wasteful — we'd repeat the upsert next cycle for no benefit.

    Edge cases handled BEFORE any Postgres call (so a Postgres
    outage doesn't delay these cheap drops):

    - Unknown table in outbox (schema drift between the lint
      allowlist and ``REPLICATED_TABLES``): log + drop without
      shipping so the queue doesn't permanently block.

    - Missing live row (deleted between enqueue and ship): log +
      drop without shipping. Normal for append-mostly tables —
      operator cleared the Pi-side row but the server keeps its
      previously-shipped copy.

    For each ship-needed table:
    - ``pg_client.upsert_rows`` errors propagate. The failing
      table's outbox entries stay in place for retry; earlier
      tables in the same batch that shipped successfully have
      already been removed (per-table commit boundary).

    Returns True if any entries were processed (shipped, dropped,
    or both), False if the queue was empty.
    """
    entries = outbox.peek_rows(sqlite_conn, limit=1000)
    if not entries:
        return False

    # Triage entries into ship-needed (grouped by table) vs
    # drop-immediately (unknown table or missing live row).
    by_table: dict[str, list[tuple[int, dict]]] = {}
    drop_immediately_ids: list[int] = []
    for entry in entries:
        table = entry["table_name"]
        schema = REPLICATED_TABLES.get(table)
        if schema is None:
            # Schema drift — unknown table. Log + orphan the
            # entry so a future schema change doesn't permanently
            # block the queue. (If this fires in production, the
            # lint allowlist or REPLICATED_TABLES is out of date.)
            log.warning(
                "backup db: unknown replicated table %r — "
                "dropping outbox entry id=%s",
                table, entry["id"],
            )
            drop_immediately_ids.append(entry["id"])
            continue
        pk_values = parse_pk(entry["pk"], schema["pk_types"])
        live_row = _read_live_row(
            sqlite_conn, table, schema["pk_columns"], pk_values,
        )
        if live_row is None:
            # Row was deleted between enqueue and ship. Drop the
            # outbox entry; the server keeps its previously-shipped
            # copy (append-mostly delete doesn't propagate).
            log.info(
                "backup db: live row %r:%r missing — "
                "dropping outbox entry id=%s",
                table, entry["pk"], entry["id"],
            )
            drop_immediately_ids.append(entry["id"])
            continue
        by_table.setdefault(table, []).append((entry["id"], live_row))

    # Drop unknown/missing entries first — no Postgres calls
    # needed, so this happens even if every table below errors.
    # Commit immediately so a later failure doesn't waste the
    # drop work either.
    if drop_immediately_ids:
        outbox.delete_rows(sqlite_conn, ids=drop_immediately_ids)
        sqlite_conn.commit()

    # Ship each table separately. A failure on table B leaves
    # table A's outbox entries gone (shipped + committed) and
    # table B's entries in place for retry — instead of the old
    # behaviour where ALL entries were retained on any failure
    # and re-shipped.
    #
    # The explicit per-table ``commit()`` is load-bearing: the
    # run loop opens the connection with ``contextlib.closing``
    # which does NOT auto-commit on exit. Without it, a
    # successful drain would silently lose every outbox delete
    # and re-ship the same rows on the next cycle.
    for table, items in by_table.items():
        schema = REPLICATED_TABLES[table]
        ids = [i for i, _ in items]
        rows = [r for _, r in items]
        pg_client.upsert_rows(
            table=table,
            pk_columns=schema["pk_columns"],
            rows=rows,
        )
        outbox.delete_rows(sqlite_conn, ids=ids)
        sqlite_conn.commit()

    return True


# ── Public entry points ────────────────────────────────────────────

def drain_db_batch(
    sqlite_conn: sqlite3.Connection,
    pg_client,
) -> bool:
    """Drain one batch of outbox entries to Postgres.

    Dispatches to two helpers in strict order:

      1. ``_ship_delete_scope_batch`` FIRST — strict-mirror wipes
         must land on the server BEFORE the corresponding INSERTs
         so a DELETE+INSERT replace arrives atomically. If we
         shipped INSERTs first, the server would briefly have old
         + new versions overlapping; a mid-batch crash would leave
         stale rows behind.

      2. ``_ship_row_batch`` second — per-table upsert+delete so
         a mid-batch Postgres failure on table B doesn't waste
         outbox entries for already-shipped table A.

    Returns True if any work was shipped (rows or scopes, drops or
    ships), False if both queues were empty. The run loop uses
    False to flip the worker state back to IDLE.

    Failure semantics are per-helper — see ``_ship_row_batch`` for
    the per-table commit boundary that distinguishes this from the
    original "ship everything then delete everything" shape.
    """
    scopes_shipped = _ship_delete_scope_batch(sqlite_conn, pg_client)
    rows_shipped = _ship_row_batch(sqlite_conn, pg_client)
    return scopes_shipped or rows_shipped


def drain_files_batch(
    sqlite_conn: sqlite3.Connection,
    s3_client,
) -> bool:
    """Drain one batch of outbox_blobs entries to S3.

    Per-entry flow:

      1. If ``source_path`` no longer exists on disk: log + drop
         the outbox entry without any network round-trip. Normal
         for append-mostly artefacts where the operator cleared
         the Pi-side file (e.g. clear_photos route unlinks JPEGs)
         but the server keeps its previously-shipped copy.

      2. Derive the S3 bucket suffix from ``target_key`` via
         ``_bucket_suffix_for_key``. Unknown prefix → log + drop
         so schema drift doesn't permanently block the queue.

      3. HEAD-check the destination. If the object is already
         there (idempotency — previous ship succeeded but the
         worker crashed before the outbox delete committed),
         skip the upload but still drop the outbox entry.

      4. PUT if missing.

      5. Drop the outbox entry after a successful ship (or skip).

    Returns True if any entries were processed (shipped, skipped,
    or dropped), False if the outbox was empty. The Task 15 run
    loop uses False to flip the worker state back to IDLE.

    Batch size respects ``outbox.peek_blobs`` default (10) —
    blobs are multi-MB uploads, so we ship slower than the DB
    pipeline by design.

    Errors from ``s3_client.head`` or ``s3_client.put`` propagate.
    The run loop catches them and transitions to BACKOFF via
    ``_on_ship_failed``. The failing entry stays in the outbox for
    retry; earlier entries in the same batch that already shipped
    successfully are not rolled back (their outbox entries were
    already deleted + committed per-iteration).

    The explicit per-entry ``sqlite_conn.commit()`` is load-bearing:
    the run loop opens the connection with ``contextlib.closing``
    which does NOT auto-commit on exit. Without it, a successful
    drain whose connection is then closed would silently lose every
    outbox delete and re-ship the same blobs forever.

    Connection + client are passed in so this is unit-testable in
    isolation; the run loop (Task 15) will own the live SQLite
    connection and the S3Client instance and pass them in.
    """
    entries = outbox.peek_blobs(sqlite_conn, limit=10)
    if not entries:
        return False

    for entry in entries:
        # Cheap dead-source check first — saves a network round-trip
        # if the operator cleared the file between enqueue and ship.
        if not os.path.exists(entry["source_path"]):
            log.info(
                "backup files: source %r missing — "
                "dropping outbox entry id=%s",
                entry["source_path"], entry["id"],
            )
            outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
            sqlite_conn.commit()
            continue

        # Route to the right bucket. Unknown prefix is schema drift
        # — log + drop so the queue isn't permanently blocked.
        try:
            bucket_suffix = _bucket_suffix_for_key(entry["target_key"])
        except ValueError as exc:
            log.warning(
                "backup files: %s — dropping outbox entry id=%s",
                exc, entry["id"],
            )
            outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
            sqlite_conn.commit()
            continue

        # Idempotency: if the blob is already on S3 (crash-resume
        # mid-batch), skip the upload but still drop the outbox
        # entry. HEAD errors (auth, network) propagate — caller
        # decides retry strategy.
        if s3_client.head(
            bucket_suffix=bucket_suffix, key=entry["target_key"],
        ):
            outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
            sqlite_conn.commit()
            continue

        # Ship. Errors propagate — outbox entry stays in place for
        # retry on the next drain cycle.
        s3_client.put(
            bucket_suffix=bucket_suffix,
            key=entry["target_key"],
            source_path=entry["source_path"],
            sha256=entry["sha256"],
        )
        outbox.delete_blobs(sqlite_conn, ids=[entry["id"]])
        sqlite_conn.commit()

    return True

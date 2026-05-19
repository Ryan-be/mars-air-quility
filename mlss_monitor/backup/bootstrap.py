"""Bootstrap scanner — backfill historical data into the outbox.

Phase 5 Task 18. The live @tee_to_outbox decorator only enqueues rows
written AFTER it was wired in. ``BootstrapScanner`` walks the
pre-existing data (rows in replicated tables, files in photo / model
trees) and enqueues each one so the operator's "enable backups"
button retroactively ships everything to the backup server, not just
new rows going forward.

Two pipelines, both resumable via ``bootstrap_progress``:

  - ``start_db_bootstrap`` iterates every table in
    ``REPLICATED_TABLES`` (single source of truth — see
    ``replicated_tables.py``), walks rows by ROWID, and enqueues each
    via ``outbox.enqueue_row``. The pk string format matches the live
    writers (``str(pk)`` for single-PK, ``f"{a}:{b}"`` for composite)
    so ``replicated_tables.parse_pk`` reads them back the same way.

  - ``start_files_bootstrap`` walks each ``(kind, root)`` pair
    recursively with ``Path.rglob``, hashes the bytes, and enqueues
    via ``outbox.enqueue_blob``. ``target_key`` is the path relative
    to the root (e.g. ``unit_001/2026-05-18/120000.jpg`` for a
    ``data/grow_images`` root) — matches the shape used by
    ``photo_storage.handle_photo_frame`` so a re-scan after the live
    writers have shipped is coalesced by the outbox's UNIQUE
    constraint.

Both pipelines:

  - Skip scopes already marked ``completed_at IS NOT NULL`` (re-running
    is a no-op once a table or root has been fully scanned).
  - Persist progress per-batch / per-file so a crash mid-scan
    resumes from ``last_pk`` on the next call rather than re-hashing
    the world.

``reset(pipeline, scope=None)`` clears progress so a Force-re-bootstrap
admin action can restart from zero. Used by the Phase 6 admin
endpoint.

Threading: this class is NOT a worker. Phase 6 / 8 spawn a thread
that calls these methods; the class itself is just the algorithm. The
methods are safe to call concurrently with the live writers because
SQLite WAL handles concurrent readers/writers, and the outbox UNIQUE
constraints coalesce double-enqueues if a live writer and bootstrap
happen to enqueue the same row at the same instant.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
Plan: docs/superpowers/plans/2026-05-18-mlss-backup.md (Phase 5 Task 18)
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from mlss_monitor.backup import outbox
from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _format_pk_for_outbox(row: sqlite3.Row, pk_columns: list[str]) -> str:
    """Format a row's PK as the outbox stores it.

    Mirrors how the live writers call ``outbox.enqueue_row``:

      - Single-PK rows: just the value (``str()``-coerced by enqueue_row
        internally — we pass through whatever the SELECT returned).
      - Composite-PK rows: ``f"{a}:{b}"`` — the same `':'.join` shape
        used by ``grow/handlers.py`` and ``incident_grouper.py``. The
        ``replicated_tables.parse_pk`` rsplit-from-right uses the rightmost
        colon as the delimiter so a TEXT first column containing colons
        (e.g. an ISO 8601 ``incident_id``) round-trips intact.

    The caller passes the row + the canonical ``pk_columns`` from
    ``REPLICATED_TABLES`` so this helper doesn't need to know the
    schema itself.
    """
    if len(pk_columns) == 1:
        return str(row[pk_columns[0]])
    return ":".join(str(row[col]) for col in pk_columns)


class BootstrapScanner:
    """Walk historical replicated-table rows + filesystem trees and
    enqueue each one into the outbox.

    Construct once per app start; methods are idempotent + resumable
    so a crash mid-scan picks up from the last persisted cursor on
    next call.
    """

    def __init__(self, db_file: str) -> None:
        """``db_file`` is the SQLite path holding the live tables AND
        the outbox + bootstrap_progress tables (they're co-located by
        design so writes share a transaction).
        """
        self.db_file = db_file

    # -- DB pipeline ---------------------------------------------------

    def start_db_bootstrap(self) -> None:
        """Walk every table in ``REPLICATED_TABLES`` and enqueue each
        row's PK into ``outbox_changes``.

        Resumable: progress persisted to ``bootstrap_progress`` per
        table. A crash partway through table 5 of 18 doesn't restart
        from table 1 — the next call picks up from the row after
        ``last_pk`` for table 5 and then continues with table 6.

        Idempotent on full re-run: tables already marked
        ``completed_at IS NOT NULL`` are skipped.
        """
        for table in REPLICATED_TABLES:
            self._scan_table(table)

    def _scan_table(self, table: str, batch_size: int = 1000) -> None:
        """Walk one replicated table's rows in ROWID order, batching
        the SELECT + enqueues + cursor advance into one transaction
        per batch.

        Why ROWID rather than PK column ordering: ROWID is monotonic
        and present on every table in our schema (none declared
        ``WITHOUT ROWID``), so the cursor logic stays uniform for
        composite-PK tables. The actual enqueued PK string still uses
        ``REPLICATED_TABLES[table]['pk_columns']`` — ROWID is the
        cursor, the PK is the payload.

        Empty-table behaviour: the loop's first SELECT returns zero
        rows and falls through to the ``completed_at`` UPDATE, so an
        empty table just gets a progress row with started_at ≈
        completed_at and no enqueues. No special-case fast path
        needed.
        """
        pk_columns = REPLICATED_TABLES[table]["pk_columns"]

        with sqlite3.connect(self.db_file, timeout=10) as conn:
            conn.row_factory = sqlite3.Row

            # Look up cursor. completed_at set → skip entirely.
            existing = conn.execute(
                "SELECT last_pk, completed_at FROM bootstrap_progress "
                "WHERE pipeline='db' AND scope=?",
                (table,),
            ).fetchone()

            if existing is not None and existing["completed_at"] is not None:
                return

            if existing is None:
                # First run for this table — record total_rows snapshot
                # for monitoring.
                total = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO bootstrap_progress "
                    "(pipeline, scope, last_pk, total_rows, started_at) "
                    "VALUES ('db', ?, NULL, ?, ?)",
                    (table, total, _now_iso()),
                )
                conn.commit()
                last_rowid = 0
            else:
                # Resuming. last_pk holds the ROWID we finished on; the
                # next batch picks up from ROWID > last_pk.
                last_rowid = int(existing["last_pk"]) if existing["last_pk"] else 0

            pk_cols_sql = ", ".join(pk_columns)
            select_sql = (
                f"SELECT ROWID AS _rowid, {pk_cols_sql} FROM {table} "
                f"WHERE ROWID > ? ORDER BY ROWID LIMIT ?"
            )

            while True:
                rows = conn.execute(
                    select_sql, (last_rowid, batch_size),
                ).fetchall()
                if not rows:
                    break

                for row in rows:
                    pk_str = _format_pk_for_outbox(row, pk_columns)
                    outbox.enqueue_row(conn, table=table, pk=pk_str)
                    last_rowid = row["_rowid"]

                # Persist the new cursor + commit so a crash here
                # resumes from this point, not from zero.
                conn.execute(
                    "UPDATE bootstrap_progress SET last_pk=? "
                    "WHERE pipeline='db' AND scope=?",
                    (str(last_rowid), table),
                )
                conn.commit()

            # Loop exited with no rows in the final batch → done.
            conn.execute(
                "UPDATE bootstrap_progress SET completed_at=? "
                "WHERE pipeline='db' AND scope=?",
                (_now_iso(), table),
            )
            conn.commit()

    # -- Files pipeline ------------------------------------------------

    def start_files_bootstrap(self, dirs: list[tuple[str, Path]]) -> None:
        """Walk each ``(kind, root)`` pair recursively and enqueue
        every regular file into ``outbox_blobs``.

        ``dirs`` is a list because the caller (Phase 8 app wiring)
        wants to bootstrap multiple roots in one go — e.g.
        ``[('photo', Path('data/grow_images')), ('anomaly',
        Path('data/models/anomaly'))]``. Each root gets its own
        ``bootstrap_progress`` row keyed by ``str(root)``.

        ``kind`` ends up in ``outbox_blobs.kind`` for routing /
        diagnostics — see the worker's drain loop.
        """
        for kind, root in dirs:
            self._scan_directory(kind, root)

    def _scan_directory(self, kind: str, root: Path) -> None:
        """Walk one root recursively. ``target_key`` is the path
        relative to ``root`` (e.g. ``unit_001/2026-05-18/120000.jpg``
        for a ``data/grow_images`` root) so a later live writer enqueue
        with the same shape coalesces via the outbox UNIQUE constraint.

        Cursor is the relative path string. On resume, skip files
        whose relpath sorts ``<= last_pk``. Files are sorted before
        iteration so the cursor advances deterministically.

        Resumable: ``last_pk`` updated after every successful enqueue,
        so a crash mid-rglob loses at most one in-flight enqueue (and
        even that is fine — the outbox UNIQUE constraint coalesces the
        re-enqueue on next run).
        """
        scope = str(root)

        with sqlite3.connect(self.db_file, timeout=10) as conn:
            conn.row_factory = sqlite3.Row

            existing = conn.execute(
                "SELECT last_pk, completed_at FROM bootstrap_progress "
                "WHERE pipeline='files' AND scope=?",
                (scope,),
            ).fetchone()

            if existing is not None and existing["completed_at"] is not None:
                return

            if existing is None:
                conn.execute(
                    "INSERT INTO bootstrap_progress "
                    "(pipeline, scope, last_pk, total_rows, started_at) "
                    "VALUES ('files', ?, NULL, NULL, ?)",
                    (scope, _now_iso()),
                )
                conn.commit()
                last_pk = ""
            else:
                last_pk = existing["last_pk"] or ""

            # Sort files so the cursor advance is deterministic across
            # runs. rglob order isn't guaranteed otherwise.
            files = sorted(
                (p for p in root.rglob("*") if p.is_file()),
                key=lambda p: p.relative_to(root).as_posix(),
            )

            for path in files:
                rel = path.relative_to(root).as_posix()
                if last_pk and rel <= last_pk:
                    continue

                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                outbox.enqueue_blob(
                    conn,
                    kind=kind,
                    source_path=str(path),
                    target_key=rel,
                    sha256=sha,
                )
                conn.execute(
                    "UPDATE bootstrap_progress SET last_pk=? "
                    "WHERE pipeline='files' AND scope=?",
                    (rel, scope),
                )
                conn.commit()
                last_pk = rel

            conn.execute(
                "UPDATE bootstrap_progress SET completed_at=? "
                "WHERE pipeline='files' AND scope=?",
                (_now_iso(), scope),
            )
            conn.commit()

    # -- Reset (Force-re-bootstrap) ------------------------------------

    def reset(self, pipeline: str, scope: str | None = None) -> None:
        """Clear ``bootstrap_progress`` rows so the next
        ``start_*_bootstrap`` call re-enqueues from zero.

        Two modes:

          - ``scope=None``: clear every row for the pipeline. Used by
            the admin "Force re-bootstrap entire DB" button.
          - ``scope=<table>`` (db) or ``scope=<root path str>`` (files):
            clear one scope only. Used for targeted re-runs.

        Idempotent — clearing rows that don't exist is a silent no-op
        (the admin may trigger this before any bootstrap has ever
        run, e.g. as part of a config-wipe action).
        """
        with sqlite3.connect(self.db_file, timeout=10) as conn:
            if scope is None:
                conn.execute(
                    "DELETE FROM bootstrap_progress WHERE pipeline=?",
                    (pipeline,),
                )
            else:
                conn.execute(
                    "DELETE FROM bootstrap_progress "
                    "WHERE pipeline=? AND scope=?",
                    (pipeline, scope),
                )
            conn.commit()

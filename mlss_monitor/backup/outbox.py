"""Outbox storage helpers.

The outbox is a set of pointer tables (not row copies) co-located with the
live SQLite. Storage helpers run inside whatever transaction the caller has
open — they don't commit. The @tee_to_outbox decorator (added in a later
task) will use these helpers inside the live-write transaction so backup
state can never lag the live system.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
import functools
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Iterable


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def enqueue_row(conn: sqlite3.Connection, *, table: str, pk) -> None:
    """Insert-or-coalesce a row pointer in outbox_changes.

    If a pending entry for (table, pk) already exists, leave first_seen_at
    alone and bump last_change_at. Multiple updates to the same row collapse
    into one outbox entry — the shipper will read current state at ship-time.
    """
    now = _now_iso()
    conn.execute(
        "INSERT INTO outbox_changes "
        "(table_name, pk, first_seen_at, last_change_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(table_name, pk) DO UPDATE SET last_change_at=excluded.last_change_at",
        (table, str(pk), now, now),
    )


def enqueue_blob(conn: sqlite3.Connection, *, kind: str, source_path: str,
                 target_key: str, sha256: str) -> None:
    """Insert a blob pointer in outbox_blobs.

    Idempotent on target_key — if the same key is queued twice we silently
    keep the first entry. The S3 bucket+key is the canonical identity; two
    physical paths producing the same S3 key would conflict on upload anyway.
    """
    now = _now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO outbox_blobs "
        "(kind, source_path, target_key, sha256, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (kind, source_path, target_key, sha256, now),
    )


def peek_rows(conn: sqlite3.Connection, limit: int = 1000) -> list[dict]:
    """Return up to `limit` pending row entries in monotonic order.

    Does NOT delete or mark them — that happens after server ACK via
    delete_rows. Caller treats each entry as: (table_name, pk) to look up,
    then ship, then delete by id.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, table_name, pk, first_seen_at, last_change_at, ship_attempts "
        "FROM outbox_changes ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def peek_blobs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Return up to `limit` pending blob entries in monotonic order.

    Default limit lower than rows because blobs are slow to ship (each is a
    multi-MB upload over the network).
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, kind, source_path, target_key, sha256, first_seen_at, ship_attempts "
        "FROM outbox_blobs ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_rows(conn: sqlite3.Connection, *, ids: Iterable[int]) -> None:
    """Remove entries after the server has ACKed them."""
    id_list = list(ids)
    if not id_list:
        return
    placeholders = ",".join("?" * len(id_list))
    conn.execute(f"DELETE FROM outbox_changes WHERE id IN ({placeholders})",
                 tuple(id_list))


def delete_blobs(conn: sqlite3.Connection, *, ids: Iterable[int]) -> None:
    """Remove blob entries after S3 ACKs the upload."""
    id_list = list(ids)
    if not id_list:
        return
    placeholders = ",".join("?" * len(id_list))
    conn.execute(f"DELETE FROM outbox_blobs WHERE id IN ({placeholders})",
                 tuple(id_list))


def pending_count_rows(conn: sqlite3.Connection) -> int:
    """Count of pending row entries — for status panel."""
    return conn.execute("SELECT COUNT(*) FROM outbox_changes").fetchone()[0]


def pending_count_blobs(conn: sqlite3.Connection) -> int:
    """Count of pending blob entries — for status panel."""
    return conn.execute("SELECT COUNT(*) FROM outbox_blobs").fetchone()[0]


def increment_ship_attempts_rows(conn: sqlite3.Connection,
                                 *, ids: Iterable[int]) -> None:
    """Bump ship_attempts for the named rows. Used by worker on ship failure
    so the UI can show 'this row has retried N times' in diagnostics."""
    id_list = list(ids)
    if not id_list:
        return
    placeholders = ",".join("?" * len(id_list))
    conn.execute(
        f"UPDATE outbox_changes SET ship_attempts = ship_attempts + 1 "
        f"WHERE id IN ({placeholders})",
        tuple(id_list),
    )


def increment_ship_attempts_blobs(conn: sqlite3.Connection,
                                  *, ids: Iterable[int]) -> None:
    """Bump ship_attempts for blob entries on failure."""
    id_list = list(ids)
    if not id_list:
        return
    placeholders = ",".join("?" * len(id_list))
    conn.execute(
        f"UPDATE outbox_blobs SET ship_attempts = ship_attempts + 1 "
        f"WHERE id IN ({placeholders})",
        tuple(id_list),
    )


def tee_to_outbox(*, table: str, db_file: str | None = None):
    """Decorator: wrap a save helper so its live write + outbox enqueue
    commit in one transaction.

    Usage:
        @tee_to_outbox(table="sensor_data")
        def save_sensor_data(conn, ...):
            cur = conn.execute("INSERT INTO sensor_data ...")
            return cur.lastrowid

    The wrapped helper MUST:
      - take `conn: sqlite3.Connection` as its first positional argument
      - return the primary key of the row it wrote (for enqueueing)

    The decorator opens its own short-lived connection, calls the helper,
    enqueues, commits. The two writes share one transaction so a crash
    between them is impossible.

    `db_file` is optional — defaults to config.DB_FILE at call-time. Tests
    that use a tempfile pass an explicit override.
    """
    def wrap(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            from config import config as _cfg
            path = db_file or _cfg.get("DB_FILE", "data/sensor_data.db")
            with closing(sqlite3.connect(path, timeout=10)) as conn:
                with conn:  # transaction context — commit on success, rollback on exception
                    pk = fn(conn, *args, **kwargs)
                    if pk is None:
                        raise ValueError(
                            f"@tee_to_outbox wrapped helper for table={table!r} returned None; "
                            f"helper must return the row PK"
                        )
                    enqueue_row(conn, table=table, pk=pk)
            return pk
        return wrapped
    return wrap

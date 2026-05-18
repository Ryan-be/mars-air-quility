"""Schema for the backup outbox tables (Pi-side).

These three tables live inside the live SQLite (data/sensor_data.db) so the
@tee_to_outbox decorator's two writes (live row + outbox pointer) commit in a
single transaction.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    """Idempotent — safe to call on every startup."""
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS outbox_changes (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      table_name      TEXT NOT NULL,
      pk              TEXT NOT NULL,
      first_seen_at   DATETIME NOT NULL,
      last_change_at  DATETIME NOT NULL,
      ship_attempts   INTEGER NOT NULL DEFAULT 0,
      UNIQUE(table_name, pk)
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS outbox_blobs (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      kind            TEXT NOT NULL,
      source_path     TEXT NOT NULL,
      target_key      TEXT NOT NULL,
      sha256          TEXT NOT NULL,
      first_seen_at   DATETIME NOT NULL,
      ship_attempts   INTEGER NOT NULL DEFAULT 0,
      UNIQUE(target_key)
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bootstrap_progress (
      pipeline        TEXT NOT NULL,
      scope           TEXT NOT NULL,
      last_pk         TEXT,
      total_rows      INTEGER,
      started_at      DATETIME NOT NULL,
      completed_at    DATETIME,
      PRIMARY KEY(pipeline, scope)
    );
    """)
    conn.commit()

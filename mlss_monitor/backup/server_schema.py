"""Generate the receiving Postgres schema by introspecting the live
SQLite schema.

Single source of truth: REPLICATED_TABLES tells us which tables to
replicate + their PK columns; PRAGMA table_info tells us the column
shape. Adding a new replicated table only requires updating
REPLICATED_TABLES — the server DDL follows automatically.

POST /api/admin/backup/init?pipeline=db calls generate_ddl + passes
the result to PostgresClient.run_ddl. CREATE TABLE IF NOT EXISTS
makes the operation idempotent.

Skipped from server side (intentional, see module docstring):
  CHECK constraints, foreign keys, AUTOINCREMENT, SQLite DEFAULTs.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES


_SQLITE_TO_POSTGRES_TYPE: dict[str, str] = {
    "INTEGER":   "INTEGER",
    "TEXT":      "TEXT",
    "REAL":      "DOUBLE PRECISION",
    "DATETIME":  "TIMESTAMPTZ",
    "TIMESTAMP": "TIMESTAMPTZ",
    "BLOB":      "BYTEA",
}


def _sqlite_type_to_postgres(sqlite_type: str) -> str:
    """Map a SQLite type to Postgres. Defaults to TEXT for unrecognised
    types (defensive — better than failing the init on a column shape
    we haven't seen)."""
    if not sqlite_type:
        return "TEXT"
    # Strip parametrisation: "VARCHAR(255)" → "VARCHAR" → fall through to TEXT
    base = sqlite_type.strip().upper().split("(", 1)[0].strip()
    return _SQLITE_TO_POSTGRES_TYPE.get(base, "TEXT")


def _generate_table_ddl(conn: sqlite3.Connection, table: str,
                        pk_columns: list[str]) -> str:
    """Generate CREATE TABLE IF NOT EXISTS for one replicated table.

    Reads the column shape from ``PRAGMA table_info(table)``. Augments
    with source_pi_id + ingested_at + composite PK + index.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        raise ValueError(
            f"PRAGMA table_info({table!r}) returned no rows — table "
            f"missing from live SQLite. Run database/init_db.create_db() first."
        )

    col_defs = []
    for _cid, name, sqlite_type, notnull, _dflt, _pk_idx in rows:
        pg_type = _sqlite_type_to_postgres(sqlite_type)
        nullable = " NOT NULL" if notnull else ""
        col_defs.append(f"  {name} {pg_type}{nullable}")

    # Backup-specific columns
    col_defs.append("  source_pi_id TEXT NOT NULL")
    col_defs.append("  ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()")

    # Composite PK — source_pi_id appended so two Pis writing the same
    # `id` don't collide.
    pk_clause = ", ".join(pk_columns + ["source_pi_id"])
    col_defs.append(f"  PRIMARY KEY ({pk_clause})")

    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        + ",\n".join(col_defs)
        + "\n);"
    )


def _generate_index_ddl(table: str) -> str:
    """Time-series index for backup queries: latest first per Pi."""
    return (
        f"CREATE INDEX IF NOT EXISTS idx_{table}_source_ingested\n"
        f"  ON {table} (source_pi_id, ingested_at DESC);"
    )


def generate_ddl(sqlite_db_path: str) -> str:
    """Generate the complete server-side DDL.

    Returns a single string of semicolon-separated CREATE TABLE +
    CREATE INDEX statements, one pair per entry in REPLICATED_TABLES.
    psycopg2 accepts multi-statement strings in execute(), so the
    caller just passes the whole thing to PostgresClient.run_ddl.
    """
    statements: list[str] = []
    with closing(sqlite3.connect(sqlite_db_path)) as conn:
        for table, schema in REPLICATED_TABLES.items():
            statements.append(_generate_table_ddl(conn, table, schema["pk_columns"]))
            statements.append(_generate_index_ddl(table))
    return "\n\n".join(statements)

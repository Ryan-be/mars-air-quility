"""Tests for the server-side DDL generator.

mlss_monitor.backup.server_schema introspects the live SQLite schema
via PRAGMA table_info and produces CREATE TABLE / CREATE INDEX
statements for the receiving Postgres server. Every replicated table
gains source_pi_id + ingested_at + a composite PK + a time-series
index; CHECK constraints / FOREIGN KEYs / SQLite DEFAULTs are
intentionally dropped (Pi side enforces; server is the archive).

These tests are pure-Python — no real Postgres needed. The db_path
fixture from conftest.py primes a tempfile SQLite with the full live
schema, and we read the DDL strings the generator emits.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from mlss_monitor.backup import server_schema
from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES
from mlss_monitor.backup.server_schema import (
    _generate_index_ddl,
    _generate_table_ddl,
    _sqlite_type_to_postgres,
    generate_ddl,
)


# ─────────────────────────────────────────────────────────────────────
# _sqlite_type_to_postgres
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sqlite_type,expected", [
    ("INTEGER",   "INTEGER"),
    ("TEXT",      "TEXT"),
    ("REAL",      "DOUBLE PRECISION"),
    ("DATETIME",  "TIMESTAMPTZ"),
    ("TIMESTAMP", "TIMESTAMPTZ"),
    ("BLOB",      "BYTEA"),
    # Lowercase + mixed-case (PRAGMA returns the declared case verbatim)
    ("integer",   "INTEGER"),
    ("Real",      "DOUBLE PRECISION"),
    ("datetime",  "TIMESTAMPTZ"),
])
def test_sqlite_type_to_postgres_known_types(sqlite_type, expected):
    """Every type we explicitly map should round-trip; lookup is case-
    insensitive because PRAGMA preserves the declared casing."""
    assert _sqlite_type_to_postgres(sqlite_type) == expected


@pytest.mark.parametrize("sqlite_type", [
    "VARCHAR(255)",   # parametrised type — strip parens, fall through
    "NUMERIC(10,2)",  # parametrised type with multiple args
    "BIGINT",         # unknown
    "",               # empty string from PRAGMA on bare-affinity columns
    "WEIRDTYPE",      # totally unrecognised
])
def test_sqlite_type_to_postgres_unknown_falls_through_to_text(sqlite_type):
    """Unknown types default to TEXT — defensive, not raising."""
    assert _sqlite_type_to_postgres(sqlite_type) == "TEXT"


def test_sqlite_type_to_postgres_strips_parametrisation():
    """VARCHAR(255) → strip to VARCHAR → falls through to TEXT."""
    assert _sqlite_type_to_postgres("VARCHAR(255)") == "TEXT"
    # Even with whitespace
    assert _sqlite_type_to_postgres("  VARCHAR ( 255 ) ") == "TEXT"


# ─────────────────────────────────────────────────────────────────────
# _generate_table_ddl — exercises against the live SQLite schema
# ─────────────────────────────────────────────────────────────────────


def test_generate_table_ddl_adds_source_pi_id_and_ingested_at(db_path):
    """Every replicated table on the server gets the two backup
    columns appended to its original column list."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "sensor_data", ["id"])
    assert "source_pi_id TEXT NOT NULL" in ddl
    assert "ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()" in ddl


def test_generate_table_ddl_composite_pk_includes_source_pi_id(db_path):
    """Single-PK table sensor_data gets PRIMARY KEY (id, source_pi_id)."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "sensor_data", ["id"])
    assert "PRIMARY KEY (id, source_pi_id)" in ddl


def test_generate_table_ddl_composite_pk_table(db_path):
    """incident_alerts has composite PK (incident_id, alert_id). The
    server-side PK appends source_pi_id, so it becomes
    (incident_id, alert_id, source_pi_id) — three columns, in order."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(
            conn, "incident_alerts", ["incident_id", "alert_id"]
        )
    assert "PRIMARY KEY (incident_id, alert_id, source_pi_id)" in ddl


def test_generate_table_ddl_preserves_not_null(db_path):
    """SQLite's NOT NULL columns should carry over to Postgres. e.g.
    sensor_data.timestamp is declared NOT NULL."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "sensor_data", ["id"])
    # timestamp is NOT NULL in the SQLite schema
    assert "timestamp TIMESTAMPTZ NOT NULL" in ddl
    # temperature is nullable in the SQLite schema (no NOT NULL on it)
    # — verify it does NOT get a NOT NULL on the server side.
    assert "temperature DOUBLE PRECISION NOT NULL" not in ddl
    assert "temperature DOUBLE PRECISION" in ddl


def test_generate_table_ddl_drops_check_constraints(db_path):
    """incidents.max_severity has a CHECK(...) constraint in SQLite.
    PRAGMA table_info doesn't expose CHECK constraints as part of the
    column DDL, so they're naturally absent from our generated DDL —
    but assert explicitly so a future refactor can't reintroduce them."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "incidents", ["id"])
    assert "CHECK" not in ddl.upper()
    # max_severity should still be there as a plain TEXT column
    assert "max_severity TEXT" in ddl


def test_generate_table_ddl_drops_foreign_keys(db_path):
    """event_tags.inference_id has a FOREIGN KEY in SQLite. The server
    side is an archive (Pi enforces referential integrity), so the DDL
    should not contain any FK clauses."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "event_tags", ["id"])
    assert "FOREIGN KEY" not in ddl.upper()
    assert "REFERENCES" not in ddl.upper()
    # inference_id should still be there as a plain INTEGER NOT NULL
    assert "inference_id INTEGER NOT NULL" in ddl


def test_generate_table_ddl_raises_for_missing_table(db_path):
    """If a replicated-table entry references a table that doesn't
    exist in the live SQLite schema, fail loud — silently emitting an
    empty DDL would let the server be init'd with missing tables."""
    with closing(sqlite3.connect(db_path)) as conn:
        with pytest.raises(ValueError, match="returned no rows"):
            _generate_table_ddl(conn, "this_table_does_not_exist", ["id"])


def test_generate_table_ddl_is_create_table_if_not_exists(db_path):
    """Idempotent re-runs: every CREATE TABLE must use IF NOT EXISTS so
    re-applying the DDL on a populated server is a no-op."""
    with closing(sqlite3.connect(db_path)) as conn:
        ddl = _generate_table_ddl(conn, "sensor_data", ["id"])
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS sensor_data")


# ─────────────────────────────────────────────────────────────────────
# _generate_index_ddl
# ─────────────────────────────────────────────────────────────────────


def test_generate_index_ddl_shape():
    """The time-series index is named consistently and orders
    ingested_at DESC so a "newest first per Pi" scan is one
    btree-range read."""
    ddl = _generate_index_ddl("sensor_data")
    assert "CREATE INDEX IF NOT EXISTS idx_sensor_data_source_ingested" in ddl
    assert "ON sensor_data (source_pi_id, ingested_at DESC)" in ddl


# ─────────────────────────────────────────────────────────────────────
# generate_ddl — end-to-end
# ─────────────────────────────────────────────────────────────────────


def test_generate_ddl_covers_all_replicated_tables(db_path):
    """One CREATE TABLE + one CREATE INDEX per REPLICATED_TABLES entry."""
    ddl = generate_ddl(db_path)
    n_tables = len(REPLICATED_TABLES)
    assert ddl.count("CREATE TABLE IF NOT EXISTS") == n_tables
    assert ddl.count("CREATE INDEX IF NOT EXISTS") == n_tables
    # Each table name appears in the DDL (at minimum in the CREATE TABLE line)
    for table in REPLICATED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in ddl


def test_generate_ddl_emits_parseable_statements(db_path):
    """Splitting on `;` yields statements that each begin with
    CREATE TABLE IF NOT EXISTS or CREATE INDEX IF NOT EXISTS (after
    stripping leading whitespace). psycopg2 accepts the joined string
    as a multi-statement execute, but verify the shape anyway."""
    ddl = generate_ddl(db_path)
    statements = [s.strip() for s in ddl.split(";") if s.strip()]
    # 2 statements per table (CREATE TABLE + CREATE INDEX)
    assert len(statements) == 2 * len(REPLICATED_TABLES)
    for stmt in statements:
        assert (
            stmt.startswith("CREATE TABLE IF NOT EXISTS")
            or stmt.startswith("CREATE INDEX IF NOT EXISTS")
        ), f"unexpected statement: {stmt[:80]!r}"


def test_generate_ddl_idempotent_via_if_not_exists(db_path):
    """Every CREATE in the emitted DDL uses IF NOT EXISTS, so feeding
    the same string to a populated server twice is a no-op."""
    ddl = generate_ddl(db_path)
    # Every CREATE in the DDL is the IF NOT EXISTS variant.
    assert ddl.count("CREATE TABLE ") == ddl.count("CREATE TABLE IF NOT EXISTS")
    assert ddl.count("CREATE INDEX ") == ddl.count("CREATE INDEX IF NOT EXISTS")


def test_generate_ddl_composite_pk_for_grow_unit_capabilities(db_path):
    """grow_unit_capabilities has SQLite PK (unit_id, channel) — the
    server-side composite PK should be (unit_id, channel, source_pi_id).
    Regression-guards against re-ordering source_pi_id into the middle."""
    ddl = generate_ddl(db_path)
    assert "PRIMARY KEY (unit_id, channel, source_pi_id)" in ddl


def test_generate_ddl_module_exposes_only_one_public_entry():
    """Best-practices guard: no new abstractions sneaking in. The
    module's public API is generate_ddl + the type-mapping function;
    the table/index helpers are private."""
    public = [name for name in dir(server_schema)
              if not name.startswith("_") and callable(getattr(server_schema, name))]
    # generate_ddl is the public entry point. The type-mapping helper
    # is private (leading underscore). Other module-level callables
    # like `closing` and `REPLICATED_TABLES` are imports — filter them.
    public_local = [
        name for name in public
        if getattr(server_schema, name).__module__ == server_schema.__name__
    ]
    assert public_local == ["generate_ddl"]

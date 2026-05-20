"""Postgres client for the backup pipeline.

Wraps psycopg2 with batch UPSERT, connection test, and DDL exec. The
worker uses these methods to ship outbox row pointers to the home
Postgres server.

Server-side schema for every replicated table adds:
  - ``source_pi_id TEXT NOT NULL``  — partitions multi-Pi data
  - ``ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``  — server stamp

The UPSERT conflict key is ``(*pk_columns, source_pi_id)``. The client
appends its configured source_pi_id to every row tuple; the server
default handles ingested_at.

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras


class PostgresClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        source_pi_id: str,
        sslmode: str = "require",
        sslrootcert: str | None = None,
        timeout: int = 10,
    ) -> None:
        # source_pi_id is the partition key for multi-Pi data on the
        # server. ``delete_scope`` builds its WHERE clause as
        # ``source_pi_id = %s AND …`` — passing an empty string or
        # whitespace would silently match every row whose
        # source_pi_id is `''` (e.g. left over from a corrupt earlier
        # ingest) and DELETE them, potentially cross-Pi-wiping data.
        # None would NULL-compare in WHERE (always false) but slip
        # through every test because nothing else uses it. Fail loud
        # at construction so misconfigured deployments can't reach
        # delete_scope.
        if not source_pi_id or not source_pi_id.strip():
            raise ValueError(
                "source_pi_id is required and must be a non-empty string — "
                "this Pi's data on the server is partitioned by this value, "
                "and an empty source_pi_id could cross-Pi-DELETE on a "
                "future delete_scope call."
            )
        self._kwargs: dict = {
            "host": host,
            "port": port,
            "dbname": database,
            "user": user,
            "password": password,
            "sslmode": sslmode,
            "connect_timeout": timeout,
        }
        if sslrootcert:
            self._kwargs["sslrootcert"] = sslrootcert
        self.source_pi_id = source_pi_id.strip()

    def _connect(self):
        """Open a fresh connection. Returns a connection that's already
        in a transaction — caller is expected to use it as a context
        manager so commit/rollback happens on exit."""
        return psycopg2.connect(**self._kwargs)

    def test_connection(self) -> dict:
        """Try to connect + SELECT version(). Returns a dict — never
        raises (caller is a Flask route that needs to JSON-serialise the
        result whatever happens)."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version()")
                    version = cur.fetchone()[0]
            return {"ok": True, "version": version}
        except Exception as exc:  # pylint: disable=broad-except
            return {"ok": False, "error": str(exc)}

    def upsert_rows(
        self,
        *,
        table: str,
        pk_columns: list[str],
        rows: list[dict],
    ) -> None:
        """Batch UPSERT a list of rows into ``table``.

        Each row dict's keys form the column list. ``source_pi_id`` is
        appended automatically. Conflict key is ``(*pk_columns, source_pi_id)``;
        every non-pk column gets ``=EXCLUDED.{col}`` in the SET clause so
        re-shipping a row updates the server-side copy.

        ``ingested_at`` is NOT set — the server's DEFAULT NOW() takes care
        of stamping each upsert.

        Empty ``rows`` is a no-op (does not open a connection).
        """
        if not rows:
            return
        columns = list(rows[0].keys())
        cols_sql = ", ".join(columns + ["source_pi_id"])
        placeholders = ", ".join(["%s"] * (len(columns) + 1))
        conflict_cols = ", ".join(pk_columns + ["source_pi_id"])
        # Update every non-pk column. PK columns stay constant by
        # construction — re-setting them to themselves is wasted IO.
        update_set = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in columns if c not in pk_columns
        )
        sql = (
            f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_set}"
        )
        values = [
            tuple(r[c] for c in columns) + (self.source_pi_id,)
            for r in rows
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, values)
            # `with conn:` auto-commits on context exit if no exception.

    def delete_scope(self, *, table: str, scope: dict) -> None:
        """Delete rows on the server matching ``source_pi_id`` AND every
        column in ``scope``. Empty ``scope`` deletes ALL of this Pi's
        rows from the table.

        Used by the BackupWorker DB sub-worker to ship strict-mirror
        wipes (outbox_delete_scope entries) BEFORE the corresponding
        INSERTs, so an operator's DELETE+INSERT replace pattern arrives
        atomically on the server side.

        SQL shape::

            DELETE FROM {table}
             WHERE source_pi_id = %s
               AND col1 = %s AND col2 = %s ...

        ``source_pi_id`` is always the first parameter. Empty ``scope``
        leaves the WHERE clause at just the ``source_pi_id`` predicate
        — i.e. "wipe all of this Pi's rows from {table}".
        """
        if not isinstance(scope, dict):
            raise TypeError(f"scope must be a dict, got {type(scope).__name__}")
        where_clauses = ["source_pi_id = %s"]
        values: list = [self.source_pi_id]
        for col, val in scope.items():
            where_clauses.append(f"{col} = %s")
            values.append(val)
        sql = f"DELETE FROM {table} WHERE {' AND '.join(where_clauses)}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, values)
            # `with conn:` auto-commits on context exit if no exception.

    def run_ddl(self, sql: str) -> None:
        """Execute arbitrary DDL. Used by POST /init?pipeline=db to apply
        the server-side schema (the create-table statements that add
        source_pi_id + ingested_at to every replicated table)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)

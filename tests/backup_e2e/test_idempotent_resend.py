"""E2E failure mode #3: same outbox entry shipped twice → Postgres
has exactly one row.

Models the "worker crashed between PG ack and outbox delete commit"
scenario. The same row pointer gets shipped a second time on
recovery. The Postgres ``ON CONFLICT … DO UPDATE`` semantics in
``PostgresClient.upsert_rows`` must make this a no-op (or
idempotent update); two ships of the same row MUST NOT produce two
rows on the server.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.backup_e2e.conftest import wait_until

pytestmark = pytest.mark.e2e


def test_resending_outbox_entry_keeps_one_row(configured_backup, db_worker):
    """Write a row, let it ship. Manually re-enqueue the SAME pointer
    in the outbox, let it ship again. Postgres must hold ONE row,
    not two.
    """
    from database.db_logger import log_sensor_data  # pylint: disable=import-outside-toplevel
    from database.init_db import DB_FILE  # pylint: disable=import-outside-toplevel
    from mlss_monitor.backup import outbox  # pylint: disable=import-outside-toplevel

    pk = log_sensor_data(21.0, 55.0, 480, 75)
    pg = configured_backup["pg"]

    def first_ship_landed() -> bool:
        with pg._connect() as conn:  # pylint: disable=protected-access
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM sensor_data "
                    "WHERE id=%s AND source_pi_id='test-pi'",
                    (pk,),
                )
                return cur.fetchone() is not None

    wait_until(
        first_ship_landed, timeout=20.0,
        message="initial sensor row did not land on first ship",
    )

    # Re-enqueue the same pk. enqueue_row does INSERT OR ... DO UPDATE
    # so this isn't a strict duplicate at the SQLite level, but it
    # restores the pointer for the worker to re-pick.
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        with conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=pk)

    # Wait for the worker to drain the re-enqueued pointer. We can't
    # rely on a state assertion here (the drain is fast), so poll
    # until the outbox is empty again.
    def outbox_drained() -> bool:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            return outbox.pending_count_rows(conn) == 0

    wait_until(
        outbox_drained, timeout=20.0,
        message="re-enqueued outbox entry was not drained",
    )

    # Server-side: must have EXACTLY one row with this id +
    # source_pi_id. Postgres' composite PK (id, source_pi_id) +
    # ON CONFLICT UPDATE is what enforces this — without it, the
    # second ship would create a duplicate.
    with pg._connect() as conn:  # pylint: disable=protected-access
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM sensor_data "
                "WHERE id=%s AND source_pi_id='test-pi'",
                (pk,),
            )
            count = cur.fetchone()[0]
    assert count == 1, (
        f"Expected exactly 1 row after idempotent resend, got {count}. "
        "ON CONFLICT (id, source_pi_id) DO UPDATE is supposed to make "
        "double-ship a no-op."
    )

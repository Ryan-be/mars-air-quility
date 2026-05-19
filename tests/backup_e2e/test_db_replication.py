"""E2E happy-path #1: live SQLite write → outbox enqueue → worker
drain → Postgres row visible.

Covered:
  - Sensor data row lands on the server-side Postgres with the
    correct ``source_pi_id`` partition.
  - Strict-mirror replace (grow_unit_capabilities) propagates the
    delete-scope wipe ahead of the new INSERTs so the server's row
    set matches the Pi-side state, not the union of old + new.

The whole production code path runs: ``log_sensor_data`` /
``handle_capabilities`` enqueue via ``@tee_to_outbox`` ; the
``db_worker`` thread drains; ``configured_backup["pg"]._connect()``
verifies server-side state. Nothing is mocked except the worker's
PostgresClient ``sslmode='disable'`` (no TLS in the local container).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.backup_e2e.conftest import wait_until

pytestmark = pytest.mark.e2e


def test_sensor_data_row_lands_in_postgres(configured_backup, db_worker):
    """Write one sensor row via the live ``log_sensor_data`` helper.
    The decorator enqueues an outbox pointer; the worker thread
    drains; the server-side Postgres has a row with our value +
    ``source_pi_id='test-pi'`` within the poll window.
    """
    from database.db_logger import log_sensor_data  # pylint: disable=import-outside-toplevel
    pk = log_sensor_data(22.5, 45.0, 400, 20)
    pg = configured_backup["pg"]

    def shipped() -> bool:
        with pg._connect() as conn:  # pylint: disable=protected-access
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, temperature, source_pi_id "
                    "FROM sensor_data WHERE id=%s",
                    (pk,),
                )
                row = cur.fetchone()
        if row is None:
            return False
        assert row[1] == pytest.approx(22.5)
        assert row[2] == "test-pi"
        return True

    wait_until(
        shipped, timeout=20.0,
        message=f"sensor_data id={pk} did not land in Postgres",
    )


def test_strict_mirror_delete_scope_propagates(configured_backup, db_worker):
    """Replace ``grow_unit_capabilities`` for one unit twice. After
    the second replace, the server must hold the SECOND set only —
    not the union of the first + second.

    This exercises the strict-mirror path:
      1. First ``handle_capabilities`` enqueues a delete-scope wipe
         + 2 row pointers for {soil, pump}.
      2. After the worker drains, the server holds {soil, pump}.
      3. Second ``handle_capabilities`` enqueues another wipe + 1
         row pointer for {light}.
      4. After the second drain, the server holds {light} only —
         the wipe ran before the INSERT, so soil + pump are gone.

    If the wipe were not honoured (or ran AFTER the INSERTs), the
    server would still hold soil/pump and the final assertion would
    see 3 rows.
    """
    import sqlite3  # pylint: disable=import-outside-toplevel
    from database.init_db import DB_FILE  # pylint: disable=import-outside-toplevel
    from mlss_monitor.grow import handlers  # pylint: disable=import-outside-toplevel

    # Seed one grow_unit so handle_capabilities has an FK target.
    now = datetime.utcnow()
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        conn.execute(
            "INSERT INTO grow_units "
            "(id, hardware_serial, label, enrolled_at, bearer_token_hash, "
            " phase_set_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (42, "test-serial", "unit-42", now, "hash", now),
        )
        conn.commit()

    pg = configured_backup["pg"]

    # First replace: {soil, pump}.
    handlers.handle_capabilities(unit_id=42, ts=now, payload={
        "capabilities": [
            {"channel": "soil", "hardware": "STEMMA", "is_required": True},
            {"channel": "pump", "hardware": "GPIO",   "is_required": True},
        ],
    })

    def first_replace_landed() -> bool:
        with pg._connect() as conn:  # pylint: disable=protected-access
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT channel FROM grow_unit_capabilities "
                    "WHERE unit_id=42 AND source_pi_id='test-pi' "
                    "ORDER BY channel"
                )
                rows = cur.fetchall()
        return [r[0] for r in rows] == ["pump", "soil"]

    wait_until(
        first_replace_landed, timeout=20.0,
        message="first capability set did not land on server",
    )

    # Second replace: {light} only. After the drain, soil + pump
    # must be GONE — the delete-scope wipe ran before the INSERT.
    handlers.handle_capabilities(unit_id=42, ts=now, payload={
        "capabilities": [
            {"channel": "light", "hardware": "GPIO", "is_required": False},
        ],
    })

    def second_replace_only_light() -> bool:
        with pg._connect() as conn:  # pylint: disable=protected-access
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT channel FROM grow_unit_capabilities "
                    "WHERE unit_id=42 AND source_pi_id='test-pi' "
                    "ORDER BY channel"
                )
                rows = cur.fetchall()
        return [r[0] for r in rows] == ["light"]

    wait_until(
        second_replace_only_light, timeout=20.0,
        message="strict-mirror replace did not propagate — "
                "server still holds rows from the first capability set",
    )

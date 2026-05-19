"""E2E failure mode #1: Postgres pause → worker BACKOFF → unpause →
drain catches up.

Models the dominant operational failure: the home-server Pi loses
its uplink overnight. The Pi-side worker should NOT lose data — it
should sit in BACKOFF (the outbox queue grows), then drain the
backlog automatically when the server comes back.

We use docker pause/unpause (not stop/start) because:
  - stop() would invalidate the session-scoped ``postgres`` fixture
    for subsequent tests in the same session.
  - pause() freezes the container's processes without dropping
    state, which is closer to the real "network outage" scenario.
"""
from __future__ import annotations

import pytest

from tests.backup_e2e.conftest import wait_until

pytestmark = pytest.mark.e2e


def test_backlog_drains_after_postgres_unpause(configured_backup, db_worker):
    """Pause Postgres mid-stream, write a sensor row, observe worker
    enters BACKOFF, unpause, observe row eventually lands. The
    worker should automatically recover once Postgres is reachable
    again — no admin intervention.
    """
    from database.db_logger import log_sensor_data  # pylint: disable=import-outside-toplevel

    pg = configured_backup["pg"]
    pg_container = configured_backup["postgres_params"]["container"]
    raw = pg_container.get_wrapped_container()

    # Pause Postgres. The worker is currently IDLE; next drain will
    # fail to connect → transitions to BACKOFF.
    raw.pause()
    try:
        # Write a row. The outbox enqueue happens locally and succeeds
        # (it's just SQLite). The worker will pick it up, try to ship,
        # and fail.
        pk = log_sensor_data(19.0, 60.0, 500, 50)

        # Worker reaction loop: poll outbox (every ~_DRAINING_POLL_S),
        # find the row, try ``pg_client.upsert_rows`` → ``_connect()``
        # hangs at TCP level (Docker pause freezes the container's
        # entire net namespace so SYN-ACKs never come) until psycopg2's
        # configured ``connect_timeout`` fires → exception bubbles →
        # BACKOFF. With the conftest's 3s connect-timeout override,
        # worst-case wall-clock is ~3s + scheduling.
        #
        # We poll ``last_error`` rather than ``state`` because the
        # state oscillates between BACKOFF (just failed) and DRAINING
        # (about to retry); ``last_error`` is the durable signal that
        # at least one ship attempt has failed.
        wait_until(
            lambda: db_worker.last_error is not None,
            timeout=30.0,
            message="worker did not record a ship failure while Postgres was paused",
        )
    finally:
        # Always unpause so the container is usable for the next test
        # in the session — pause leaks would block every subsequent
        # postgres-dependent test.
        raw.unpause()

    # After unpause: the next drain attempt succeeds + the row lands.
    # ``request_reload`` wakes the BACKOFF sleep immediately so we
    # don't have to wait out the (potentially 32+ second) backoff.
    db_worker.request_reload()

    def shipped() -> bool:
        with pg._connect() as conn:  # pylint: disable=protected-access
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM sensor_data "
                    "WHERE id=%s AND source_pi_id='test-pi'",
                    (pk,),
                )
                return cur.fetchone() is not None

    wait_until(
        shipped, timeout=30.0,
        message=f"sensor_data id={pk} did not catch up after Postgres unpause",
    )

"""E2E failure mode #2: wrong creds → BACKOFF → admin fixes creds
→ ``backup_config_changed`` event wakes worker → drain succeeds.

This is the admin's recovery path: a typo in the password was
caught after the Pi went live; the worker is in BACKOFF; the admin
saves a corrected config in the UI; the event bus broadcasts
``backup_config_changed``; the worker's listener thread fires
``request_reload()`` which resets backoff + sets ``_reload_event``;
the next iteration of the run loop drains successfully.

Constructs its own worker (not the ``db_worker`` fixture) because
we need an injected EventBus to deliver the reload event. The
conftest's ``db_worker`` builds a worker with ``event_bus=None``
for simplicity.
"""
from __future__ import annotations

import pytest

from tests.backup_e2e.conftest import wait_until

pytestmark = pytest.mark.e2e


def test_hot_reload_recovers_from_wrong_password(configured_backup,
                                                  postgres):
    """Save WRONG password → start worker → it enters BACKOFF → save
    CORRECT password + publish reload event → worker drains.
    """
    from database.db_logger import log_sensor_data  # pylint: disable=import-outside-toplevel
    from mlss_monitor.backup import config  # pylint: disable=import-outside-toplevel
    from mlss_monitor.backup.worker import (  # pylint: disable=import-outside-toplevel
        BackupWorker,
    )
    from mlss_monitor.event_bus import EventBus  # pylint: disable=import-outside-toplevel

    # Wreck the config first so the worker starts with wrong creds.
    config.save({"db": {"password": "wrong-password"}})

    bus = EventBus()
    w = BackupWorker(pipeline="db", event_bus=bus)
    w._on_enabled()  # pylint: disable=protected-access
    w.start()
    try:
        # Write a row — it queues locally; the worker tries to ship
        # with bad creds; transitions to BACKOFF.
        pk = log_sensor_data(20.0, 50.0, 450, 100)

        # Wait until at least one ship attempt has failed (we poll
        # ``last_error`` rather than ``state`` — see the note in
        # test_overnight_outage for why).
        wait_until(
            lambda: w.last_error is not None,
            timeout=15.0,
            message="worker did not record a ship failure with wrong password",
        )

        # Fix the config + broadcast the reload event. ``config.save``
        # alone doesn't wake the worker — only the event does (the
        # listener thread reads it from the subscription queue).
        config.save({"db": {"password": postgres["password"]}})
        bus.publish("backup_config_changed", {"pipeline": "db"})

        pg = configured_backup["pg"]

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
            message=(f"sensor_data id={pk} did not ship after hot-reload "
                     "with corrected creds"),
        )
    finally:
        w.stop(timeout=5.0)

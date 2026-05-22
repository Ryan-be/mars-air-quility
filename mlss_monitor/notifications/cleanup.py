"""Periodic pruning of notification_history.

30-day default retention. Single daemon thread, runs once a day. Failure
during prune is logged at WARNING and the loop continues — never crash
the host process.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta

from config import config

log = logging.getLogger(__name__)

_DEFAULT_DAYS = 30
_DEFAULT_INTERVAL_HOURS = 24


def _db_file() -> str:
    return config.get("DB_FILE", "data/sensor_data.db")


def prune_old_notifications(days: int = _DEFAULT_DAYS) -> int:
    """Delete rows older than `days` from notification_history.

    Returns the number of rows deleted.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(_db_file())
    try:
        cur = conn.execute(
            "DELETE FROM notification_history WHERE created_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _loop(interval_hours: float, days: int, stop_event: threading.Event) -> None:
    # Sleep first so we don't prune on every restart spike.
    while not stop_event.wait(interval_hours * 3600):
        try:
            n = prune_old_notifications(days=days)
            if n:
                log.info("Pruned %d notification_history rows older than %d days",
                         n, days)
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("notification cleanup failed: %s", exc)


def start_cleanup_loop(
    interval_hours: float = _DEFAULT_INTERVAL_HOURS,
    days: int = _DEFAULT_DAYS,
) -> threading.Thread:
    """Start the daemon prune loop. Returns the Thread for inspection in tests."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=_loop,
        args=(interval_hours, days, stop_event),
        daemon=True,
        name="NotificationCleanupLoop",
    )
    t._stop_event = stop_event  # so tests can stop cleanly if needed
    t.start()
    return t

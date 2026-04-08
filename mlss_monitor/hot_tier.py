"""HotTier: in-memory ring buffer of NormalisedReadings with SQLite persistence.

The DB is optional — pass db_file=None (or omit it) to run purely in-memory,
which is how all pre-existing tests use it.

When db_file is provided:
- __init__ loads the last 60 minutes of rows from the DB into the deque.
- push() inserts each reading into the DB as well as the deque.
- prune_old() deletes rows older than 60 minutes (call every 60s from app.py).

A single persistent SQLite connection (check_same_thread=False) is kept open
for the lifetime of the HotTier so that push() does not pay a connection-open
overhead on every call (3600×/hour at 1 Hz).  CPython's GIL keeps single-writer
/ single-pruner access safe in practice; the explicit reconnect-on-error path
handles the rare case where the connection goes stale.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone

from config import config

log = logging.getLogger(__name__)

# Module-level DB_FILE so tests can patch it (same pattern as db_logger.py).
DB_FILE: str = config.get("DB_FILE", "data/sensor_data.db")

# Ordered list of sensor columns in hot_tier table (matches NormalisedReading fields).
_SENSOR_COLS: tuple[str, ...] = (
    "tvoc_ppb", "eco2_ppm", "temperature_c",
    "humidity_pct", "pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3",
    "co_ppb", "no2_ppb", "nh3_ppb", "pressure_hpa",
)


class HotTier:
    """In-memory ring buffer of NormalisedReading objects.

    Thread-safe for single-writer / multiple-reader usage under CPython's GIL.
    deque.append() and reads via list() are atomic in CPython.

    Args:
        maxlen: Maximum number of readings to keep in memory (default 3600 = 1hr at 1Hz).
        db_file: Path to SQLite DB for persistence. Pass None to disable DB entirely.
                 When None, push/prune are no-ops against the DB and __init__ skips
                 the reload. All existing behaviour is preserved.
    """

    def __init__(self, maxlen: int = 3600, db_file: str | None = None) -> None:
        self._buffer: deque = deque(maxlen=maxlen)
        self._db_file = db_file
        self._conn: sqlite3.Connection | None = None
        if self._db_file is not None:
            self._conn = self._open_connection()
            self._load_from_db()

    # ── Public API (unchanged from original) ─────────────────────────────────

    def push(self, reading) -> None:
        self._buffer.append(reading)
        if self._db_file is not None:
            self._insert_row(reading)

    def latest(self):
        return self._buffer[-1] if self._buffer else None

    def size(self) -> int:
        return len(self._buffer)

    def last_n(self, n: int) -> list:
        """Return the n most recent readings, oldest first."""
        buf = list(self._buffer)
        return buf[-n:] if n <= len(buf) else buf

    def last_minutes(self, minutes: float) -> list:
        """Return all readings from the last `minutes` minutes, oldest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [r for r in self._buffer if r.timestamp >= cutoff]

    def snapshot(self) -> list:
        """Return a full copy of the buffer contents, oldest first."""
        return list(self._buffer)

    # ── New: DB maintenance ───────────────────────────────────────────────────

    def prune_old(self) -> None:
        """Delete rows older than 60 minutes from the hot_tier table.

        Call this periodically (every 60s) from the background log loop.
        No-op when db_file is None.
        """
        if self._db_file is None:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        try:
            conn = self._get_connection()
            conn.execute("DELETE FROM hot_tier WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception as exc:
            log.warning("HotTier.prune_old failed: %s", exc)
            self._conn = None  # Force reconnect on next call

    # ── Private helpers ───────────────────────────────────────────────────────

    def _open_connection(self) -> sqlite3.Connection:
        """Open and return a new persistent SQLite connection."""
        conn = sqlite3.connect(self._db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        """Return the persistent connection, reopening it if it went stale."""
        if self._conn is None:
            self._conn = self._open_connection()
        return self._conn

    def _load_from_db(self) -> None:
        """Load the last 60 minutes of rows from DB into the deque on startup."""
        from mlss_monitor.data_sources.base import NormalisedReading
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM hot_tier WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff,),
            ).fetchall()
            for row in rows:
                ts_str = row["timestamp"]
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                reading = NormalisedReading(
                    timestamp=ts,
                    source=row["source"],
                    **{col: row[col] for col in _SENSOR_COLS},
                )
                self._buffer.append(reading)
            if rows:
                log.info("HotTier: loaded %d readings from DB (last 60 min)", len(rows))
        except Exception as exc:
            log.warning("HotTier: could not load from DB: %s", exc)
            self._conn = None

    def _insert_row(self, reading) -> None:
        """Insert one NormalisedReading row into hot_tier using the persistent connection."""
        cols = ("timestamp", "source") + _SENSOR_COLS
        placeholders = ", ".join("?" for _ in cols)
        values = (
            reading.timestamp.isoformat(),
            reading.source,
            *[getattr(reading, col) for col in _SENSOR_COLS],
        )
        try:
            conn = self._get_connection()
            conn.execute(
                f"INSERT INTO hot_tier ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
        except Exception as exc:
            log.warning("HotTier._insert_row failed: %s", exc)
            self._conn = None  # Force reconnect on next call

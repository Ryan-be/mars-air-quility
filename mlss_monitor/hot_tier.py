"""HotTier: in-memory ring buffer of NormalisedReadings with SQLite persistence.

The DB is optional — pass db_file=None (or omit it) to run purely in-memory,
which is how all pre-existing tests use it.

When db_file is provided:
- __init__ loads the last 60 minutes of rows from the DB into the deque.
- push() inserts each reading into the DB as well as the deque.
- prune_old() deletes rows older than 60 minutes (call every 60s from app.py).
"""
from __future__ import annotations

import logging
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlss_monitor.data_sources.base import NormalisedReading

log = logging.getLogger(__name__)

# Module-level DB_FILE so tests can patch it (same pattern as db_logger.py).
from config import config
DB_FILE: str = config.get("DB_FILE", "data/sensor_data.db")

# Ordered list of sensor columns in hot_tier table (matches NormalisedReading fields).
_SENSOR_COLS: tuple[str, ...] = (
    "tvoc_ppb", "eco2_ppm", "temperature_c",
    "humidity_pct", "pm25_ug_m3", "co_ppb", "no2_ppb", "nh3_ppb",
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
        if self._db_file is not None:
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
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.execute("DELETE FROM hot_tier WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception as exc:
            log.warning("HotTier.prune_old failed: %s", exc)
        finally:
            if conn:
                conn.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        """Load the last 60 minutes of rows from DB into the deque on startup."""
        from mlss_monitor.data_sources.base import NormalisedReading
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.row_factory = sqlite3.Row
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
        finally:
            if conn:
                conn.close()

    def _insert_row(self, reading) -> None:
        """Insert one NormalisedReading row into hot_tier."""
        cols = ("timestamp", "source") + _SENSOR_COLS
        placeholders = ", ".join("?" for _ in cols)
        values = (
            reading.timestamp.isoformat(),
            reading.source,
            *[getattr(reading, col) for col in _SENSOR_COLS],
        )
        conn = None
        try:
            conn = sqlite3.connect(self._db_file)
            conn.execute(
                f"INSERT INTO hot_tier ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
        except Exception as exc:
            log.warning("HotTier._insert_row failed: %s", exc)
        finally:
            if conn:
                conn.close()

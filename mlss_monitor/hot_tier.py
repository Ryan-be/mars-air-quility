from __future__ import annotations

from collections import deque
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlss_monitor.data_sources.base import NormalisedReading


class HotTier:
    """In-memory ring buffer of NormalisedReading objects.

    Thread-safe for single-writer / multiple-reader usage under CPython's GIL.
    deque.append() and reads via list() are atomic in CPython.
    """

    def __init__(self, maxlen: int = 3600) -> None:
        self._buffer: deque[NormalisedReading] = deque(maxlen=maxlen)

    def push(self, reading: NormalisedReading) -> None:
        self._buffer.append(reading)

    def latest(self) -> NormalisedReading | None:
        return self._buffer[-1] if self._buffer else None

    def size(self) -> int:
        return len(self._buffer)

    def last_n(self, n: int) -> list[NormalisedReading]:
        """Return the n most recent readings, oldest first."""
        buf = list(self._buffer)
        return buf[-n:] if n <= len(buf) else buf

    def last_minutes(self, minutes: float) -> list[NormalisedReading]:
        """Return all readings from the last `minutes` minutes, oldest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [r for r in self._buffer if r.timestamp >= cutoff]

    def snapshot(self) -> list[NormalisedReading]:
        """Return a full copy of the buffer contents, oldest first."""
        return list(self._buffer)

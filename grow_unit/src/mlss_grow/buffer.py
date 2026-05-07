"""Local SQLite buffer for telemetry + events when MLSS is unreachable.

When the WS client can't deliver, messages go here. On reconnect, the
client calls .peek_all() to see what's queued, sends each row, and
calls .delete(row_id) only after the send acks — so a mid-replay
disconnect leaves the un-sent tail in place for the next attempt.
(The older .pop_all() flow that deleted everything up front silently
dropped rows when the socket died mid-replay; see I2 fix.)

Disk-bounding (C2): two layers of defence so a permanently-down MLSS
or misconfigured server URL can't fill the SD card.

  1. Age-based prune via .prune(retention_days). Wired by ws_client to
     fire on every successful reconnect against the server's
     `grow_units.buffer_retention_days` value (default 7 days).

  2. Hard size caps via _DEFAULT_MAX_ROWS / _DEFAULT_MAX_BYTES, applied
     unconditionally inside .append() — these run regardless of whether
     prune is reachable. FIFO eviction: oldest rows dropped first, since
     newer telemetry has more diagnostic value than week-old already-
     stale data.

When the size caps trigger, the buffer fires the optional `on_eviction`
callback so the WSClient can emit a grow_errors event ("your unit
dropped data because the server was unreachable too long"). The callback
runs inside the commit flow — keep it fast and best-effort, callback
exceptions are swallowed rather than breaking the buffer.
"""
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


# Hard-cap defaults. Defence-in-depth against misconfigured/dead-server
# scenarios where the age-based prune never gets to run. The 100k-row /
# 50MB pair is sized for a Pi Zero W with a typical 16-32GB SD card —
# leaves plenty of headroom for OS + logs + photos. Override via
# LocalBuffer(max_rows=..., max_bytes=...) in tests.
_DEFAULT_MAX_ROWS = 100_000
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# Byte-cap is checked every N inserts rather than every insert — the
# SUM(LENGTH(body)) scan is O(rows) and would dominate the per-write
# cost on a Pi Zero. Row count is the cheap primary check; byte cap is
# a periodic ceiling.
_BYTE_CAP_CHECK_EVERY = 100


@dataclass
class BufferedRow:
    id: int
    msg_type: str
    body: str
    timestamp_utc: datetime


class LocalBuffer:
    def __init__(self, db_path: str, *,
                 max_rows: int = _DEFAULT_MAX_ROWS,
                 max_bytes: int = _DEFAULT_MAX_BYTES,
                 on_eviction: Optional[Callable[..., None]] = None) -> None:
        """Open (or create) the buffer DB at db_path.

        max_rows / max_bytes: hard caps that trigger FIFO eviction inside
        .append(). Tests pass small values to exercise the eviction path
        without writing 100k rows; production uses the defaults.

        on_eviction: optional callback fired when eviction kicks in.
        Called as on_eviction(reason="row_cap"|"byte_cap", evicted_count=N).
        Exceptions raised by the callback are caught and swallowed —
        a buggy callback must not break the buffer.
        """
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_type TEXT NOT NULL,
                body TEXT NOT NULL,
                timestamp_utc DATETIME NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_buffer_ts ON buffer(timestamp_utc)"
        )
        self._conn.commit()
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._on_eviction = on_eviction
        # Guard against on_eviction → append → _evict_if_over_cap →
        # on_eviction infinite recursion. The callback typically writes
        # an eviction-event row back into this buffer; without the guard,
        # that write would re-trigger the cap check (we just evicted to
        # leave headroom for it, but the recursion would still happen).
        self._eviction_in_progress = False

    def append(self, msg_type: str, body: str, ts: datetime) -> None:
        self._conn.execute(
            "INSERT INTO buffer (msg_type, body, timestamp_utc) VALUES (?, ?, ?)",
            (msg_type, body, ts),
        )
        self._conn.commit()
        self._evict_if_over_cap()

    def _evict_if_over_cap(self) -> None:
        """FIFO drop oldest rows when row count or byte size exceeds caps.

        Uses row count as the primary check (cheap COUNT(*) over an
        autoincrement PK) and only falls back to the LENGTH(body) SUM
        scan every _BYTE_CAP_CHECK_EVERY inserts. The byte-cap branch is
        a periodic ceiling, not a per-write guarantee — we accept brief
        excursions over the byte cap between checks rather than scanning
        the whole table on every append.

        Eviction policy is FIFO (oldest first) on the rationale that newer
        telemetry has more diagnostic value than week-old data that's
        already stale by the time the server comes back.
        """
        # Re-entry guard: the on_eviction callback typically appends an
        # eviction-event row back into this buffer, which would re-enter
        # _evict_if_over_cap. The eviction itself already deleted enough
        # rows to make room for the event; skip the recheck so the event
        # row isn't double-evicted (or worse, fires another on_eviction).
        if self._eviction_in_progress:
            return

        row_count = self._conn.execute(
            "SELECT COUNT(*) FROM buffer"
        ).fetchone()[0]

        if row_count > self._max_rows:
            excess = row_count - self._max_rows
            self._conn.execute(
                "DELETE FROM buffer WHERE id IN ("
                "  SELECT id FROM buffer ORDER BY id ASC LIMIT ?"
                ")",
                (excess,),
            )
            self._conn.commit()
            log.warning(
                "buffer evicted %d oldest rows (over row cap %d)",
                excess, self._max_rows,
            )
            self._fire_eviction("row_cap", excess)
            return

        # Byte cap: periodic check only. The SUM scan is the expensive
        # path — running it every insert would crater write throughput
        # on a Pi Zero. row_count % EVERY == 0 means rows 100, 200, ...
        # trigger the check; rows 1-99 skip it.
        if row_count == 0 or (row_count % _BYTE_CAP_CHECK_EVERY) != 0:
            return
        total_bytes = self._conn.execute(
            "SELECT COALESCE(SUM(LENGTH(body)), 0) FROM buffer"
        ).fetchone()[0]
        if total_bytes > self._max_bytes:
            # Drop oldest 10% to give some headroom — otherwise the next
            # 100 inserts would trigger another scan + eviction.
            excess = max(1, row_count // 10)
            self._conn.execute(
                "DELETE FROM buffer WHERE id IN ("
                "  SELECT id FROM buffer ORDER BY id ASC LIMIT ?"
                ")",
                (excess,),
            )
            self._conn.commit()
            log.warning(
                "buffer evicted %d oldest rows (over byte cap %d, total=%d)",
                excess, self._max_bytes, total_bytes,
            )
            self._fire_eviction("byte_cap", excess)

    def _fire_eviction(self, reason: str, evicted_count: int) -> None:
        """Best-effort eviction-event callback. Never raises.

        Sets _eviction_in_progress so re-entry into _evict_if_over_cap
        from inside the callback (which typically appends an event row)
        short-circuits — the eviction has already made room for that
        one extra row.
        """
        if self._on_eviction is None:
            return
        self._eviction_in_progress = True
        try:
            self._on_eviction(reason=reason, evicted_count=evicted_count)
        except Exception as exc:
            # Don't let a buggy callback break the buffer. The eviction
            # itself already committed; losing the notification is the
            # least bad outcome.
            log.warning("on_eviction callback failed: %s", exc)
        finally:
            self._eviction_in_progress = False

    def size(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM buffer").fetchone()[0]

    def peek_all(self) -> list[BufferedRow]:
        """Return all buffered rows in timestamp order WITHOUT deleting them.

        The replay protocol uses peek_all + delete(row_id) per success so
        a mid-replay disconnect doesn't lose unsent rows. See ws_client
        ._replay_buffer for the call site.
        """
        rows = self._conn.execute(
            "SELECT id, msg_type, body, timestamp_utc FROM buffer "
            "ORDER BY timestamp_utc ASC"
        ).fetchall()
        return [
            BufferedRow(
                id=r[0], msg_type=r[1], body=r[2],
                timestamp_utc=datetime.fromisoformat(r[3])
                if isinstance(r[3], str) else r[3]
            )
            for r in rows
        ]

    def delete(self, row_id: int) -> None:
        """Delete one buffered row by id. Idempotent — deleting a missing
        id is a no-op (no error raised), so a retried replay loop is safe."""
        self._conn.execute("DELETE FROM buffer WHERE id=?", (row_id,))
        self._conn.commit()

    def pop_all(self) -> list[BufferedRow]:
        """DEPRECATED. Returns all buffered rows in timestamp order, then
        clears the buffer.

        Kept only for backward compatibility — the new replay path uses
        peek_all + delete(id) so a mid-replay disconnect doesn't drop the
        unsent tail. New code must not call pop_all; remove once no
        callers remain.
        """
        rows = self.peek_all()
        self._conn.execute("DELETE FROM buffer")
        self._conn.commit()
        return rows

    def prune(self, retention_days: int, now: datetime | None = None) -> None:
        cutoff = (now or datetime.utcnow()) - timedelta(days=retention_days)
        self._conn.execute("DELETE FROM buffer WHERE timestamp_utc < ?", (cutoff,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

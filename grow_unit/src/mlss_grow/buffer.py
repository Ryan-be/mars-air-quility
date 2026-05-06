"""Local SQLite buffer for telemetry + events when MLSS is unreachable.

When the WS client can't deliver, messages go here. On reconnect, the
client calls .peek_all() to see what's queued, sends each row, and
calls .delete(row_id) only after the send acks — so a mid-replay
disconnect leaves the un-sent tail in place for the next attempt.
(The older .pop_all() flow that deleted everything up front silently
dropped rows when the socket died mid-replay; see I2 fix.)

Prune by age to bound disk use.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class BufferedRow:
    id: int
    msg_type: str
    body: str
    timestamp_utc: datetime


class LocalBuffer:
    def __init__(self, db_path: str) -> None:
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

    def append(self, msg_type: str, body: str, ts: datetime) -> None:
        self._conn.execute(
            "INSERT INTO buffer (msg_type, body, timestamp_utc) VALUES (?, ?, ?)",
            (msg_type, body, ts),
        )
        self._conn.commit()

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

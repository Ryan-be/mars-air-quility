"""Outbox storage helpers — enqueue, coalesce, drain.

The ``db_path`` fixture is provided by ``tests/conftest.py`` — see
its docstring for the list of DB_FILE references it patches.
"""
import sqlite3
import time
import gc

from mlss_monitor.backup import outbox


def _conn(db_path):
    return sqlite3.connect(db_path)


def test_enqueue_row_writes_entry(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=42)
        with _conn(db_path) as conn:
            rows = list(conn.execute("SELECT table_name, pk FROM outbox_changes"))
    finally:
        gc.collect()
    assert rows == [("sensor_data", "42")]


def test_enqueue_row_coalesces_on_duplicate(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=42)
            time.sleep(0.01)
            outbox.enqueue_row(conn, table="sensor_data", pk=42)
        with _conn(db_path) as conn:
            rows = list(conn.execute(
                "SELECT table_name, pk, first_seen_at, last_change_at "
                "FROM outbox_changes WHERE table_name='sensor_data' AND pk='42'"))
    finally:
        gc.collect()
    assert len(rows) == 1
    table, pk, first, last = rows[0]
    assert first < last  # last_change_at updated, first_seen_at preserved


def test_enqueue_blob_writes_entry(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path="/tmp/foo.jpg",
                target_key="unit_1/2026-05-18/123.jpg",
                sha256="abc123",
            )
        with _conn(db_path) as conn:
            rows = list(conn.execute(
                "SELECT kind, source_path, target_key, sha256 FROM outbox_blobs"))
    finally:
        gc.collect()
    assert rows == [("photo", "/tmp/foo.jpg", "unit_1/2026-05-18/123.jpg", "abc123")]


def test_enqueue_blob_is_idempotent_by_target_key(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_blob(conn, kind="photo", source_path="/a", target_key="k1", sha256="x")
            outbox.enqueue_blob(conn, kind="photo", source_path="/a", target_key="k1", sha256="x")
        with _conn(db_path) as conn:
            rows = list(conn.execute("SELECT COUNT(*) FROM outbox_blobs WHERE target_key='k1'"))
    finally:
        gc.collect()
    assert rows[0][0] == 1


def test_enqueue_blob_re_enqueue_refreshes_source_path_and_sha(db_path):
    """ON CONFLICT DO UPDATE: a second enqueue with the SAME target_key
    but different content (new source_path / sha256) overwrites the
    first enqueue. The latest bytes are what the worker should ship —
    without the UPDATE the stale-sha first blob would ship and the
    refreshed content would be dropped on the floor.

    ``first_seen_at`` stays anchored to the first enqueue so the
    queued-age metric stays meaningful (i.e. "this blob has been in
    the queue for N minutes" reports time-since-first-attempt, not
    time-since-latest-refresh)."""
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_blob(
                conn, kind="photo", source_path="/path/a",
                target_key="k1", sha256="sha-A",
            )
        # Snapshot first_seen_at so we can assert it survives.
        with _conn(db_path) as conn:
            first_at = conn.execute(
                "SELECT first_seen_at FROM outbox_blobs WHERE target_key='k1'"
            ).fetchone()[0]

        # Force a different ISO timestamp on the second enqueue.
        time.sleep(0.01)

        with _conn(db_path) as conn:
            outbox.enqueue_blob(
                conn, kind="photo", source_path="/path/b",
                target_key="k1", sha256="sha-B",
            )
        with _conn(db_path) as conn:
            rows = list(conn.execute(
                "SELECT source_path, sha256, first_seen_at FROM outbox_blobs "
                "WHERE target_key='k1'"
            ))
    finally:
        gc.collect()

    assert len(rows) == 1, "still exactly one entry — the UPDATE coalesced"
    source_path, sha256, first_seen_at = rows[0]
    assert source_path == "/path/b", "source_path refreshed to latest enqueue"
    assert sha256 == "sha-B", "sha256 refreshed to latest enqueue"
    assert first_seen_at == first_at, (
        "first_seen_at preserved — queued-age metric stays anchored to the "
        "FIRST enqueue, not the latest refresh"
    )


def test_enqueue_blob_peek_returns_refreshed_content(db_path):
    """End-to-end check via peek_blobs: a re-enqueue with new sha is
    visible to the worker as the new sha (not the original)."""
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_blob(
                conn, kind="photo", source_path="/path/old",
                target_key="k1", sha256="sha-OLD",
            )
            outbox.enqueue_blob(
                conn, kind="photo", source_path="/path/new",
                target_key="k1", sha256="sha-NEW",
            )
        with _conn(db_path) as conn:
            entries = outbox.peek_blobs(conn, limit=10)
    finally:
        gc.collect()

    assert len(entries) == 1
    assert entries[0]["target_key"] == "k1"
    assert entries[0]["sha256"] == "sha-NEW"
    assert entries[0]["source_path"] == "/path/new"


def test_peek_rows_returns_oldest_first(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=1)
            outbox.enqueue_row(conn, table="sensor_data", pk=2)
            outbox.enqueue_row(conn, table="inferences", pk=5)
        with _conn(db_path) as conn:
            batch = outbox.peek_rows(conn, limit=10)
    finally:
        gc.collect()
    pks = [(r["table_name"], r["pk"]) for r in batch]
    assert pks == [("sensor_data", "1"), ("sensor_data", "2"), ("inferences", "5")]


def test_peek_rows_respects_limit(db_path):
    try:
        with _conn(db_path) as conn:
            for i in range(20):
                outbox.enqueue_row(conn, table="sensor_data", pk=i)
        with _conn(db_path) as conn:
            batch = outbox.peek_rows(conn, limit=5)
    finally:
        gc.collect()
    assert len(batch) == 5


def test_delete_rows_by_id(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_row(conn, table="sensor_data", pk=1)
            outbox.enqueue_row(conn, table="sensor_data", pk=2)
            rows = outbox.peek_rows(conn, limit=10)
            outbox.delete_rows(conn, ids=[rows[0]["id"]])
        with _conn(db_path) as conn:
            remaining = outbox.peek_rows(conn, limit=10)
    finally:
        gc.collect()
    assert len(remaining) == 1
    assert remaining[0]["pk"] == "2"


def test_pending_count_rows(db_path):
    try:
        with _conn(db_path) as conn:
            for i in range(7):
                outbox.enqueue_row(conn, table="sensor_data", pk=i)
        with _conn(db_path) as conn:
            assert outbox.pending_count_rows(conn) == 7
    finally:
        gc.collect()


def test_pending_count_blobs(db_path):
    try:
        with _conn(db_path) as conn:
            for i in range(3):
                outbox.enqueue_blob(
                    conn, kind="photo", source_path=f"/a{i}",
                    target_key=f"k{i}", sha256="x")
        with _conn(db_path) as conn:
            assert outbox.pending_count_blobs(conn) == 3
    finally:
        gc.collect()

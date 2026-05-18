"""Outbox storage helpers — enqueue, coalesce, drain."""
import sqlite3
import tempfile
import time
import gc
from pathlib import Path
import pytest

from mlss_monitor.backup import outbox


@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


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

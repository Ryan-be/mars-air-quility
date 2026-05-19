"""@tee_to_outbox decorator — live write + outbox enqueue in one transaction.

Uses the shared ``db_path`` fixture from ``tests/conftest.py`` and adds
a ``test_t`` table on top for the decorator-target table — keeps the
decorator tests self-contained while sharing the DB_FILE plumbing.
"""
import sqlite3
import gc
import pytest

from mlss_monitor.backup.outbox import tee_to_outbox


@pytest.fixture
def db_path_with_test_t(db_path):  # noqa: F811 — pytest fixture override
    """Extends the shared ``db_path`` fixture with a ``test_t`` table
    for decorator unit tests. The decorator wraps writes to a single
    named table; ``test_t`` keeps these tests isolated from the live
    schema's replicated tables."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE test_t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.close()
    return db_path


def test_decorator_writes_live_and_outbox(db_path_with_test_t):
    db_path = db_path_with_test_t
    @tee_to_outbox(table="test_t", db_file=db_path)
    def save_thing(conn, value):
        cur = conn.execute("INSERT INTO test_t(v) VALUES (?)", (value,))
        return cur.lastrowid

    try:
        pk = save_thing("hello")
        conn = sqlite3.connect(db_path)
        live = conn.execute("SELECT v FROM test_t WHERE id=?", (pk,)).fetchone()
        outbox_entry = conn.execute(
            "SELECT table_name, pk FROM outbox_changes WHERE table_name='test_t'"
        ).fetchone()
        conn.close()
    finally:
        gc.collect()
    assert live == ("hello",)
    assert outbox_entry == ("test_t", str(pk))


def test_decorator_atomic_on_helper_exception(db_path_with_test_t):
    db_path = db_path_with_test_t
    @tee_to_outbox(table="test_t", db_file=db_path)
    def save_thing(conn, value):
        conn.execute("INSERT INTO test_t(v) VALUES (?)", (value,))
        raise RuntimeError("oops")

    try:
        with pytest.raises(RuntimeError):
            save_thing("hello")
        conn = sqlite3.connect(db_path)
        live_count = conn.execute("SELECT COUNT(*) FROM test_t").fetchone()[0]
        outbox_count = conn.execute("SELECT COUNT(*) FROM outbox_changes").fetchone()[0]
        conn.close()
    finally:
        gc.collect()
    assert live_count == 0
    assert outbox_count == 0


def test_decorator_coalesces_on_duplicate_pk(db_path_with_test_t):
    db_path = db_path_with_test_t
    @tee_to_outbox(table="test_t", db_file=db_path)
    def upsert_thing(conn, pk, value):
        conn.execute(
            "INSERT INTO test_t(id, v) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET v=excluded.v",
            (pk, value))
        return pk

    try:
        upsert_thing(1, "a")
        upsert_thing(1, "b")
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT pk FROM outbox_changes WHERE table_name='test_t'").fetchall()
        conn.close()
    finally:
        gc.collect()
    assert rows == [("1",)]  # one entry, coalesced


def test_decorator_returns_helper_return_value(db_path_with_test_t):
    db_path = db_path_with_test_t
    @tee_to_outbox(table="test_t", db_file=db_path)
    def save_thing(conn, value):
        cur = conn.execute("INSERT INTO test_t(v) VALUES (?)", (value,))
        return cur.lastrowid

    try:
        result = save_thing("hello")
    finally:
        gc.collect()
    assert isinstance(result, int)
    assert result > 0


def test_decorator_raises_value_error_when_helper_returns_none(db_path_with_test_t):
    db_path = db_path_with_test_t
    @tee_to_outbox(table="test_t", db_file=db_path)
    def bad_save(conn, value):
        conn.execute("INSERT INTO test_t(v) VALUES (?)", (value,))
        return None  # bug — forgot to return pk

    try:
        with pytest.raises(ValueError, match="returned None"):
            bad_save("hello")
        # And the rollback should leave the live row out too
        conn = sqlite3.connect(db_path)
        live_count = conn.execute("SELECT COUNT(*) FROM test_t").fetchone()[0]
        outbox_count = conn.execute("SELECT COUNT(*) FROM outbox_changes").fetchone()[0]
        conn.close()
    finally:
        gc.collect()
    assert live_count == 0  # rollback included the live row
    assert outbox_count == 0

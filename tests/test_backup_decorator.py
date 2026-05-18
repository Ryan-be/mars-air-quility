"""@tee_to_outbox decorator — live write + outbox enqueue in one transaction."""
import sqlite3
import tempfile
import gc
from pathlib import Path
import pytest

from mlss_monitor.backup.outbox import tee_to_outbox


@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    # Also create a test table so we can write to it
    conn = sqlite3.connect(tmp.name)
    conn.execute("CREATE TABLE test_t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.close()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


def test_decorator_writes_live_and_outbox(db_path):
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


def test_decorator_atomic_on_helper_exception(db_path):
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


def test_decorator_coalesces_on_duplicate_pk(db_path):
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


def test_decorator_returns_helper_return_value(db_path):
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


def test_decorator_raises_value_error_when_helper_returns_none(db_path):
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

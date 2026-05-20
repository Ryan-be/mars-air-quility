"""Outbox + bootstrap_progress tables created by init_db."""
import gc
import sqlite3
import tempfile
from pathlib import Path
import pytest


@pytest.fixture
def fresh_db():
    # NamedTemporaryFile must outlive this fixture; the temp file path is
    # yielded to the test and removed on teardown after the yield resumes.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=consider-using-with
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    yield tmp.name
    # Force any lingering sqlite3.Connection objects to be GC'd so Windows
    # releases its file handle before unlink. (No-op on POSIX.)
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


def test_outbox_changes_table_exists(fresh_db):
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(outbox_changes)")}
        expected = {"id", "table_name", "pk", "first_seen_at",
                    "last_change_at", "ship_attempts"}
        assert expected <= cols, f"missing: {expected - cols}"
        # UNIQUE(table_name, pk) — try inserting a duplicate and assert IntegrityError
        conn.execute(
            "INSERT INTO outbox_changes(table_name, pk, first_seen_at, last_change_at) "
            "VALUES ('foo', '1', '2026-01-01', '2026-01-01')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO outbox_changes(table_name, pk, first_seen_at, last_change_at) "
                "VALUES ('foo', '1', '2026-01-01', '2026-01-01')")
    finally:
        conn.close()


def test_outbox_blobs_table_exists(fresh_db):
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(outbox_blobs)")}
        expected = {"id", "kind", "source_path", "target_key",
                    "sha256", "first_seen_at", "ship_attempts"}
        assert expected <= cols
    finally:
        conn.close()


def test_bootstrap_progress_table_exists(fresh_db):
    conn = sqlite3.connect(fresh_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bootstrap_progress)")}
        expected = {"pipeline", "scope", "last_pk", "total_rows",
                    "started_at", "completed_at"}
        assert expected <= cols
    finally:
        conn.close()


def test_create_tables_is_idempotent(fresh_db):
    # Running create_db twice doesn't error
    import database.init_db as init_db
    init_db.create_db()
    init_db.create_db()  # second call: should be no-op via IF NOT EXISTS

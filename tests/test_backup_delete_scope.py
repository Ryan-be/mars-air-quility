"""Strict-mirror delete-scope outbox — for tables where the operator's
DELETE+INSERT replace pattern must propagate the delete to the server.

The ``db_path`` fixture is provided by ``tests/conftest.py``.
"""
import json
import sqlite3
import gc

from mlss_monitor.backup import outbox


def _conn(db_path):
    return sqlite3.connect(db_path)


def test_outbox_delete_scope_table_exists(db_path):
    try:
        with _conn(db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(outbox_delete_scope)")}
    finally:
        gc.collect()
    expected = {"id", "table_name", "scope_json", "first_seen_at", "ship_attempts"}
    assert expected <= cols, f"missing: {expected - cols}"


def test_enqueue_delete_scope_writes_entry(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_delete_scope(
                conn, table="incidents", scope={})
            outbox.enqueue_delete_scope(
                conn, table="grow_light_windows",
                scope={"unit_id": 3, "phase": "vegetative"})
        with _conn(db_path) as conn:
            rows = list(conn.execute(
                "SELECT table_name, scope_json FROM outbox_delete_scope ORDER BY id"))
    finally:
        gc.collect()
    assert len(rows) == 2
    assert rows[0][0] == "incidents"
    assert json.loads(rows[0][1]) == {}
    assert rows[1][0] == "grow_light_windows"
    assert json.loads(rows[1][1]) == {"unit_id": 3, "phase": "vegetative"}


def test_peek_delete_scope_returns_oldest_first(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_delete_scope(conn, table="incidents", scope={})
            outbox.enqueue_delete_scope(conn, table="grow_light_windows",
                                        scope={"unit_id": 1, "phase": "veg"})
        with _conn(db_path) as conn:
            batch = outbox.peek_delete_scope(conn, limit=10)
    finally:
        gc.collect()
    assert len(batch) == 2
    assert batch[0]["table_name"] == "incidents"
    assert batch[1]["table_name"] == "grow_light_windows"


def test_delete_delete_scope_by_id(db_path):
    try:
        with _conn(db_path) as conn:
            outbox.enqueue_delete_scope(conn, table="incidents", scope={})
            outbox.enqueue_delete_scope(conn, table="grow_light_windows",
                                        scope={"unit_id": 1})
            batch = outbox.peek_delete_scope(conn, limit=10)
            outbox.delete_delete_scope(conn, ids=[batch[0]["id"]])
        with _conn(db_path) as conn:
            remaining = outbox.peek_delete_scope(conn, limit=10)
    finally:
        gc.collect()
    assert len(remaining) == 1
    assert remaining[0]["table_name"] == "grow_light_windows"


def test_pending_count_delete_scope(db_path):
    try:
        with _conn(db_path) as conn:
            for i in range(3):
                outbox.enqueue_delete_scope(conn, table="incidents", scope={"i": i})
        with _conn(db_path) as conn:
            assert outbox.pending_count_delete_scope(conn) == 3
    finally:
        gc.collect()

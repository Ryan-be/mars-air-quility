"""Tests for notification_history pruning."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from database.init_db import create_db
from mlss_monitor.notifications import cleanup


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    monkeypatch.setattr(cleanup, "_db_file", lambda: str(db_path))
    create_db()
    # Seed a user (FK target).
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO users (github_username, created_at) "
        "VALUES ('alice', '2026-05-20T10:00:00Z')"
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"path": str(db_path), "user_id": user_id}


def _seed_row(db_path, user_id, created_at):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO notification_history "
        "(user_id, category, severity, title, body, deep_link, created_at) "
        "VALUES (?, 'air_quality', 'warning', 'X', 'Y', '/i', ?)",
        (user_id, created_at),
    )
    conn.commit()
    conn.close()


def test_prune_old_deletes_rows_past_cutoff(db):
    now = datetime.utcnow()
    _seed_row(db["path"], db["user_id"], (now - timedelta(days=10)).isoformat())  # keep
    _seed_row(db["path"], db["user_id"], (now - timedelta(days=29)).isoformat())  # keep
    _seed_row(db["path"], db["user_id"], (now - timedelta(days=31)).isoformat())  # delete
    _seed_row(db["path"], db["user_id"], (now - timedelta(days=60)).isoformat())  # delete

    deleted = cleanup.prune_old_notifications(days=30)
    assert deleted == 2

    conn = sqlite3.connect(db["path"])
    rows = conn.execute(
        "SELECT created_at FROM notification_history ORDER BY created_at"
    ).fetchall()
    conn.close()
    assert len(rows) == 2


def test_prune_old_empty_table_returns_zero(db):
    assert cleanup.prune_old_notifications(days=30) == 0


def test_prune_old_default_30_days(db):
    now = datetime.utcnow()
    _seed_row(db["path"], db["user_id"], (now - timedelta(days=31)).isoformat())
    assert cleanup.prune_old_notifications() == 1


def test_start_cleanup_loop_returns_thread(db):
    t = cleanup.start_cleanup_loop(interval_hours=24)
    assert t is not None
    assert t.daemon is True
    # Don't wait — it's a daemon and the test runner will tear it down.

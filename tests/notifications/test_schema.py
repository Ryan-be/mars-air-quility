"""Tests for notification schema migrations."""

import sqlite3

import pytest

from database.init_db import create_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    create_db()
    return db_path


def test_users_table_has_notify_columns(db):
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    conn.close()
    assert "notify_air_quality"      in cols
    assert "notify_grow_units"       in cols
    assert "notify_system_health"    in cols
    assert "notify_backup_pipeline"  in cols


def test_users_notify_defaults_are_warning(db):
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO users (github_username, created_at) "
        "VALUES ('alice', '2026-05-20T10:00:00Z')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT notify_air_quality, notify_grow_units, "
        "       notify_system_health, notify_backup_pipeline "
        "FROM users WHERE github_username = 'alice'"
    ).fetchone()
    conn.close()
    assert row == ("warning", "warning", "warning", "warning")


def test_push_subscriptions_table_exists(db):
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(push_subscriptions)"
    ).fetchall()}
    conn.close()
    assert cols == {"id", "user_id", "endpoint", "p256dh", "auth",
                    "device_label", "created_at", "last_used_at"}


def test_notification_history_table_exists(db):
    conn = sqlite3.connect(str(db))
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(notification_history)"
    ).fetchall()}
    conn.close()
    assert cols == {"id", "user_id", "category", "severity", "title",
                    "body", "deep_link", "event_count", "delivered_count",
                    "failed_count", "created_at", "read_at"}


def test_indexes_exist(db):
    conn = sqlite3.connect(str(db))
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    conn.close()
    assert "idx_push_sub_user"        in indexes
    assert "idx_notif_hist_user_time" in indexes


def test_migrations_idempotent(db):
    # Running create_db twice should not error.
    create_db()
    create_db()
    conn = sqlite3.connect(str(db))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert cols.count("notify_air_quality") == 1
    conn.close()

"""Tests for NotificationDispatcher startup wiring and grow_error publish."""

import sqlite3
import time

import pytest

from database.init_db import create_db
from mlss_monitor.event_bus import EventBus
from mlss_monitor.notifications.dispatcher import start_dispatcher


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    create_db()
    # Seed a user
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "INSERT INTO users (github_username, created_at) "
        "VALUES ('alice', '2026-05-20T10:00:00Z')"
    )
    user_id = cur.lastrowid
    conn.execute(
        "INSERT INTO push_subscriptions "
        "(user_id, endpoint, p256dh, auth, created_at) "
        "VALUES (?, 'https://push.example/u1', 'p', 'a', '2026-05-20T10:00:00Z')",
        (user_id,),
    )
    conn.commit()
    conn.close()
    return {"db": str(db_path), "user_id": user_id}


def test_start_dispatcher_returns_running_thread(env):
    bus = EventBus(max_history=10)
    d = start_dispatcher(bus, env["db"])
    assert d is not None
    assert d._thread is not None
    assert d._thread.is_alive()
    d.stop()
    d._thread.join(timeout=2)


def test_dispatcher_consumes_grow_error_published_to_bus(env, monkeypatch):
    # Mock push_client.send so we don't hit the network.
    sent = []
    def _fake_send(sub, payload, *_args, **_kw):
        sent.append((sub["endpoint"], payload["title"]))
        return type("R", (), {"delivered": True, "stale": False})()
    monkeypatch.setattr(
        "mlss_monitor.notifications.dispatcher.push_client.send",
        _fake_send,
    )

    bus = EventBus(max_history=10)
    d = start_dispatcher(bus, env["db"])
    try:
        bus.publish("grow_error_logged", {
            "unit_id": 3, "severity": "warning",
            "title": "Pump stuck on",
            "message": "Watering pump active for 600s",
        })
        # Give the subscriber thread a moment to process.
        for _ in range(40):
            if sent:
                break
            time.sleep(0.05)
    finally:
        d.stop()
        d._thread.join(timeout=2)

    assert len(sent) >= 1
    endpoint, title = sent[0]
    assert endpoint == "https://push.example/u1"
    assert "#3" in title and "Pump stuck on" in title

    # And a history row was persisted.
    conn = sqlite3.connect(env["db"])
    rows = conn.execute(
        "SELECT category, title, deep_link FROM notification_history"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "grow_units"
    assert rows[0][2] == "/grow/3"

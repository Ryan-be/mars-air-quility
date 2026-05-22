"""Tests for the NotificationDispatcher coalesce + fan-out logic."""

import sqlite3
import time
from unittest.mock import patch

import pytest

from database.init_db import create_db
from mlss_monitor.event_bus import EventBus
from mlss_monitor.notifications import dispatcher as disp_module
from mlss_monitor.notifications.dispatcher import NotificationDispatcher


def _add_user(db_path, username, severity_floors=None):
    floors = severity_floors or {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (github_username, created_at) "
        "VALUES (?, '2026-05-20T10:00:00Z')",
        (username,),
    )
    user_id = cur.lastrowid
    for cat, lvl in floors.items():
        cur.execute(
            f"UPDATE users SET notify_{cat} = ? WHERE id = ?",
            (lvl, user_id),
        )
    conn.commit()
    conn.close()
    return user_id


def _add_subscription(db_path, user_id, endpoint="https://push.example/u1"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO push_subscriptions "
        "(user_id, endpoint, p256dh, auth, created_at) "
        "VALUES (?, ?, 'p', 'a', '2026-05-20T10:00:00Z')",
        (user_id, endpoint),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MLSS_DB_FILE", str(db_path))
    from config import config as _config
    _config.reload()
    monkeypatch.setattr("database.init_db.DB_FILE", str(db_path))
    create_db()
    bus = EventBus(max_history=10)
    user_id = _add_user(str(db_path), "alice")
    _add_subscription(str(db_path), user_id)
    return {"db": str(db_path), "bus": bus, "user_id": user_id}


def _ok():
    return type("R", (), {"delivered": True, "stale": False})()


def _stale():
    return type("R", (), {"delivered": False, "stale": True})()


def test_dispatcher_writes_history_row_on_matching_event(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_ok()):
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "TVOC spike", "description": "..."
        }})
    conn = sqlite3.connect(env["db"])
    rows = conn.execute("SELECT category, severity, title, event_count "
                        "FROM notification_history").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == ("air_quality", "warning", "TVOC spike", 1)


def test_user_with_off_floor_does_not_receive(env):
    conn = sqlite3.connect(env["db"])
    conn.execute("UPDATE users SET notify_air_quality = 'off' "
                 "WHERE id = ?", (env["user_id"],))
    conn.commit()
    conn.close()

    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send") as mock_send:
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "x", "description": "y"
        }})
        assert mock_send.call_count == 0

    conn = sqlite3.connect(env["db"])
    rows = conn.execute("SELECT * FROM notification_history").fetchall()
    conn.close()
    assert len(rows) == 0


def test_severity_below_floor_does_not_receive(env):
    conn = sqlite3.connect(env["db"])
    conn.execute("UPDATE users SET notify_air_quality = 'critical' "
                 "WHERE id = ?", (env["user_id"],))
    conn.commit()
    conn.close()

    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send") as mock_send:
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "x", "description": "y"
        }})
        assert mock_send.call_count == 0


def test_coalesce_window_updates_existing_row(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_ok()):
        for _ in range(3):
            d._handle_event({"event": "inference_fired", "data": {
                "severity": "warning", "title": "TVOC spike", "description": "..."
            }})
    conn = sqlite3.connect(env["db"])
    rows = conn.execute(
        "SELECT title, event_count FROM notification_history"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][1] == 3
    assert rows[0][0].startswith("3×")


def test_stale_subscription_is_deleted(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_stale()):
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "x", "description": "y"
        }})
    conn = sqlite3.connect(env["db"])
    rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    conn.close()
    assert len(rows) == 0


def test_unmapped_event_is_ignored(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send") as mock_send:
        d._handle_event({"event": "sensor_update", "data": {}})
        assert mock_send.call_count == 0
    conn = sqlite3.connect(env["db"])
    rows = conn.execute("SELECT * FROM notification_history").fetchall()
    conn.close()
    assert len(rows) == 0


def test_window_expires_after_60s(env, monkeypatch):
    fake_now = [time.time()]
    monkeypatch.setattr(disp_module, "_now", lambda: fake_now[0])

    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_ok()):
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "spike", "description": "..."
        }})
        fake_now[0] += 61
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "spike again", "description": "..."
        }})
    conn = sqlite3.connect(env["db"])
    rows = conn.execute(
        "SELECT title, event_count FROM notification_history ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[0][1] == 1
    assert rows[1][1] == 1


def _fail():
    """Push failed but sub is *not* known-stale (e.g. 403, 500, network)."""
    return type("R", (), {"delivered": False, "stale": False})()


# ── 403 BadJwtToken hammering protection (regression) ──────────────────
#
# When APNs returned 403 BadJwtToken (e.g. the operator never set a
# contact email and the default ``mailto:admin@localhost`` was being
# rejected), the dispatcher kept retrying the same subscription every
# few seconds for hours, spamming journalctl. We now track consecutive
# failures per endpoint and treat the sub as stale after a threshold —
# but a single success resets the counter so transient errors (network
# flap, push-service 5xx) don't accidentally evict working devices.
def test_consecutive_failures_eventually_evict_subscription(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_fail()):
        for _i in range(disp_module._FAILURE_EVICT_THRESHOLD + 1):
            d._handle_event({"event": "inference_fired", "data": {
                "severity": "warning",
                "title": f"spike-{_i}",
                "description": "...",
            }})
    conn = sqlite3.connect(env["db"])
    subs = conn.execute("SELECT id FROM push_subscriptions").fetchall()
    conn.close()
    assert subs == [], (
        "Sub should have been evicted after "
        f"{disp_module._FAILURE_EVICT_THRESHOLD} consecutive failures, "
        "but still present in DB"
    )


def test_single_success_resets_failure_counter(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    threshold = disp_module._FAILURE_EVICT_THRESHOLD
    responses = [_fail()] * (threshold - 1) + [_ok()] + [_fail()] * 3
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               side_effect=responses):
        for i in range(len(responses)):
            d._handle_event({"event": "inference_fired", "data": {
                "severity": "warning",
                "title": f"event-{i}",
                "description": "...",
            }})
    # We had (threshold-1) fails, then a success (resets counter), then
    # 3 fails — we should NOT have crossed the threshold again.
    conn = sqlite3.connect(env["db"])
    subs = conn.execute("SELECT id FROM push_subscriptions").fetchall()
    conn.close()
    assert len(subs) == 1, (
        "Successful push must reset the consecutive-failure counter; "
        "subscription should still be present"
    )


def test_single_failure_does_not_evict(env):
    d = NotificationDispatcher(env["bus"], env["db"])
    with patch("mlss_monitor.notifications.dispatcher.push_client.send",
               return_value=_fail()):
        d._handle_event({"event": "inference_fired", "data": {
            "severity": "warning", "title": "spike", "description": "...",
        }})
    conn = sqlite3.connect(env["db"])
    subs = conn.execute("SELECT id FROM push_subscriptions").fetchall()
    conn.close()
    assert len(subs) == 1, (
        "A single failure must NOT evict a sub — transient errors "
        "(network flap, 5xx) need to be tolerated"
    )

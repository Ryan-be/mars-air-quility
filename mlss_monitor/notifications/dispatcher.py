"""NotificationDispatcher — subscribes to event_bus, fans out push.

Single background thread:
  1. Listen on event_bus.subscribe().
  2. For each event, look up the NotificationSpec via event_map.
  3. For each user with severity floor >= spec.severity, apply 60s
     coalesce window per (user_id, category, severity).
  4. Within an active window: update the existing notification_history
     row's title to "Nx ..." and event_count; only push to subscriptions
     that haven't been pushed yet this window.
  5. New window: insert a fresh history row, push to all subscriptions.
  6. Drop stale subscriptions (push_client returns stale=True for 410).
"""

import logging
import queue
import sqlite3
import threading
import time
from datetime import datetime

from mlss_monitor.event_bus import EventBus
from mlss_monitor.notifications import event_map, push_client, vapid

log = logging.getLogger(__name__)

_WINDOW_SECONDS = 60

_SEVERITY_ORDER = {"off": -1, "info": 0, "warning": 1, "critical": 2}

_CATEGORY_COLUMNS = {
    "air_quality":      "notify_air_quality",
    "grow_units":       "notify_grow_units",
    "system_health":    "notify_system_health",
    "backup_pipeline":  "notify_backup_pipeline",
}


def _now() -> float:
    """Indirection so tests can monkey-patch the clock."""
    return time.time()


class NotificationDispatcher:
    """Background subscriber that translates events into Web Push."""

    def __init__(self, event_bus: EventBus, db_file: str):
        self._bus = event_bus
        self._db_file = db_file
        self._windows: dict[tuple[int, str, str], dict] = {}
        self._windows_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="NotificationDispatcher"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        sub_queue = self._bus.subscribe(replay=False)
        log.info("NotificationDispatcher started")
        try:
            while not self._stop_event.is_set():
                try:
                    msg = sub_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                try:
                    self._handle_event(msg)
                except Exception as exc:  # pylint: disable=broad-except
                    log.warning("Dispatcher error on %s: %s",
                                msg.get("event"), exc)
        finally:
            self._bus.unsubscribe(sub_queue)
            log.info("NotificationDispatcher stopped")

    def _handle_event(self, msg: dict) -> None:
        spec = event_map.map_event(msg.get("event", ""), msg.get("data") or {})
        if spec is None:
            return

        users = self._users_for_category(spec.category, spec.severity)
        if not users:
            return

        for user_id in users:
            self._deliver_to_user(user_id, spec)

    def _users_for_category(self, category: str, severity: str) -> list[int]:
        col = _CATEGORY_COLUMNS.get(category)
        if col is None:
            return []
        sev_rank = _SEVERITY_ORDER.get(severity, 0)
        eligible_floors = [
            f for f, rank in _SEVERITY_ORDER.items()
            if 0 <= rank <= sev_rank
        ]
        if not eligible_floors:
            return []
        placeholders = ",".join("?" * len(eligible_floors))
        conn = sqlite3.connect(self._db_file)
        try:
            rows = conn.execute(
                f"SELECT id FROM users "
                f"WHERE is_active = 1 AND {col} IN ({placeholders})",
                eligible_floors,
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]

    def _deliver_to_user(self, user_id: int, spec) -> None:
        key = (user_id, spec.category, spec.severity)
        now = _now()
        with self._windows_lock:
            window = self._windows.get(key)
            if window is not None and (now - window["first_seen"]) > _WINDOW_SECONDS:
                window = None
            if window is None:
                history_id = self._insert_history(user_id, spec)
                window = {
                    "first_seen": now,
                    "count": 1,
                    "history_id": history_id,
                    "original_title": spec.title,
                    "pushed_endpoints": set(),
                }
                self._windows[key] = window
                push_title = spec.title
            else:
                window["count"] += 1
                push_title = f"{window['count']}× {window['original_title']}"
                self._update_history_title_count(
                    window["history_id"], push_title, window["count"],
                )

        self._push_fanout(user_id, spec, window, push_title)

    def _insert_history(self, user_id: int, spec) -> int:
        conn = sqlite3.connect(self._db_file)
        try:
            cur = conn.execute(
                "INSERT INTO notification_history "
                "(user_id, category, severity, title, body, deep_link, "
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, spec.category, spec.severity, spec.title,
                 spec.body, spec.deep_link, datetime.utcnow().isoformat()),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _update_history_title_count(self, history_id: int,
                                     title: str, count: int) -> None:
        conn = sqlite3.connect(self._db_file)
        try:
            conn.execute(
                "UPDATE notification_history SET title = ?, event_count = ? "
                "WHERE id = ?",
                (title, count, history_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_history_delivery(self, history_id: int,
                                  delivered: int, failed: int) -> None:
        conn = sqlite3.connect(self._db_file)
        try:
            conn.execute(
                "UPDATE notification_history "
                "SET delivered_count = delivered_count + ?, "
                "    failed_count    = failed_count    + ? "
                "WHERE id = ?",
                (delivered, failed, history_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _push_fanout(self, user_id: int, spec, window: dict,
                     push_title: str) -> None:
        conn = sqlite3.connect(self._db_file)
        try:
            subs = conn.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                "WHERE user_id = ?", (user_id,),
            ).fetchall()
        finally:
            conn.close()

        if not subs:
            return

        pub = vapid.get_public_key()
        priv = vapid.get_private_key()
        contact = vapid.get_contact_email()

        delivered = 0
        failed = 0
        for sub_id, endpoint, p256dh, auth in subs:
            if endpoint in window["pushed_endpoints"]:
                continue
            payload = {
                "title": push_title,
                "body":  spec.body,
                "url":   spec.deep_link,
                "icon":  "/static/icons/icon-192.png",
                "tag":   f"{spec.category}-{spec.severity}",
            }
            result = push_client.send(
                {"endpoint": endpoint, "p256dh": p256dh, "auth": auth},
                payload, pub, priv, contact,
            )
            if result.delivered:
                delivered += 1
                window["pushed_endpoints"].add(endpoint)
            else:
                failed += 1
                if result.stale:
                    self._delete_subscription(sub_id)
        self._update_history_delivery(
            window["history_id"], delivered, failed,
        )

    def _delete_subscription(self, sub_id: int) -> None:
        conn = sqlite3.connect(self._db_file)
        try:
            conn.execute("DELETE FROM push_subscriptions WHERE id = ?",
                         (sub_id,))
            conn.commit()
        finally:
            conn.close()


def start_dispatcher(event_bus: EventBus, db_file: str) -> NotificationDispatcher:
    d = NotificationDispatcher(event_bus, db_file)
    d.start()
    return d

"""
Lightweight in-process event bus for server-sent events.

Producers (sensor loop, inference engine, weather loop) call ``publish()``
to broadcast events.  SSE consumers call ``subscribe()`` to receive a
``queue.Queue`` that yields events in real time.

Thread-safe: multiple producers and consumers may operate concurrently.
"""

import itertools
import queue
import threading
from collections import deque


class EventBus:
    """Fan-out pub/sub bus backed by per-subscriber ``queue.Queue`` instances."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._history: deque[dict] = deque(maxlen=max_history)
        self._counter = itertools.count(1)

    # ── Public API ───────────────────────────────────────────────────────

    def subscribe(self, replay: bool = False) -> queue.Queue:
        """Add a new subscriber queue.  If *replay* is True the queue is
        pre-loaded with the most recent cached events."""
        sub_queue: queue.Queue = queue.Queue()
        with self._lock:
            if replay:
                for event in self._history:
                    sub_queue.put(event)
            self._subscribers.append(sub_queue)
        return sub_queue

    def unsubscribe(self, sub_queue: queue.Queue) -> None:
        """Remove a subscriber queue (idempotent)."""
        with self._lock:
            try:
                self._subscribers.remove(sub_queue)
            except ValueError:
                pass

    def publish(self, event: str, data: dict) -> None:
        """Broadcast an event to every current subscriber and store it in
        the rolling history."""
        msg = {
            "id": next(self._counter),
            "event": event,
            "data": data,
        }
        with self._lock:
            self._history.append(msg)
            for sub_queue in self._subscribers:
                sub_queue.put_nowait(msg)

    # ── Introspection ────────────────────────────────────────────────────

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def get_history(self, event_type: str | None = None) -> list[dict]:
        """Return a copy of the recent event history, optionally filtered."""
        with self._lock:
            events = list(self._history)
        if event_type is not None:
            events = [e for e in events if e["event"] == event_type]
        return events

"""Tests for the in-process event bus (publish / subscribe)."""

import threading
import time

from mlss_monitor.event_bus import EventBus


# ── Construction ─────────────────────────────────────────────────────────────

class TestEventBusConstruction:
    def test_new_bus_has_no_subscribers(self):
        bus = EventBus()
        assert bus.subscriber_count() == 0

    def test_max_history_defaults_to_fifty(self):
        bus = EventBus()
        assert bus.max_history == 50


# ── Subscribe / unsubscribe ──────────────────────────────────────────────────

class TestSubscription:
    def test_subscribe_returns_queue(self):
        bus = EventBus()
        queue = bus.subscribe()
        assert queue is not None
        assert bus.subscriber_count() == 1

    def test_unsubscribe_removes_queue(self):
        bus = EventBus()
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        assert bus.subscriber_count() == 0

    def test_unsubscribe_unknown_queue_is_noop(self):
        bus = EventBus()
        import queue as stdlib_queue
        bus.unsubscribe(stdlib_queue.Queue())  # not subscribed — should not raise

    def test_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert bus.subscriber_count() == 2
        bus.unsubscribe(q1)
        assert bus.subscriber_count() == 1
        bus.unsubscribe(q2)
        assert bus.subscriber_count() == 0


# ── Publish / receive ────────────────────────────────────────────────────────

class TestPublish:
    def test_single_subscriber_receives_event(self):
        bus = EventBus()
        queue = bus.subscribe()
        bus.publish("sensor_update", {"temp": 22.5})
        msg = queue.get(timeout=1)
        assert msg["event"] == "sensor_update"
        assert msg["data"]["temp"] == 22.5
        assert "id" in msg

    def test_multiple_subscribers_each_receive_event(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish("fan_status", {"state": "on"})
        assert q1.get(timeout=1)["event"] == "fan_status"
        assert q2.get(timeout=1)["event"] == "fan_status"

    def test_publish_adds_incrementing_id(self):
        bus = EventBus()
        queue = bus.subscribe()
        bus.publish("a", {})
        bus.publish("b", {})
        m1 = queue.get(timeout=1)
        m2 = queue.get(timeout=1)
        assert m2["id"] > m1["id"]

    def test_unsubscribed_queue_stops_receiving(self):
        bus = EventBus()
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        bus.publish("sensor_update", {"temp": 20})
        assert queue.empty()


# ── Event history (replay for late-joining clients) ──────────────────────────

class TestHistory:
    def test_history_stores_recent_events(self):
        bus = EventBus(max_history=5)
        for i in range(3):
            bus.publish("tick", {"i": i})
        history = bus.get_history()
        assert len(history) == 3
        assert history[0]["data"]["i"] == 0

    def test_history_is_bounded(self):
        bus = EventBus(max_history=3)
        for i in range(10):
            bus.publish("tick", {"i": i})
        history = bus.get_history()
        assert len(history) == 3
        # Oldest surviving should be i=7
        assert history[0]["data"]["i"] == 7

    def test_subscribe_with_replay_delivers_history_first(self):
        bus = EventBus(max_history=5)
        bus.publish("old", {"v": 1})
        bus.publish("old", {"v": 2})
        queue = bus.subscribe(replay=True)
        # Should receive 2 replayed events before any new ones
        m1 = queue.get(timeout=1)
        m2 = queue.get(timeout=1)
        assert m1["data"]["v"] == 1
        assert m2["data"]["v"] == 2

    def test_subscribe_without_replay_skips_history(self):
        bus = EventBus(max_history=5)
        bus.publish("old", {"v": 1})
        queue = bus.subscribe(replay=False)
        assert queue.empty()

    def test_get_history_by_event_type(self):
        bus = EventBus(max_history=10)
        bus.publish("sensor_update", {"temp": 22})
        bus.publish("fan_status", {"state": "on"})
        bus.publish("sensor_update", {"temp": 23})
        history = bus.get_history(event_type="sensor_update")
        assert len(history) == 2
        assert all(h["event"] == "sensor_update" for h in history)


# ── Thread safety ────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_publish_and_subscribe(self):
        bus = EventBus()
        queue = bus.subscribe()
        count = 100
        received = []

        def publisher():
            for i in range(count):
                bus.publish("tick", {"i": i})

        def consumer():
            for _ in range(count):
                msg = queue.get(timeout=5)
                received.append(msg["data"]["i"])

        t1 = threading.Thread(target=publisher)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert len(received) == count

    def test_subscribe_unsubscribe_during_publish(self):
        """Subscribe/unsubscribe concurrently with publishes — no crash."""
        bus = EventBus()
        stop = threading.Event()

        def churn():
            while not stop.is_set():
                q = bus.subscribe()
                time.sleep(0.001)
                bus.unsubscribe(q)

        def publish():
            for _ in range(50):
                bus.publish("tick", {})

        t1 = threading.Thread(target=churn)
        t2 = threading.Thread(target=publish)
        t1.start()
        t2.start()
        t2.join(timeout=10)
        stop.set()
        t1.join(timeout=10)
        # No assertion — just proving it doesn't deadlock or crash

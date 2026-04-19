"""Tests for the background poller + non-blocking read_pm() contract.

The PM sensor's serial read can block for up to ~11s per failed cycle
(3 attempts x 3s timeout + 2 x 1s sleeps). Running that on the 1 Hz
sensor loop or the 10s log loop would stall the hot tier and the SSE
sensor_update broadcast. These tests verify that the blocking read now
runs on a dedicated daemon thread and that all callers use a
lock-guarded, instant cache via get_cached_pm().
"""

from __future__ import annotations

import threading
import time

import sensor_interfaces.sb_components_pm_sensor as pm_mod
from sensor_interfaces.sb_components_pm_sensor import AirMonitoringHAT_PM


def _make_sensor() -> AirMonitoringHAT_PM:
    """Construct a sensor without opening the serial port.

    We bypass __init__'s side-effects only where needed. __init__ here is
    safe: it creates the executor and state but never touches the serial
    port (that happens lazily in _open()).
    """
    sensor = AirMonitoringHAT_PM(port="/dev/null", baudrate=9600, timeout=1)
    return sensor


def test_get_cached_pm_returns_none_when_cache_empty():
    sensor = _make_sensor()
    try:
        assert sensor.get_cached_pm() is None
    finally:
        sensor.stop_poller()


def test_get_cached_pm_returns_last_successful_read():
    sensor = _make_sensor()
    try:
        frame = {"pm1_0": 5, "pm2_5": 9, "pm10": 12}
        with sensor._cache_lock:
            sensor._cached_result = dict(frame)
            sensor._cached_monotonic_ts = time.monotonic()
        got = sensor.get_cached_pm()
        assert got == frame
        # Must be a copy — mutating the returned dict should not poison
        # the cache.
        got["pm2_5"] = 999
        again = sensor.get_cached_pm()
        assert again == frame
    finally:
        sensor.stop_poller()


def test_get_cached_pm_respects_max_age(monkeypatch):
    sensor = _make_sensor()
    try:
        fake_now = [1000.0]

        def fake_monotonic() -> float:
            return fake_now[0]

        monkeypatch.setattr(
            "sensor_interfaces.sb_components_pm_sensor.time.monotonic",
            fake_monotonic,
        )
        with sensor._cache_lock:
            sensor._cached_result = {"pm1_0": 1, "pm2_5": 2, "pm10": 3}
            sensor._cached_monotonic_ts = fake_monotonic()

        # Still fresh.
        assert sensor.get_cached_pm(max_age=5.0) is not None
        # Advance past the max_age.
        fake_now[0] += 10.0
        assert sensor.get_cached_pm(max_age=5.0) is None
        # Without max_age the stale entry is still returned.
        assert sensor.get_cached_pm() == {"pm1_0": 1, "pm2_5": 2, "pm10": 3}
    finally:
        sensor.stop_poller()


def test_poll_loop_populates_cache():
    sensor = _make_sensor()
    read_called = threading.Event()
    frame = {"pm1_0": 4, "pm2_5": 7, "pm10": 11}

    def fake_read_pm():
        read_called.set()
        return dict(frame)

    sensor.read_pm = fake_read_pm  # type: ignore[assignment]
    try:
        # Use a tiny interval so the loop ticks quickly. The poller waits
        # `interval` seconds before the first read.
        sensor.start_poller(interval=0.05)
        assert read_called.wait(timeout=1.5), "poller did not call read_pm"
        # Give the cache write a moment to happen after the read.
        deadline = time.monotonic() + 1.0
        cached = None
        while time.monotonic() < deadline:
            cached = sensor.get_cached_pm()
            if cached is not None:
                break
            time.sleep(0.01)
        assert cached == frame
    finally:
        sensor.stop_poller()
        if sensor._poller_thread is not None:
            sensor._poller_thread.join(timeout=1.0)
            assert not sensor._poller_thread.is_alive()


def test_poll_loop_survives_read_exception():
    sensor = _make_sensor()
    raised = threading.Event()
    call_count = {"n": 0}

    def fake_read_pm():
        call_count["n"] += 1
        raised.set()
        raise RuntimeError("boom")

    sensor.read_pm = fake_read_pm  # type: ignore[assignment]
    try:
        sensor.start_poller(interval=0.05)
        # The first read should be attempted within ~interval seconds.
        assert raised.wait(timeout=1.5)
        # Let the loop cycle a couple more times to prove it survived the
        # exception.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and call_count["n"] < 2:
            time.sleep(0.02)
        assert call_count["n"] >= 2
        assert sensor._poller_thread is not None
        assert sensor._poller_thread.is_alive()
    finally:
        sensor.stop_poller()
        if sensor._poller_thread is not None:
            sensor._poller_thread.join(timeout=1.0)
            assert not sensor._poller_thread.is_alive()


def test_restart_after_fork_replaces_executor_and_starts_fresh_poller():
    """Simulates the gunicorn post_fork path.

    In prod, preload_app=True runs __init__ + start_poller in the master;
    fork() then strands those threads. restart_after_fork() must swap the
    executor, reset the poller Event, and start a new daemon thread that
    actually fires read_pm().
    """
    sensor = _make_sensor()
    try:
        # Set up "master" state: start a poller with a fake read, then
        # capture identities so we can prove they're replaced.
        sensor.read_pm = lambda: {"pm1_0": 1, "pm2_5": 2, "pm10": 3}  # type: ignore[assignment]
        sensor.start_poller(interval=0.05)
        # Wait briefly so the original poller definitely got going.
        time.sleep(0.1)
        original_executor = sensor._executor
        original_stop_event = sensor._poller_stop
        original_thread = sensor._poller_thread

        # Now simulate post-fork restart.
        read_called = threading.Event()

        def fresh_read():
            read_called.set()
            return {"pm1_0": 10, "pm2_5": 20, "pm10": 30}

        sensor.read_pm = fresh_read  # type: ignore[assignment]
        sensor.restart_after_fork(interval=0.05)

        assert sensor._executor is not original_executor, (
            "executor must be a fresh instance so its worker thread exists"
        )
        assert sensor._poller_stop is not original_stop_event, (
            "stop Event must be replaced to avoid inheriting a set-state"
        )
        assert sensor._poller_thread is not original_thread, (
            "poller thread must be a fresh daemon thread"
        )
        assert sensor._poller_thread is not None
        assert sensor._poller_thread.is_alive()

        # Confirm the new poller actually ticks.
        assert read_called.wait(timeout=1.5), (
            "new poller did not invoke read_pm after restart_after_fork"
        )
        # And the cache reflects the post-restart frame.
        deadline = time.monotonic() + 1.0
        cached = None
        while time.monotonic() < deadline:
            cached = sensor.get_cached_pm()
            if cached == {"pm1_0": 10, "pm2_5": 20, "pm10": 30}:
                break
            time.sleep(0.02)
        assert cached == {"pm1_0": 10, "pm2_5": 20, "pm10": 30}
    finally:
        sensor.stop_poller()
        if sensor._poller_thread is not None:
            sensor._poller_thread.join(timeout=1.0)
            assert not sensor._poller_thread.is_alive()


def test_module_read_pm_is_nonblocking(monkeypatch):
    """The module-level read_pm() must not call the blocking serial read.

    Even if the underlying sensor.read_pm() would sleep 10s, read_pm()
    returns instantly because it now dispatches to get_cached_pm().
    """

    class FakeSensor:
        def __init__(self):
            self._cached_result = {"pm1_0": 1, "pm2_5": 2, "pm10": 3}
            self._cached_monotonic_ts = time.monotonic()
            self._cache_lock = threading.Lock()
            self.read_called = False

        def read_pm(self):
            # time.sleep would block catastrophically IF the module-level
            # read_pm() were still calling the blocking path. It must not.
            self.read_called = True
            time.sleep(10.0)

        def get_cached_pm(self, max_age=None):
            return dict(self._cached_result)

    fake = FakeSensor()
    monkeypatch.setattr(pm_mod, "_sensor", fake)

    start = time.monotonic()
    got = pm_mod.read_pm()
    elapsed = time.monotonic() - start

    assert got == {"pm1_0": 1, "pm2_5": 2, "pm10": 3}
    assert elapsed < 0.1, f"read_pm() took {elapsed:.3f}s — should be <100ms"
    assert not fake.read_called, "module read_pm() must not call blocking read_pm()"

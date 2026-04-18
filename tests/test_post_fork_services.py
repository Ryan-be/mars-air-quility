"""Post-fork hook rebuilds the asyncio driver + PM poller + background services.

With gunicorn `preload_app=True`, only the calling thread survives `fork()` —
every thread started at import time in the master (asyncio driver, PM poller,
PM executor) is dead in the worker. `gunicorn.conf.py::post_fork` is the only
code that runs between fork and serving the first request, so regressions
here are invisible until production.

We simulate the post-fork state by killing the driver + poller threads, then
invoke `post_fork` directly with a mock server/worker and assert every
rebuilt thread is alive and functional.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

# gunicorn is a prod-only dependency (not installed in local dev on Windows).
# Skip the whole module if it isn't importable — the post_fork regression is
# still covered in CI where gunicorn is available.
pytest.importorskip("gunicorn")


def _simulate_dead_fork(app_module):
    """Close the asyncio driver loop and stop the PM poller — mirrors
    the state a newly-forked gunicorn worker inherits."""
    # Stop the asyncio loop (driver thread exits shortly after).
    if app_module.thread_loop is not None and app_module.thread_loop.is_running():
        app_module.thread_loop.call_soon_threadsafe(app_module.thread_loop.stop)
        # Give the driver thread a chance to observe stop() and exit.
        stopped = threading.Event()

        def _wait_stopped():
            while app_module.thread_loop.is_running():
                threading.Event().wait(0.01)
            stopped.set()

        threading.Thread(target=_wait_stopped, daemon=True).start()
        stopped.wait(timeout=2)

    # Stop the PM poller if one is running; leave the sensor object so the
    # post_fork restart_after_fork path exercises the rebuild.
    pm_sensor = app_module.state.pm_sensor
    if pm_sensor is not None:
        pm_sensor.stop_poller()
        if pm_sensor._poller_thread is not None:
            pm_sensor._poller_thread.join(timeout=2)


def test_post_fork_restarts_pm_poller_and_driver(monkeypatch):
    """post_fork must (a) rebuild the asyncio driver, (b) restart the PM
    poller, and (c) clear-then-set the `_services_started` idempotency guard.

    This is the full H-severity regression test: if `gunicorn.conf.py` ever
    drops the PM restart or forgets to clear `_services_started`, the
    workers run with a silent pipeline — exactly the bug fixed in
    65d3336.
    """
    import mlss_monitor.app as app_module
    import gunicorn.conf as gconf

    # Stub out `_start_background_services` so the test doesn't start the
    # real sensor / log / weather threads (they would race with other tests
    # and hit the real DB). We only need to verify it's called with the
    # services-started Event cleared.
    start_calls = []
    services_started_snapshot = []

    def fake_start_services():
        # Capture the Event state at the moment of call so the assertion
        # can verify the clear() happened *before* the call.
        services_started_snapshot.append(app_module._services_started.is_set())
        start_calls.append(1)
        # Mirror the real function: mark services as started.
        app_module._services_started.set()

    monkeypatch.setattr(app_module, "_start_background_services", fake_start_services)

    # Ensure PM sensor stub exists so the restart_after_fork path runs. If
    # the real sensor couldn't initialise (no /dev/serial0 in CI), inject a
    # minimal stub that implements the restart API.
    if app_module.state.pm_sensor is None:
        stub = MagicMock()
        stub.restart_after_fork = MagicMock()
        monkeypatch.setattr(app_module.state, "pm_sensor", stub)

    # Mark services as already started so post_fork has to clear the guard.
    app_module._services_started.set()

    _simulate_dead_fork(app_module)

    # Sanity: the old loop should be stopped (not running).
    assert not app_module.thread_loop.is_running()

    # Invoke post_fork directly with mock server/worker.
    server = MagicMock()
    worker = MagicMock()
    worker.pid = 12345
    gconf.post_fork(server, worker)

    # (a) A fresh asyncio loop is running in a new driver thread.
    ready = threading.Event()

    async def _ping():
        ready.set()

    future = asyncio.run_coroutine_threadsafe(_ping(), app_module.thread_loop)
    future.result(timeout=2)
    assert ready.is_set(), "new asyncio driver thread did not execute coroutine"
    assert app_module.thread_loop.is_running()

    # (b) PM poller restart was attempted (either via real thread, or via
    # the stubbed restart_after_fork on a MagicMock).
    pm_sensor = app_module.state.pm_sensor
    assert pm_sensor is not None
    if isinstance(pm_sensor, MagicMock):
        pm_sensor.restart_after_fork.assert_called_once()
    else:
        assert pm_sensor._poller_thread is not None
        assert pm_sensor._poller_thread.is_alive(), "PM poller not restarted"

    # (c) _services_started was cleared before _start_background_services()
    # ran, and set once it finished.
    assert start_calls == [1], "_start_background_services called wrong # of times"
    assert services_started_snapshot == [False], (
        "post_fork did not clear _services_started before calling "
        "_start_background_services — idempotency guard will suppress the "
        "restart"
    )
    assert app_module._services_started.is_set()

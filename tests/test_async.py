"""
Tests for async fan control behaviour.

The app runs a single long-lived asyncio event loop inside a daemon thread
(thread_loop).  All smart-plug I/O must be dispatched through that loop via
asyncio.run_coroutine_threadsafe().  These tests verify:

  1. thread_loop is actually running and accepts real coroutines
  2. log_data() dispatches to thread_loop and never calls asyncio.run()
  3. log_data() is fire-and-forget (does NOT block on .result())
  4. control_fan() DOES block on .result() so the HTTP response is accurate
  5. get_fan_state() calls plug.update() first, then get_state()
  6. All three paths handle smart-plug exceptions without crashing the app
"""
import asyncio
import threading
from unittest.mock import MagicMock

import pytest

import mlss_monitor.state as app_state


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_future(return_value=None, raise_exc=None):
    """Return a mock concurrent.futures.Future."""
    f = MagicMock()
    if raise_exc:
        f.result.side_effect = raise_exc
    else:
        f.result.return_value = return_value
    return f


# ---------------------------------------------------------------------------
# 1. thread_loop integration
# ---------------------------------------------------------------------------

class TestThreadLoop:
    """The background event loop must be live and running in its own thread."""

    def test_loop_is_running(self):
        import mlss_monitor.app as app_module
        assert app_module.thread_loop.is_running()

    def test_loop_runs_in_different_thread(self):
        import mlss_monitor.app as app_module

        loop_thread_id = []

        async def capture():
            loop_thread_id.append(threading.current_thread().ident)

        future = asyncio.run_coroutine_threadsafe(capture(), app_module.thread_loop)
        future.result(timeout=2)

        assert loop_thread_id[0] != threading.current_thread().ident

    def test_loop_executes_coroutine_and_returns_value(self):
        import mlss_monitor.app as app_module

        async def add(a, b):
            return a + b

        future = asyncio.run_coroutine_threadsafe(add(2, 3), app_module.thread_loop)
        assert future.result(timeout=2) == 5

    def test_loop_handles_coroutine_exception(self):
        import mlss_monitor.app as app_module

        async def boom():
            raise ValueError("intentional")

        future = asyncio.run_coroutine_threadsafe(boom(), app_module.thread_loop)
        with pytest.raises(ValueError, match="intentional"):
            future.result(timeout=2)


# ---------------------------------------------------------------------------
# 2 & 3. log_data() dispatch behaviour
# ---------------------------------------------------------------------------

class TestLogDataAsyncDispatch:
    """log_data() must use run_coroutine_threadsafe, target thread_loop,
    and must NOT block the logging thread by calling .result()."""

    def _run(self, monkeypatch, db, temp=25.0, tvoc=600, enabled=True):
        import mlss_monitor.app as app_module
        from database.db_logger import update_fan_settings

        update_fan_settings(0, 500, 0.0, 20.0, enabled)
        monkeypatch.setattr(app_module, "read_sensors", lambda: (temp, 50, 300, tvoc))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)

        mock_future = MagicMock()
        threadsafe_calls = []

        def fake_threadsafe(coro, loop):
            threadsafe_calls.append({"coro": coro, "loop": loop})
            return mock_future

        monkeypatch.setattr(app_module.asyncio, "run_coroutine_threadsafe", fake_threadsafe)

        # asyncio.run() must never be touched — make it blow up if called
        monkeypatch.setattr(
            app_module.asyncio, "run",
            MagicMock(side_effect=AssertionError("log_data must not call asyncio.run()"))
        )

        app_module.log_data()
        return threadsafe_calls, mock_future

    def test_run_coroutine_threadsafe_is_called(self, db, monkeypatch):
        # get_power() + switch() = 2 calls when auto is enabled
        calls, _ = self._run(monkeypatch, db)
        assert len(calls) == 2

    def test_dispatches_to_thread_loop(self, db, monkeypatch):
        import mlss_monitor.app as app_module
        calls, _ = self._run(monkeypatch, db)
        assert all(c["loop"] is app_module.thread_loop for c in calls)

    def test_asyncio_run_is_never_called(self, db, monkeypatch):
        # Would raise AssertionError if asyncio.run() is invoked — test passes if silent
        self._run(monkeypatch, db)

    def test_get_power_result_is_awaited(self, db, monkeypatch):
        """get_power() blocks (with timeout) so watts can be stored; switch() is fire-and-forget."""
        _, mock_future = self._run(monkeypatch, db)
        # .result() is called at least once (for get_power)
        mock_future.result.assert_called()

    def test_no_switch_dispatch_when_auto_disabled(self, db, monkeypatch):
        # get_power() is always dispatched; switch() is NOT when auto is disabled
        calls, _ = self._run(monkeypatch, db, enabled=False)
        assert len(calls) == 1  # only get_power

    def test_switch_true_when_temp_over_max(self, db, monkeypatch):
        import mlss_monitor.app as app_module
        from database.db_logger import update_fan_settings
        update_fan_settings(0, 500, 0.0, 20.0, True)

        captured_coro_args = []
        mock_future = MagicMock()

        original_switch = app_state.fan_smart_plug.switch

        def spy_switch(state):
            captured_coro_args.append(state)
            return original_switch(state)

        monkeypatch.setattr(app_state.fan_smart_plug, "switch", spy_switch)
        monkeypatch.setattr(app_module, "read_sensors", lambda: (25.0, 50, 300, 100))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.asyncio, "run_coroutine_threadsafe", lambda coro, loop: mock_future)

        app_module.log_data()
        assert captured_coro_args == [True]

    def test_switch_false_when_below_thresholds(self, db, monkeypatch):
        import mlss_monitor.app as app_module
        from database.db_logger import update_fan_settings
        update_fan_settings(0, 500, 0.0, 20.0, True)

        captured_coro_args = []
        mock_future = MagicMock()
        original_switch = app_state.fan_smart_plug.switch

        def spy_switch(state):
            captured_coro_args.append(state)
            return original_switch(state)

        monkeypatch.setattr(app_state.fan_smart_plug, "switch", spy_switch)
        monkeypatch.setattr(app_module, "read_sensors", lambda: (15.0, 50, 300, 100))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.asyncio, "run_coroutine_threadsafe", lambda coro, loop: mock_future)

        app_module.log_data()
        assert captured_coro_args == [False]

    def test_plug_exception_does_not_crash_log_loop(self, db, monkeypatch):
        import mlss_monitor.app as app_module
        from database.db_logger import update_fan_settings
        update_fan_settings(0, 500, 0.0, 20.0, True)

        monkeypatch.setattr(app_module, "read_sensors", lambda: (25.0, 50, 300, 100))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(
            app_module.asyncio, "run_coroutine_threadsafe",
            MagicMock(side_effect=Exception("plug unreachable"))
        )

        # Must not raise — exceptions are caught inside log_data
        app_module.log_data()


# ---------------------------------------------------------------------------
# 4. control_fan() — must block on .result()
# ---------------------------------------------------------------------------

class TestControlFanAsyncDispatch:
    """The POST /api/fan endpoint must wait for the plug before responding."""

    def _mock_threadsafe(self, monkeypatch, return_value=None, raise_exc=None):
        import mlss_monitor.routes.api_fan as fan_module
        mock_future = _make_future(return_value=return_value, raise_exc=raise_exc)
        calls = []

        def fake(coro, loop):
            calls.append({"coro": coro, "loop": loop})
            return mock_future

        monkeypatch.setattr(fan_module.asyncio, "run_coroutine_threadsafe", fake)
        return calls, mock_future

    def test_result_called_for_manual_on(self, app_client, monkeypatch):
        client, _ = app_client
        _, mock_future = self._mock_threadsafe(monkeypatch)
        client.post("/api/fan?state=on")
        mock_future.result.assert_called_once()

    def test_result_called_for_manual_off(self, app_client, monkeypatch):
        client, _ = app_client
        _, mock_future = self._mock_threadsafe(monkeypatch)
        client.post("/api/fan?state=off")
        mock_future.result.assert_called_once()

    def test_auto_mode_never_calls_plug(self, app_client, monkeypatch):
        client, _ = app_client
        calls, mock_future = self._mock_threadsafe(monkeypatch)
        client.post("/api/fan?state=auto")
        assert len(calls) == 0
        mock_future.result.assert_not_called()  # auto mode must not touch the plug

    def test_dispatches_to_thread_loop(self, app_client, monkeypatch):
        client, _ = app_client
        calls, _ = self._mock_threadsafe(monkeypatch)
        client.post("/api/fan?state=on")
        assert calls[0]["loop"] is app_state.thread_loop

    def test_plug_timeout_returns_500(self, app_client, monkeypatch):
        client, _ = app_client
        self._mock_threadsafe(monkeypatch, raise_exc=TimeoutError("plug timed out"))
        res = client.post("/api/fan?state=on")
        assert res.status_code == 500
        assert "error" in res.get_json()

    def test_plug_network_error_returns_500(self, app_client, monkeypatch):
        client, _ = app_client
        self._mock_threadsafe(monkeypatch, raise_exc=Exception("connection refused"))
        res = client.post("/api/fan?state=on")
        assert res.status_code == 500

    def test_invalid_state_returns_400(self, app_client):
        client, _ = app_client
        res = client.post("/api/fan?state=broken")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# 5 & 6. get_fan_state() — correct sequence + error handling
# ---------------------------------------------------------------------------

class TestGetFanStateAsync:
    """GET /api/fan/status must call update() first, then get_state(),
    and handle errors from either call gracefully."""

    def _setup_status_mocks(self, monkeypatch, update_exc=None, state_exc=None,
                            state_value=None):
        import mlss_monitor.routes.api_fan as fan_module

        update_future = _make_future(raise_exc=update_exc)
        state_future = _make_future(
            return_value=state_value or {"ip_address": "192.168.1.63", "state": True},
            raise_exc=state_exc,
        )

        futures = iter([update_future, state_future])
        call_order = []

        def fake_threadsafe(_coro, _loop):
            f = next(futures)
            if f is update_future:
                call_order.append("update")
            else:
                call_order.append("get_state")
            return f

        monkeypatch.setattr(fan_module.asyncio, "run_coroutine_threadsafe", fake_threadsafe)
        return update_future, state_future, call_order

    def test_update_called_before_get_state(self, app_client, monkeypatch):
        client, _ = app_client
        _, _, order = self._setup_status_mocks(monkeypatch)
        client.get("/api/fan/status")
        assert order == ["update", "get_state"]

    def test_update_result_awaited(self, app_client, monkeypatch):
        """update() result must be awaited so state is fresh before reading."""
        client, _ = app_client
        update_fut, _, _ = self._setup_status_mocks(monkeypatch)
        client.get("/api/fan/status")
        update_fut.result.assert_called_once()

    def test_state_result_awaited(self, app_client, monkeypatch):
        client, _ = app_client
        _, state_fut, _ = self._setup_status_mocks(monkeypatch)
        client.get("/api/fan/status")
        state_fut.result.assert_called_once()

    def test_returns_plug_state(self, app_client, monkeypatch):
        client, _ = app_client
        self._setup_status_mocks(monkeypatch, state_value={"ip_address": "192.168.1.63", "state": True})
        res = client.get("/api/fan/status")
        assert res.status_code == 200
        assert res.get_json()["state"] is True

    def test_update_failure_returns_500(self, app_client, monkeypatch):
        client, _ = app_client
        import mlss_monitor.routes.api_fan as fan_module

        mock_future = _make_future(raise_exc=Exception("update failed"))
        monkeypatch.setattr(fan_module.asyncio, "run_coroutine_threadsafe",
                            lambda coro, loop: mock_future)

        res = client.get("/api/fan/status")
        assert res.status_code == 500
        assert "error" in res.get_json()

    def test_get_state_failure_returns_500(self, app_client, monkeypatch):
        client, _ = app_client
        update_fut = _make_future()
        state_fut = _make_future(raise_exc=Exception("state read failed"))
        futures = iter([update_fut, state_fut])

        import mlss_monitor.routes.api_fan as fan_module
        monkeypatch.setattr(fan_module.asyncio, "run_coroutine_threadsafe",
                            lambda coro, loop: next(futures))

        res = client.get("/api/fan/status")
        assert res.status_code == 500
        assert "error" in res.get_json()

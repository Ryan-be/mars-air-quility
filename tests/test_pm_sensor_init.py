"""Tests for `init_pm_sensor()` — probe + instantiate + optional poller start.

Pinned by the gunicorn double-poll bug fix: `init_pm_sensor()` is called at
module-import time in `mlss_monitor/app.py`, which runs in the gunicorn
master because `preload_app=True`. If the function started a poller thread,
that thread would survive into the master and race the worker's poller
(started in `post_fork`) for exclusive access to `/dev/serial0`. The
`start_poller_now` flag exists precisely so app.py can defer poller startup
to the worker; these tests pin that contract.

Also covers the bonus guard: a probe that fails to open the serial port
(EACCES / missing device) must NOT spin a poller, regardless of the
`start_poller_now` flag — otherwise journalctl fills with 1 Hz error logs
on Pis without dialout group membership.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import sensor_interfaces.sb_components_pm_sensor as pm_mod


def _patched_sensor(probe_return=None, open_raises=None):
    """Return a context manager that patches AirMonitoringHAT_PM so its
    constructor never touches real hardware and the probe behaves as
    requested.

    `probe_return` — value returned by the mocked `read_pm()` (probe).
    `open_raises` — if set, `_open()` raises this exception (simulating
                    EACCES / missing serial device).
    """
    sensor = MagicMock(spec=pm_mod.AirMonitoringHAT_PM)
    # The init function reads/writes these attributes directly on the
    # sensor — provide a real lock and assignable cache fields so the
    # `with sensor._cache_lock:` block works for the success path.
    import threading
    sensor._cache_lock = threading.Lock()
    sensor._cached_result = None
    sensor._cached_monotonic_ts = 0.0
    sensor._poller_thread = None

    if open_raises is not None:
        sensor._open.side_effect = open_raises
    sensor.read_pm.return_value = probe_return
    return sensor


def test_init_pm_sensor_without_poller_does_not_start_thread():
    """`start_poller_now=False` skips the poller — the worker starts it."""
    sensor = _patched_sensor(probe_return={"pm1_0": 1, "pm2_5": 2, "pm10": 3})
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor(start_poller_now=False)
    assert got is sensor, "init must return the sensor instance on probe success"
    sensor.start_poller.assert_not_called()


def test_init_pm_sensor_default_starts_poller():
    """Legacy callers (no kwarg) keep the historical poller-start behaviour."""
    sensor = _patched_sensor(probe_return={"pm1_0": 1, "pm2_5": 2, "pm10": 3})
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor()
    assert got is sensor
    sensor.start_poller.assert_called_once_with(interval=1.0)


def test_init_pm_sensor_failed_open_does_not_start_poller():
    """An EACCES / missing-port failure during `_open()` must return None
    AND never start the poller — otherwise the poller spins logging serial
    errors at 1 Hz forever on Pis without dialout group membership.
    """
    sensor = _patched_sensor(open_raises=PermissionError("EACCES"))
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor()
    assert got is None
    sensor.start_poller.assert_not_called()


def test_init_pm_sensor_failed_open_with_start_poller_now_false():
    """Same guard with the explicit `start_poller_now=False` path."""
    sensor = _patched_sensor(open_raises=OSError("no such device"))
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor(start_poller_now=False)
    assert got is None
    sensor.start_poller.assert_not_called()


def test_init_pm_sensor_warmup_returns_sensor_and_starts_poller():
    """The probe returning None (sensor open but warming up) is a legitimate
    case — the poller should still run so it picks up data when ready.
    """
    sensor = _patched_sensor(probe_return=None)
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor()
    assert got is sensor
    sensor.start_poller.assert_called_once_with(interval=1.0)


def test_init_pm_sensor_warmup_with_start_poller_now_false_does_not_start():
    """And the deferred-poller path still respects the kwarg even for warmup."""
    sensor = _patched_sensor(probe_return=None)
    with patch.object(pm_mod, "AirMonitoringHAT_PM", return_value=sensor):
        got = pm_mod.init_pm_sensor(start_poller_now=False)
    assert got is sensor
    sensor.start_poller.assert_not_called()

"""Capability emission at boot — health field reflects driver init outcome.

Phase 2 sense-only-mode requirement: when the Automation pHAT is not yet
powered (first deployment scenario), pump + light driver init either
returns a working object (no actuation yet → "untested") or raises
(→ "no_hardware"). Sensors that read successfully start at "connected";
sensors whose first read fails report "no_hardware" so the UI can grey
them out without an explicit toggle.

These tests exercise the small helpers in mlss_grow.service so the rest
of the boot sequence (which needs real hardware) doesn't have to run.
"""
from unittest.mock import MagicMock
import pytest


def test_try_init_with_health_returns_untested_when_init_succeeds():
    from mlss_grow.service import _try_init_with_health
    factory = MagicMock(return_value="ok-driver")
    driver, health = _try_init_with_health(factory, "pump")
    assert driver == "ok-driver"
    assert health == "untested"


def test_try_init_with_health_returns_no_hardware_when_init_raises():
    from mlss_grow.service import _try_init_with_health
    factory = MagicMock(side_effect=RuntimeError("HAT not detected"))
    driver, health = _try_init_with_health(factory, "pump")
    assert driver is None
    assert health == "no_hardware"


def test_read_with_health_returns_connected_when_first_read_ok():
    from mlss_grow.service import _read_with_health
    sensor = MagicMock()
    sensor.read.return_value = {"soil_moisture": 612, "soil_temp_c": 21.4}
    reading, health = _read_with_health(sensor, "soil_moisture")
    assert reading == {"soil_moisture": 612, "soil_temp_c": 21.4}
    assert health == "connected"


def test_read_with_health_returns_no_hardware_when_read_raises():
    from mlss_grow.service import _read_with_health
    sensor = MagicMock()
    sensor.read.side_effect = OSError("i2c bus error")
    reading, health = _read_with_health(sensor, "soil_moisture")
    assert reading is None
    assert health == "no_hardware"


def test_read_with_health_returns_no_hardware_when_read_returns_empty():
    """Sensor.read() returning {} signals all-bad-reads (see SeesawSoilSensor).
    Treat that as no_hardware so the UI shows a clear disconnected state."""
    from mlss_grow.service import _read_with_health
    sensor = MagicMock()
    sensor.read.return_value = {}
    reading, health = _read_with_health(sensor, "soil_moisture")
    assert health == "no_hardware"

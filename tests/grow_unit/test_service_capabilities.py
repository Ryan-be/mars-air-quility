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


def test_get_firmware_version_returns_package_version_when_installed():
    """Phase 3 diagnostics: capabilities envelope advertises firmware_version
    pulled from importlib.metadata so the Diagnostics tab shows what's
    actually running on the unit (rather than a hard-coded constant that
    drifts from pyproject.toml)."""
    from unittest.mock import patch
    with patch("mlss_grow.service.version", return_value="1.2.3") as mock_v:
        from mlss_grow.service import _get_firmware_version
        assert _get_firmware_version() == "1.2.3"
    mock_v.assert_called_with("mlss_grow")


def test_get_firmware_version_returns_dev_when_not_installed():
    """When running out of a checkout (e.g. poetry shell, pytest)
    importlib.metadata raises PackageNotFoundError. Fall back to "dev"
    so the Diagnostics tab has something printable rather than crashing
    the capabilities frame entirely."""
    from unittest.mock import patch
    from importlib.metadata import PackageNotFoundError
    with patch(
        "mlss_grow.service.version",
        side_effect=PackageNotFoundError("mlss_grow"),
    ):
        from mlss_grow.service import _get_firmware_version
        assert _get_firmware_version() == "dev"


def test_service_uptime_s_is_non_negative_and_monotonic():
    """uptime_s comes from time.monotonic() so it should always be
    non-negative and never go backward across calls."""
    from mlss_grow.service import _service_uptime_s
    a = _service_uptime_s()
    b = _service_uptime_s()
    assert a >= 0
    assert b >= a

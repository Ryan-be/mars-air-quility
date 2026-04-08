"""Tests for the BMP280 pressure sensor interface and integration."""

# Tests legitimately access the module-level _sensor global to control state
# pylint: disable=protected-access

from unittest.mock import MagicMock, PropertyMock

import database.db_logger as dbl


# ── Sensor interface unit tests ──────────────────────────────────────────────

class TestReadBmp280:
    """Tests for the read_bmp280() function."""

    def test_returns_none_when_sensor_is_none(self):
        from sensor_interfaces.bmp280 import read_bmp280
        import sensor_interfaces.bmp280 as mod
        original = mod._sensor
        mod._sensor = None
        try:
            pressure = read_bmp280()
            assert pressure is None
        finally:
            mod._sensor = original

    def test_returns_pressure_when_sensor_present(self):
        from sensor_interfaces.bmp280 import read_bmp280
        import sensor_interfaces.bmp280 as mod
        original = mod._sensor

        mock_sensor = MagicMock()
        mock_sensor.pressure = 1013.24
        mod._sensor = mock_sensor

        try:
            pressure = read_bmp280()
            assert pressure == 1013.2  # rounded to 1 decimal
        finally:
            mod._sensor = original

    def test_returns_none_on_read_exception(self):
        from sensor_interfaces.bmp280 import read_bmp280
        import sensor_interfaces.bmp280 as mod
        original = mod._sensor

        mock_sensor = MagicMock()
        type(mock_sensor).pressure = PropertyMock(side_effect=Exception("Read error"))
        mod._sensor = mock_sensor

        try:
            pressure = read_bmp280()
            assert pressure is None
        finally:
            mod._sensor = original
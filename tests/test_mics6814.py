"""Tests for the MICS6814 gas sensor interface and integration."""

from unittest.mock import MagicMock, patch

import pytest

import database.init_db as dbi
import database.db_logger as dbl


# ── Sensor interface unit tests ──────────────────────────────────────────────

class TestReadMics6814:
    """Tests for the read_mics6814() function."""

    def test_returns_none_tuple_when_sensor_is_none(self):
        from sensor_interfaces.mics6814 import read_mics6814
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor
        mod._sensor = None
        try:
            co, no2, nh3 = read_mics6814()
            assert co is None
            assert no2 is None
            assert nh3 is None
        finally:
            mod._sensor = original

    def test_returns_values_when_sensor_present(self):
        from sensor_interfaces.mics6814 import read_mics6814
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor

        mock_sensor = MagicMock()
        mock_reading = MagicMock()
        mock_reading.reducing = 1.234
        mock_reading.oxidising = 5.678
        mock_reading.nh3 = 9.012
        mock_sensor.read_all.return_value = mock_reading
        mod._sensor = mock_sensor

        try:
            co, no2, nh3 = read_mics6814()
            assert co == 1.23
            assert no2 == 5.68
            assert nh3 == 9.01
        finally:
            mod._sensor = original

    def test_returns_none_tuple_on_read_exception(self):
        from sensor_interfaces.mics6814 import read_mics6814
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor

        mock_sensor = MagicMock()
        mock_sensor.read_all.side_effect = OSError("I2C bus error")
        mod._sensor = mock_sensor

        try:
            co, no2, nh3 = read_mics6814()
            assert co is None
            assert no2 is None
            assert nh3 is None
        finally:
            mod._sensor = original


class TestInitMics6814:
    """Tests for the init_mics6814() function."""

    def test_returns_none_when_library_missing(self):
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor

        # Temporarily remove the mock to force a real ImportError path
        with patch.dict("sys.modules", {"mics6814": None}):
            # Reload forces re-import attempt
            import importlib
            importlib.reload(mod)
            result = mod.init_mics6814()
            assert result is None
            assert mod._sensor is None

        # Restore
        mod._sensor = original

    def test_returns_sensor_on_success(self):
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_reading = MagicMock()
        mock_reading.reducing = 1.0
        mock_reading.oxidising = 2.0
        mock_reading.nh3 = 3.0
        mock_instance.read_all.return_value = mock_reading
        mock_cls.return_value = mock_instance

        mock_lib = MagicMock()
        mock_lib.MICS6814 = mock_cls

        with patch.dict("sys.modules", {"mics6814": mock_lib}):
            result = mod.init_mics6814()
            assert result is mock_instance
            assert mod._sensor is mock_instance

        mod._sensor = original

    def test_returns_none_on_os_error(self):
        import sensor_interfaces.mics6814 as mod
        original = mod._sensor

        mock_cls = MagicMock(side_effect=OSError("No device"))
        mock_lib = MagicMock()
        mock_lib.MICS6814 = mock_cls

        with patch.dict("sys.modules", {"mics6814": mock_lib}):
            result = mod.init_mics6814()
            assert result is None
            assert mod._sensor is None

        mod._sensor = original


# ── Database integration tests ───────────────────────────────────────────────

class TestGasSensorDatabase:
    """Tests that gas sensor data is stored and retrieved correctly."""

    def test_log_sensor_data_with_gas_values(self, db):
        dbl.log_sensor_data(
            22.0, 50.0, 400, 100,
            gas_co=1.5, gas_no2=0.3, gas_nh3=2.1,
        )
        rows = dbl.get_sensor_data()
        assert len(rows) == 1
        row = rows[0]
        # gas_co, gas_no2, gas_nh3 are columns 12, 13, 14
        assert row[12] == 1.5
        assert row[13] == 0.3
        assert row[14] == 2.1

    def test_log_sensor_data_with_null_gas_values(self, db):
        dbl.log_sensor_data(22.0, 50.0, 400, 100)
        rows = dbl.get_sensor_data()
        assert len(rows) == 1
        row = rows[0]
        assert row[12] is None
        assert row[13] is None
        assert row[14] is None

    def test_gas_columns_exist_after_migration(self, db):
        """Verify the ALTER TABLE migrations add gas columns."""
        import sqlite3
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(sensor_data)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "gas_co" in columns
        assert "gas_no2" in columns
        assert "gas_nh3" in columns

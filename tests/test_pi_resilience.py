"""
Pi resilience tests — scenarios that are likely to occur on the real hardware.

These tests cover failure modes that are common on a Raspberry Pi:
  - I2C sensor errors (initialisation or mid-read failures)
  - Missing data/ directory on first run
  - DB re-initialisation on an existing database
  - /proc/uptime not present (non-Linux or permission issue)
  - Sensor reads returning 0 must not trigger the fan
  - An unhandled exception in log_data() killing the background thread
"""
import sqlite3
import pytest

import database.db_logger as dbl
import database.init_db as dbi
from database.db_logger import get_fan_settings, update_fan_settings


# ---------------------------------------------------------------------------
# Sensor initialisation and mid-read failures
# ---------------------------------------------------------------------------

class TestSensorFailures:
    def test_read_sensors_returns_zeros_when_both_sensors_none(self, monkeypatch):
        """Both sensors absent — read_sensors must return zeros, not raise."""
        import mlss_monitor.app as app_module
        monkeypatch.setattr(app_module, "aht20", None)
        monkeypatch.setattr(app_module, "sgp30", None)

        temp, hum, eco2, tvoc = app_module.read_sensors()

        assert temp == 0
        assert hum == 0
        assert eco2 == 0
        assert tvoc == 0

    def test_read_sensors_returns_zeros_when_aht20_raises(self, monkeypatch):
        """AHT20 I2C error mid-read — temperature/humidity must fall back to 0."""
        import mlss_monitor.app as app_module
        from unittest.mock import MagicMock

        mock_aht20 = MagicMock()
        monkeypatch.setattr(app_module, "aht20", mock_aht20)
        monkeypatch.setattr(app_module, "sgp30", None)
        monkeypatch.setattr(
            app_module, "read_aht20",
            MagicMock(side_effect=OSError("I2C read failed"))
        )

        temp, hum, _, _ = app_module.read_sensors()

        assert temp == 0
        assert hum == 0

    def test_read_sensors_returns_zeros_when_sgp30_raises(self, monkeypatch):
        """SGP30 I2C error mid-read — eco2/tvoc must fall back to 0."""
        import mlss_monitor.app as app_module
        from unittest.mock import MagicMock

        monkeypatch.setattr(app_module, "aht20", None)
        mock_sgp30 = MagicMock()
        monkeypatch.setattr(app_module, "sgp30", mock_sgp30)
        monkeypatch.setattr(
            app_module, "read_sgp30",
            MagicMock(side_effect=OSError("I2C read failed"))
        )

        _, _, eco2, tvoc = app_module.read_sensors()

        assert eco2 == 0
        assert tvoc == 0

    def test_read_sensors_partial_success_aht20_ok_sgp30_fails(self, monkeypatch):
        """AHT20 succeeds but SGP30 fails — temperature/humidity populated, gas zeroed."""
        import mlss_monitor.app as app_module
        from unittest.mock import MagicMock

        mock_aht20 = MagicMock()
        mock_sgp30 = MagicMock()
        monkeypatch.setattr(app_module, "aht20", mock_aht20)
        monkeypatch.setattr(app_module, "sgp30", mock_sgp30)
        monkeypatch.setattr(app_module, "read_aht20", MagicMock(return_value=(22.5, 55.0)))
        monkeypatch.setattr(
            app_module, "read_sgp30",
            MagicMock(side_effect=OSError("I2C timeout"))
        )

        temp, hum, eco2, tvoc = app_module.read_sensors()

        assert temp == 22.5
        assert hum == 55.0
        assert eco2 == 0
        assert tvoc == 0

    def test_log_data_does_not_crash_when_sensors_return_zeros(self, db, monkeypatch):
        """log_data must complete without exception when all sensor reads return 0."""
        import mlss_monitor.app as app_module

        monkeypatch.setattr(app_module, "read_sensors", lambda: (0, 0, 0, 0))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module, "_collect_health", lambda: {})
        monkeypatch.setattr(
            app_module.asyncio, "run_coroutine_threadsafe",
            lambda coro, loop: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        )

        # Must not raise
        app_module.log_data()


# ---------------------------------------------------------------------------
# Fan must not false-trigger when sensor reads return 0
# ---------------------------------------------------------------------------

class TestFanZeroValueEdgeCase:
    def test_fan_turns_off_when_sensors_return_zero(self, db, monkeypatch):
        """
        When sensors fail and return 0 the fan must turn OFF, not ON.
        0°C and 0 TVOC are both below any reasonable threshold.
        """
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state
        from unittest.mock import MagicMock

        update_fan_settings(0, 500, 0.0, 20.0, True)

        switch_args = []
        original_switch = app_state.fan_smart_plug.switch
        monkeypatch.setattr(
            app_state.fan_smart_plug, "switch",
            lambda state: switch_args.append(state) or original_switch(state)
        )
        monkeypatch.setattr(app_module, "read_sensors", lambda: (0.0, 0.0, 0, 0))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module, "_collect_health", lambda: {})
        monkeypatch.setattr(
            app_module.asyncio, "run_coroutine_threadsafe",
            lambda coro, loop: MagicMock()
        )

        app_module.log_data()

        assert switch_args == [False], "Fan must be off when sensor values are 0"

    def test_fan_on_overrides_zero_humidity(self, db, monkeypatch):
        """Temp above threshold with zero humidity — fan must still turn on."""
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state
        from unittest.mock import MagicMock

        update_fan_settings(0, 500, 0.0, 20.0, True)

        switch_args = []
        original_switch = app_state.fan_smart_plug.switch
        monkeypatch.setattr(
            app_state.fan_smart_plug, "switch",
            lambda state: switch_args.append(state) or original_switch(state)
        )
        monkeypatch.setattr(app_module, "read_sensors", lambda: (25.0, 0.0, 0, 0))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module, "_collect_health", lambda: {})
        monkeypatch.setattr(
            app_module.asyncio, "run_coroutine_threadsafe",
            lambda coro, loop: MagicMock()
        )

        app_module.log_data()

        assert switch_args == [True]


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

class TestDatabaseInit:
    def test_create_db_is_idempotent(self, db):
        """Running create_db() twice must not raise or corrupt the default row."""
        dbi.create_db()  # second call on existing DB

        settings = get_fan_settings()
        assert settings["enabled"] is False  # default preserved

    def test_create_db_does_not_duplicate_default_row(self, db):
        """Each call to create_db() must leave exactly one row in fan_settings."""
        dbi.create_db()
        dbi.create_db()

        conn = sqlite3.connect(dbl.DB_FILE)
        count = conn.execute("SELECT COUNT(*) FROM fan_settings").fetchone()[0]
        conn.close()

        assert count == 1

    def test_fan_settings_table_has_required_columns(self, db):
        conn = sqlite3.connect(dbl.DB_FILE)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fan_settings)")}
        conn.close()

        assert {"tvoc_min", "tvoc_max", "temp_min", "temp_max", "enabled"}.issubset(cols)

    def test_sensor_data_table_has_required_columns(self, db):
        conn = sqlite3.connect(dbl.DB_FILE)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sensor_data)")}
        conn.close()

        assert {"timestamp", "temperature", "humidity", "eco2", "tvoc", "annotation"}.issubset(cols)

    def test_create_db_fails_clearly_when_directory_missing(self, tmp_path, monkeypatch):
        missing = str(tmp_path / "nonexistent_dir" / "sensor_data.db")
        monkeypatch.setattr(dbi, "DB_FILE", missing)
        monkeypatch.setattr(dbl, "DB_FILE", missing)

        with pytest.raises(Exception):  # sqlite3.OperationalError
            dbi.create_db()


# ---------------------------------------------------------------------------
# system_health endpoint — Linux-specific paths
# ---------------------------------------------------------------------------

class TestSystemHealth:
    def test_returns_200_with_expected_fields(self, app_client):
        client, _ = app_client
        res = client.get("/system_health")

        assert res.status_code == 200
        data = res.get_json()
        for field in ("uptime", "cpu_usage", "memory_used", "memory_total", "memory_percent"):
            assert field in data, f"Missing field: {field}"

    def test_uptime_is_unknown_when_proc_uptime_missing(self, app_client, monkeypatch):
        """/proc/uptime does not exist on non-Linux — must fall back to 'Unknown'."""
        import mlss_monitor.routes.system as system_module

        monkeypatch.setattr(
            system_module.subprocess, "check_output",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("/proc/uptime"))
        )
        client, _ = app_client
        res = client.get("/system_health")

        assert res.get_json()["uptime"] == "Unknown"

    def test_sensor_status_unavailable_when_not_initialised(self, app_client, monkeypatch):
        """Sensors that failed to init must show UNAVAILABLE, not crash."""
        import mlss_monitor.state as app_state

        monkeypatch.setattr(app_state, "aht20", None)
        monkeypatch.setattr(app_state, "sgp30", None)
        client, _ = app_client
        res = client.get("/system_health")
        data = res.get_json()

        assert data["AHT20"] == "UNAVAILABLE"
        assert data["SGP30"] == "UNAVAILABLE"

    def test_sensor_status_ok_when_initialised(self, app_client, monkeypatch):
        import mlss_monitor.state as app_state
        from unittest.mock import MagicMock

        monkeypatch.setattr(app_state, "aht20", MagicMock())
        monkeypatch.setattr(app_state, "sgp30", MagicMock())
        client, _ = app_client
        res = client.get("/system_health")
        data = res.get_json()

        assert data["AHT20"] == "OK"
        assert data["SGP30"] == "OK"


# ---------------------------------------------------------------------------
# Background thread resilience
# ---------------------------------------------------------------------------

class TestBackgroundThreadResilience:
    def test_background_log_survives_log_data_exception(self, monkeypatch):
        import mlss_monitor.app as app_module

        log_calls = [0]

        def flaky_log_data():
            log_calls[0] += 1
            if log_calls[0] == 1:
                raise OSError("data/ directory missing")

        sleep_calls = [0]

        def stop_after_two_sleeps(_interval):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(app_module, "log_data", flaky_log_data)
        monkeypatch.setattr(app_module.time, "sleep", stop_after_two_sleeps)

        with pytest.raises(KeyboardInterrupt):
            app_module._background_log()  # pylint: disable=protected-access

        assert log_calls[0] == 2, "Second log_data() call must run despite first raising"

    def test_log_data_exception_propagates_to_caller(self, db, monkeypatch):
        """log_data() itself still raises on error — _background_log catches it."""
        import mlss_monitor.app as app_module

        monkeypatch.setattr(app_module, "read_sensors", lambda: (15.0, 50, 300, 100))
        monkeypatch.setattr(
            app_module, "log_sensor_data",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("data/ directory missing"))
        )

        with pytest.raises(OSError, match="data/ directory missing"):
            app_module.log_data()

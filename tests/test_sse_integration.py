"""Integration tests: verify backend loops publish all SSE event types."""
# pylint: disable=redefined-outer-name,protected-access

from unittest.mock import MagicMock

import pytest

from mlss_monitor.event_bus import EventBus
from conftest import fake_sensors


@pytest.fixture
def bus():
    return EventBus()


class TestSensorLoopPublishesHealth:
    """log_data() should publish both sensor_update and health_update."""

    def test_log_data_publishes_health_update(self, db, monkeypatch, bus):
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state

        monkeypatch.setattr(app_state, "event_bus", bus)
        monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
        monkeypatch.setattr(app_state, "fan_mode", "manual")

        # Stub sensors to return known values
        monkeypatch.setattr(app_module, "read_sensors", lambda: fake_sensors(22.0, 55.0, 600, 100))

        # Stub smart plug power
        mock_future = MagicMock()
        mock_future.result.return_value = {"power_w": 4.2}
        monkeypatch.setattr("asyncio.run_coroutine_threadsafe", lambda *a, **k: mock_future)

        # Stub _collect_health so we don't need real psutil
        monkeypatch.setattr(app_module, "_collect_health", lambda: {
            "AHT20": "OK", "SGP30": "OK", "smart_plug": "UNAVAILABLE",
            "cpu_usage": "12.5%", "memory_percent": "50.0%",
        })

        app_module.log_data()

        events = bus.get_history()
        event_types = [e["event"] for e in events]

        assert "sensor_update" in event_types
        assert "health_update" in event_types

        health = next(e for e in events if e["event"] == "health_update")
        assert "cpu_usage" in health["data"]
        assert "memory_percent" in health["data"]

    def test_health_update_not_published_when_bus_is_none(self, db, monkeypatch):
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state

        monkeypatch.setattr(app_state, "event_bus", None)
        monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
        monkeypatch.setattr(app_state, "fan_mode", "manual")
        monkeypatch.setattr(app_module, "read_sensors", lambda: fake_sensors(22.0, 55.0, 600, 100))
        mock_future = MagicMock()
        mock_future.result.return_value = {"power_w": 0}
        monkeypatch.setattr("asyncio.run_coroutine_threadsafe", lambda *a, **k: mock_future)

        # Should not raise even with no event bus
        app_module.log_data()


class TestWeatherLoopPublishesForecasts:
    """_weather_log_loop should publish weather, forecast, and daily forecast."""

    def test_publish_weather_and_forecasts(self, db, monkeypatch, bus):
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state
        monkeypatch.setattr(app_state, "event_bus", bus)

        # Set up a location so weather loop has coords
        from database.db_logger import save_location
        save_location(53.7, -1.5, "Leeds")

        mock_weather = {
            "temp": 10.0, "humidity": 80, "feels_like": 8.0,
            "wind_speed": 12.0, "weather_code": 3, "uv_index": 2,
        }
        mock_forecast = {"hours": [{"time": "14:00", "temp": 11.0}]}
        mock_daily = {"days": [{"date": "2026-03-28", "temp_max": 13.0}]}

        mock_client = MagicMock()
        mock_client.get_current_weather.return_value = mock_weather
        mock_client.get_forecast.return_value = mock_forecast
        mock_client.get_daily_forecast.return_value = mock_daily
        monkeypatch.setattr(app_state, "open_meteo", mock_client)

        # Call the single-iteration weather function
        app_module._weather_log_once()

        events = bus.get_history()
        event_types = [e["event"] for e in events]

        assert "weather_update" in event_types
        assert "forecast_update" in event_types
        assert "daily_forecast_update" in event_types

        forecast_event = next(e for e in events if e["event"] == "forecast_update")
        assert "hours" in forecast_event["data"]

        daily_event = next(e for e in events if e["event"] == "daily_forecast_update")
        assert "days" in daily_event["data"]

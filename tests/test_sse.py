"""Tests for the Server-Sent Events (SSE) streaming endpoint."""
# pylint: disable=redefined-outer-name

import json
from unittest.mock import MagicMock

import pytest

from mlss_monitor.event_bus import EventBus


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def sse_app(db, monkeypatch, bus):
    """Flask test app wired to a fresh EventBus."""
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state

    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "event_bus", bus)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:  # pylint: disable=contextmanager-generator-missing-cleanup
            sess["logged_in"] = True
            sess["user"] = "test-admin"
            sess["user_role"] = "admin"
            sess["user_id"] = None
        yield client, bus


class TestSSEEndpoint:
    def test_stream_content_type(self, sse_app):
        client, bus = sse_app
        # Publish an event so the stream has something to yield
        bus.publish("sensor_update", {"temp": 22})
        resp = client.get("/api/stream")
        assert resp.content_type.startswith("text/event-stream")

    def test_stream_receives_published_event(self, sse_app):
        client, bus = sse_app

        # Publish before connecting — will be replayed
        bus.publish("sensor_update", {"temp": 22.5, "humidity": 55})

        resp = client.get("/api/stream")
        # Flask test client buffers the full response for streamed responses
        raw = resp.get_data(as_text=True)

        # SSE format: "id: ...\nevent: ...\ndata: ...\n\n"
        assert "event: sensor_update" in raw
        assert '"temp": 22.5' in raw

    def test_stream_sends_multiple_event_types(self, sse_app):
        client, bus = sse_app

        bus.publish("sensor_update", {"temp": 20})
        bus.publish("fan_status", {"state": "on"})
        bus.publish("inference_fired", {"title": "TVOC spike"})

        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)

        assert "event: sensor_update" in raw
        assert "event: fan_status" in raw
        assert "event: inference_fired" in raw

    def test_stream_sse_format_has_id_event_data(self, sse_app):
        client, bus = sse_app
        bus.publish("test_event", {"key": "value"})

        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)

        lines = raw.strip().split("\n")
        # Expect at least: id line, event line, data line
        id_lines = [ln for ln in lines if ln.startswith("id:")]
        event_lines = [ln for ln in lines if ln.startswith("event:")]
        data_lines = [ln for ln in lines if ln.startswith("data:")]

        assert len(id_lines) >= 1
        assert len(event_lines) >= 1
        assert len(data_lines) >= 1

        # Data should be valid JSON
        data_payload = data_lines[0].split("data: ", 1)[1]
        parsed = json.loads(data_payload)
        assert parsed["key"] == "value"

    def test_stream_requires_auth(self, db, monkeypatch):
        """Unauthenticated requests should be redirected to login."""
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state

        monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
        monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())

        # Ensure auth is "configured" so the middleware kicks in
        monkeypatch.setattr(app_state, "github_oauth", MagicMock())

        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            # Don't set session — unauthenticated
            resp = client.get("/api/stream")
            # API routes return JSON 401 (not a browser redirect) for unauthenticated requests
            assert resp.status_code == 401
            data = resp.get_json()
            assert data is not None
            assert data.get("login_required") is True


class TestSSEAllEventTypes:
    """Verify that all event types expected by the frontend are published."""

    def test_stream_receives_health_update(self, sse_app):
        client, bus = sse_app
        bus.publish("health_update", {
            "AHT20": "OK", "SGP30": "OK", "smart_plug": "OK",
            "cpu_usage": "12.3%", "memory_percent": "45.0%",
        })
        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)
        assert "event: health_update" in raw
        assert '"AHT20": "OK"' in raw

    def test_stream_receives_forecast_update(self, sse_app):
        client, bus = sse_app
        bus.publish("forecast_update", {
            "hours": [{"time": "14:00", "temp": 12.5}],
        })
        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)
        assert "event: forecast_update" in raw
        assert '"time": "14:00"' in raw

    def test_stream_receives_daily_forecast_update(self, sse_app):
        client, bus = sse_app
        bus.publish("daily_forecast_update", {
            "days": [{"date": "2026-03-28", "temp_max": 15.0}],
        })
        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)
        assert "event: daily_forecast_update" in raw
        assert '"date": "2026-03-28"' in raw

    def test_all_event_types_delivered_to_single_stream(self, sse_app):
        """All 6 event types arrive on a single SSE connection."""
        client, bus = sse_app
        bus.publish("sensor_update", {"temp": 22})
        bus.publish("fan_status", {"state": "on"})
        bus.publish("inference_fired", {"title": "spike"})
        bus.publish("weather_update", {"temp": 10})
        bus.publish("health_update", {"cpu_usage": "5%"})
        bus.publish("forecast_update", {"hours": []})
        bus.publish("daily_forecast_update", {"days": []})

        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)
        for event_type in ("sensor_update", "fan_status", "inference_fired",
                           "weather_update", "health_update",
                           "forecast_update", "daily_forecast_update"):
            assert f"event: {event_type}" in raw, f"Missing {event_type}"


class TestSSEHistory:
    def test_history_endpoint_returns_json(self, sse_app):
        client, bus = sse_app
        bus.publish("sensor_update", {"temp": 22})
        bus.publish("fan_status", {"state": "off"})

        resp = client.get("/api/stream/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_history_filtered_by_event_type(self, sse_app):
        client, bus = sse_app
        bus.publish("sensor_update", {"temp": 22})
        bus.publish("fan_status", {"state": "off"})
        bus.publish("sensor_update", {"temp": 23})

        resp = client.get("/api/stream/history?event=sensor_update")
        data = resp.get_json()
        assert len(data) == 2
        assert all(d["event"] == "sensor_update" for d in data)

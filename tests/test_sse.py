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
        with client.session_transaction() as sess:
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
        bus.publish("inference_event", {"title": "TVOC spike"})

        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)

        assert "event: sensor_update" in raw
        assert "event: fan_status" in raw
        assert "event: inference_event" in raw

    def test_stream_sse_format_has_id_event_data(self, sse_app):
        client, bus = sse_app
        bus.publish("test_event", {"key": "value"})

        resp = client.get("/api/stream")
        raw = resp.get_data(as_text=True)

        lines = raw.strip().split("\n")
        # Expect at least: id line, event line, data line
        id_lines = [l for l in lines if l.startswith("id:")]
        event_lines = [l for l in lines if l.startswith("event:")]
        data_lines = [l for l in lines if l.startswith("data:")]

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
            # Should redirect to login
            assert resp.status_code == 302


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

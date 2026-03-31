"""Tests for fan settings DB layer and Flask API endpoints."""
import sqlite3
from unittest.mock import MagicMock

import database.db_logger as dbl
from database.db_logger import get_fan_settings, update_fan_settings
from conftest import fake_sensors


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------

class TestGetFanSettings:
    def test_defaults_disabled(self, db):
        s = get_fan_settings()
        assert s["enabled"] is False

    def test_default_thresholds(self, db):
        s = get_fan_settings()
        assert s["tvoc_max"] == 500
        assert s["temp_max"] == 20.0

    def test_returns_dict_when_table_empty(self, db, monkeypatch):
        # Wipe all rows to simulate missing seed data
        conn = sqlite3.connect(dbl.DB_FILE)
        conn.execute("DELETE FROM fan_settings")
        conn.commit()
        conn.close()
        s = get_fan_settings()
        assert s["enabled"] is False  # safe fallback, no crash


class TestUpdateFanSettings:
    def test_enable_and_read_back(self, db):
        update_fan_settings(0, 800, 0.0, 25.0, True)
        s = get_fan_settings()
        assert s["enabled"] is True
        assert s["tvoc_max"] == 800
        assert s["temp_max"] == 25.0

    def test_disable_and_read_back(self, db):
        update_fan_settings(0, 500, 0.0, 20.0, True)
        update_fan_settings(0, 500, 0.0, 20.0, False)
        assert get_fan_settings()["enabled"] is False

    def test_all_fields_persisted(self, db):
        update_fan_settings(50, 700, 10.0, 28.5, True)
        s = get_fan_settings()
        assert s["tvoc_min"] == 50
        assert s["tvoc_max"] == 700
        assert s["temp_min"] == 10.0
        assert s["temp_max"] == 28.5


# ---------------------------------------------------------------------------
# Flask API
# ---------------------------------------------------------------------------

class TestFanSettingsAPI:
    def test_get_returns_defaults(self, app_client):
        client, _ = app_client
        res = client.get("/api/fan/settings")
        assert res.status_code == 200
        data = res.get_json()
        assert data["enabled"] is False
        assert data["tvoc_max"] == 500

    def test_post_persists_settings(self, app_client):
        client, _ = app_client
        payload = {"tvoc_min": 0, "tvoc_max": 600, "temp_min": 0.0, "temp_max": 22.0, "enabled": True}
        res = client.post("/api/fan/settings", json=payload)
        assert res.status_code == 200
        assert "updated" in res.get_json()["message"].lower()

        # Verify the GET reflects the change
        data = client.get("/api/fan/settings").get_json()
        assert data["enabled"] is True
        assert data["tvoc_max"] == 600
        assert data["temp_max"] == 22.0

    def test_admin_page_renders(self, app_client):
        client, _ = app_client
        res = client.get("/admin")
        assert res.status_code == 200
        assert b"Settings" in res.data


# ---------------------------------------------------------------------------
# Auto fan trigger logic
# ---------------------------------------------------------------------------

class TestLogDataAutoFan:
    def _run_log_data(self, monkeypatch, temp, tvoc):
        import mlss_monitor.app as app_module
        import mlss_monitor.state as app_state

        mock_plug = MagicMock()
        # get_power() future returns a dict so fan_power_w resolves cleanly
        power_future = MagicMock()
        power_future.result.return_value = {"power_w": None, "today_kwh": None}
        monkeypatch.setattr(app_state, "fan_smart_plug", mock_plug)
        monkeypatch.setattr(app_module, "read_sensors", lambda: fake_sensors(temp, 50, 300, tvoc))
        monkeypatch.setattr(app_module, "log_sensor_data", lambda *a, **kw: None)
        monkeypatch.setattr(app_module, "_collect_health", lambda: {})

        captured = []
        def fake_threadsafe(coro, loop):
            captured.append(coro)
            return power_future  # same mock is fine; switch() doesn't call .result()

        monkeypatch.setattr(app_module.asyncio, "run_coroutine_threadsafe", fake_threadsafe)
        app_module.log_data()
        return captured

    def test_fan_on_when_temp_exceeds_max(self, db, monkeypatch):
        update_fan_settings(0, 500, 0.0, 20.0, True)  # enabled, temp_max=20
        captured = self._run_log_data(monkeypatch, temp=25.0, tvoc=100)
        # calls[0] = get_power, calls[1] = switch(True)
        assert len(captured) == 2
        assert "switch" in str(captured[1])

    def test_fan_off_when_below_thresholds(self, db, monkeypatch):
        update_fan_settings(0, 500, 0.0, 20.0, True)
        captured = self._run_log_data(monkeypatch, temp=18.0, tvoc=100)
        assert len(captured) == 2  # get_power + switch(False)

    def test_no_fan_control_when_disabled(self, db, monkeypatch):
        update_fan_settings(0, 500, 0.0, 20.0, False)  # disabled
        captured = self._run_log_data(monkeypatch, temp=30.0, tvoc=1000)
        assert len(captured) == 1  # only get_power; switch is never dispatched

    def test_fan_on_when_tvoc_exceeds_max(self, db, monkeypatch):
        update_fan_settings(0, 500, 0.0, 20.0, True)
        captured = self._run_log_data(monkeypatch, temp=15.0, tvoc=600)
        assert len(captured) == 2  # get_power + switch(True)

"""Tests for the rule-based FanController and auto/manual sync behaviour."""

from unittest.mock import MagicMock

import pytest

from mlss_monitor.fan_controller import (
    FanAction,
    FanController,
    HumidityRule,
    SensorReading,
    TemperatureRule,
    TVOCRule,
    build_default_controller,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _reading(temp=18.0, humidity=50.0, eco2=400, tvoc=100, vpd_kpa=None):
    return SensorReading(
        temperature=temp, humidity=humidity, eco2=eco2, tvoc=tvoc, vpd_kpa=vpd_kpa,
    )


def _default_settings(**overrides):
    base = {
        "tvoc_min": 0, "tvoc_max": 500,
        "temp_min": 0.0, "temp_max": 20.0,
        "enabled": True,
        "temp_enabled": True, "tvoc_enabled": True,
        "humidity_enabled": False, "humidity_max": 70.0,
    }
    base.update(overrides)
    return base


# ── TemperatureRule ──────────────────────────────────────────────────────────

class TestTemperatureRule:
    rule = TemperatureRule()

    def test_on_when_above_max(self):
        r = self.rule.evaluate(_reading(temp=25.0), _default_settings(temp_max=20.0))
        assert r.action == FanAction.ON

    def test_no_opinion_when_within_range(self):
        r = self.rule.evaluate(_reading(temp=18.0), _default_settings(temp_max=20.0))
        assert r.action == FanAction.NO_OPINION

    def test_no_opinion_when_at_exact_max(self):
        r = self.rule.evaluate(_reading(temp=20.0), _default_settings(temp_max=20.0))
        assert r.action == FanAction.NO_OPINION

    def test_disabled_returns_no_opinion(self):
        r = self.rule.evaluate(_reading(temp=99.0), _default_settings(temp_enabled=False))
        assert r.action == FanAction.NO_OPINION
        assert "disabled" in r.reason.lower()

    def test_reason_includes_values(self):
        r = self.rule.evaluate(_reading(temp=25.0), _default_settings(temp_max=20.0))
        assert "25.0" in r.reason
        assert "20.0" in r.reason


# ── TVOCRule ─────────────────────────────────────────────────────────────────

class TestTVOCRule:
    rule = TVOCRule()

    def test_on_when_above_max(self):
        r = self.rule.evaluate(_reading(tvoc=600), _default_settings(tvoc_max=500))
        assert r.action == FanAction.ON

    def test_no_opinion_when_within_range(self):
        r = self.rule.evaluate(_reading(tvoc=200), _default_settings(tvoc_max=500))
        assert r.action == FanAction.NO_OPINION

    def test_disabled_returns_no_opinion(self):
        r = self.rule.evaluate(_reading(tvoc=9999), _default_settings(tvoc_enabled=False))
        assert r.action == FanAction.NO_OPINION


# ── HumidityRule ─────────────────────────────────────────────────────────────

class TestHumidityRule:
    rule = HumidityRule()

    def test_on_when_above_max(self):
        r = self.rule.evaluate(
            _reading(humidity=80.0), _default_settings(humidity_enabled=True, humidity_max=70.0),
        )
        assert r.action == FanAction.ON

    def test_no_opinion_when_within_range(self):
        r = self.rule.evaluate(
            _reading(humidity=50.0), _default_settings(humidity_enabled=True, humidity_max=70.0),
        )
        assert r.action == FanAction.NO_OPINION

    def test_disabled_by_default(self):
        r = self.rule.evaluate(_reading(humidity=90.0), _default_settings())
        assert r.action == FanAction.NO_OPINION
        assert "disabled" in r.reason.lower()


# ── FanController ────────────────────────────────────────────────────────────

class TestFanController:
    def test_empty_controller_returns_off(self):
        ctrl = FanController()
        action, _ = ctrl.evaluate(_reading(), _default_settings())
        assert action == "off"

    def test_any_rule_on_means_fan_on(self):
        ctrl = FanController()
        ctrl.register(TemperatureRule())
        ctrl.register(TVOCRule())
        # temp above max but tvoc fine -> fan should be ON
        action, _ = ctrl.evaluate(
            _reading(temp=25.0, tvoc=100), _default_settings(temp_max=20.0),
        )
        assert action == "on"

    def test_all_no_opinion_means_fan_off(self):
        ctrl = FanController()
        ctrl.register(TemperatureRule())
        ctrl.register(TVOCRule())
        action, _ = ctrl.evaluate(
            _reading(temp=15.0, tvoc=100), _default_settings(temp_max=20.0, tvoc_max=500),
        )
        assert action == "off"

    def test_multiple_rules_on(self):
        ctrl = FanController()
        ctrl.register(TemperatureRule())
        ctrl.register(TVOCRule())
        action, results = ctrl.evaluate(
            _reading(temp=25.0, tvoc=600), _default_settings(temp_max=20.0, tvoc_max=500),
        )
        assert action == "on"
        on_results = [r for r in results if r.action == FanAction.ON]
        assert len(on_results) == 2

    def test_build_default_has_three_rules(self):
        ctrl = build_default_controller()
        assert len(ctrl.rules) == 3

    def test_results_contain_rule_names(self):
        ctrl = build_default_controller()
        _, results = ctrl.evaluate(_reading(), _default_settings())
        names = {r.rule_name for r in results}
        assert names == {"temperature", "tvoc", "humidity"}


# ── DB layer: new per-rule fields ────────────────────────────────────────────

class TestFanSettingsPerRule:
    def test_default_rule_flags(self, db):
        from database.db_logger import get_fan_settings
        s = get_fan_settings()
        assert s["temp_enabled"] is True
        assert s["tvoc_enabled"] is True
        assert s["humidity_enabled"] is False
        assert s["humidity_max"] == 70.0

    def test_update_rule_flags(self, db):
        from database.db_logger import get_fan_settings, update_fan_settings
        update_fan_settings(
            0, 500, 0.0, 20.0, True,
            temp_enabled=False, tvoc_enabled=True,
            humidity_enabled=True, humidity_max=80.0,
        )
        s = get_fan_settings()
        assert s["temp_enabled"] is False
        assert s["humidity_enabled"] is True
        assert s["humidity_max"] == 80.0

    def test_set_fan_enabled(self, db):
        from database.db_logger import get_fan_settings, set_fan_enabled
        set_fan_enabled(True)
        assert get_fan_settings()["enabled"] is True
        set_fan_enabled(False)
        assert get_fan_settings()["enabled"] is False


# ── API sync: Auto button ↔ Settings toggle ─────────────────────────────────

class TestFanModeSync:
    def test_auto_button_enables_in_db(self, app_client):
        client, _ = app_client
        res = client.post("/api/fan?state=auto")
        assert res.status_code == 200
        # DB should now have enabled=True
        settings_res = client.get("/api/fan/settings")
        assert settings_res.get_json()["enabled"] is True

    def test_manual_button_disables_in_db(self, app_client):
        import asyncio

        client, _ = app_client
        # First enable auto
        client.post("/api/fan?state=auto")
        # Then switch to manual
        mock_future = MagicMock()
        mock_future.result.return_value = None

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "run_coroutine_threadsafe", lambda coro, loop: mock_future)
            res = client.post("/api/fan?state=on")
        assert res.status_code == 200
        settings_res = client.get("/api/fan/settings")
        assert settings_res.get_json()["enabled"] is False

    def test_settings_toggle_syncs_fan_mode(self, app_client):
        from mlss_monitor import state as app_state

        client, _ = app_client
        # Enable via settings
        client.post("/api/fan/settings", json={
            "tvoc_min": 0, "tvoc_max": 500,
            "temp_min": 0.0, "temp_max": 20.0,
            "enabled": True,
        })
        assert app_state.fan_mode == "auto"

        # Disable via settings
        client.post("/api/fan/settings", json={
            "tvoc_min": 0, "tvoc_max": 500,
            "temp_min": 0.0, "temp_max": 20.0,
            "enabled": False,
        })
        assert app_state.fan_mode == "manual"


# ── Auto-status API ─────────────────────────────────────────────────────────

class TestAutoStatusAPI:
    def test_returns_mode_and_rules(self, app_client):
        client, _ = app_client
        res = client.get("/api/fan/auto-status")
        assert res.status_code == 200
        data = res.get_json()
        assert "mode" in data
        assert "auto_enabled" in data
        assert "rules" in data

    def test_reflects_last_evaluation(self, app_client):
        from mlss_monitor import state as app_state
        app_state.last_auto_action = "on"
        app_state.last_auto_evaluation = [
            {"rule": "temperature", "action": "on", "reason": "too hot"},
        ]
        client, _ = app_client
        data = client.get("/api/fan/auto-status").get_json()
        assert data["action"] == "on"
        assert len(data["rules"]) == 1
        assert data["rules"][0]["rule"] == "temperature"

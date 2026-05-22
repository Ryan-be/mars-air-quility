"""Integration tests for the endpoint merges from the audit.

Covers:
- ``/api/data?format=json|csv``
- ``/api/weather/forecast?resolution=hourly|daily``
- ``PATCH /api/inferences/<id>`` (notes / dismissed)
- ``POST /api/effector`` + ``GET /api/effectors``
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from database.db_logger import save_inference, log_sensor_data


# ── /api/data ───────────────────────────────────────────────────────────────

class TestApiDataFormat:
    def test_api_data_json_default(self, app_client, db):
        client, _ = app_client
        log_sensor_data(21.5, 50.0, 400, 120)
        res = client.get("/api/data?range=24h")
        assert res.status_code == 200
        assert res.mimetype == "application/json"
        assert isinstance(res.get_json(), list)

    def test_api_data_json_explicit(self, app_client, db):
        client, _ = app_client
        log_sensor_data(21.5, 50.0, 400, 120)
        res = client.get("/api/data?range=24h&format=json")
        assert res.status_code == 200
        assert res.mimetype == "application/json"

    def test_api_data_csv(self, app_client, db):
        client, _ = app_client
        log_sensor_data(21.5, 50.0, 400, 120)
        res = client.get("/api/data?range=24h&format=csv")
        assert res.status_code == 200
        assert res.mimetype == "text/csv"
        body = res.data.decode("utf-8")
        assert body.splitlines()[0].startswith("id,timestamp,")

    def test_api_data_unknown_format_returns_400(self, app_client, db):
        client, _ = app_client
        res = client.get("/api/data?format=xml")
        assert res.status_code == 400
        assert "error" in res.get_json()


# ── /api/weather/forecast ───────────────────────────────────────────────────

class TestApiWeatherForecastResolution:
    def _mock_location_and_client(self, monkeypatch, hourly=None, daily=None):
        import mlss_monitor.routes.api_weather as weather_module
        from mlss_monitor import state as app_state
        monkeypatch.setattr(
            weather_module, "get_location",
            lambda: {"lat": 51.5, "lon": -0.1, "name": "London"},
        )
        mock_meteo = MagicMock()
        mock_meteo.get_forecast.return_value = hourly or {"hours": []}
        mock_meteo.get_daily_forecast.return_value = daily or {"days": []}
        monkeypatch.setattr(app_state, "open_meteo", mock_meteo)
        return mock_meteo

    def test_hourly_default(self, app_client, db, monkeypatch):
        client, _ = app_client
        mock_meteo = self._mock_location_and_client(
            monkeypatch, hourly={"hours": [{"t": 1}]}
        )
        res = client.get("/api/weather/forecast")
        assert res.status_code == 200
        assert res.get_json() == {"hours": [{"t": 1}]}
        mock_meteo.get_forecast.assert_called_once()
        mock_meteo.get_daily_forecast.assert_not_called()

    def test_hourly_explicit(self, app_client, db, monkeypatch):
        client, _ = app_client
        mock_meteo = self._mock_location_and_client(monkeypatch)
        res = client.get("/api/weather/forecast?resolution=hourly")
        assert res.status_code == 200
        mock_meteo.get_forecast.assert_called_once()

    def test_daily(self, app_client, db, monkeypatch):
        client, _ = app_client
        mock_meteo = self._mock_location_and_client(
            monkeypatch, daily={"days": [{"d": 1}]}
        )
        res = client.get("/api/weather/forecast?resolution=daily")
        assert res.status_code == 200
        assert res.get_json() == {"days": [{"d": 1}]}
        mock_meteo.get_daily_forecast.assert_called_once()

    def test_invalid_resolution_returns_400(self, app_client, db, monkeypatch):
        client, _ = app_client
        self._mock_location_and_client(monkeypatch)
        res = client.get("/api/weather/forecast?resolution=weekly")
        assert res.status_code == 400
        assert "error" in res.get_json()


# ── PATCH /api/inferences/<id> ──────────────────────────────────────────────

class TestApiInferencesPatch:
    def _new_inference(self):
        return save_inference(
            event_type="tvoc_spike",
            title="t", description="d", action="a",
            severity="warning", confidence=0.9, evidence={},
        )

    def test_patch_notes_only(self, app_client, db):
        client, _ = app_client
        inf_id = self._new_inference()
        res = client.patch(f"/api/inferences/{inf_id}",
                           json={"notes": "hello"})
        assert res.status_code == 200
        # Reflected in the list
        rows = client.get("/api/inferences").get_json()
        row = next(r for r in rows if r["id"] == inf_id)
        assert row.get("user_notes") == "hello"

    def test_patch_dismiss_only(self, app_client, db):
        client, _ = app_client
        inf_id = self._new_inference()
        res = client.patch(f"/api/inferences/{inf_id}",
                           json={"dismissed": True})
        assert res.status_code == 200
        # Default listing hides dismissed rows
        rows = client.get("/api/inferences").get_json()
        assert not any(r["id"] == inf_id for r in rows)

    def test_patch_notes_and_dismiss(self, app_client, db):
        client, _ = app_client
        inf_id = self._new_inference()
        res = client.patch(f"/api/inferences/{inf_id}",
                           json={"notes": "bye", "dismissed": True})
        assert res.status_code == 200
        rows = client.get("/api/inferences?dismissed=1").get_json()
        row = next(r for r in rows if r["id"] == inf_id)
        assert row.get("user_notes") == "bye"
        assert row.get("dismissed") in (1, True)

    def test_patch_empty_body_returns_400(self, app_client, db):
        client, _ = app_client
        inf_id = self._new_inference()
        res = client.patch(f"/api/inferences/{inf_id}", json={})
        assert res.status_code == 400
        assert "error" in res.get_json()


# ── /api/effector(s) ────────────────────────────────────────────────────────

@pytest.fixture()
def effector_mock(monkeypatch, db):  # pylint: disable=unused-argument
    """Replace the asyncio dispatch + plug handle used by the effector
    state shim so no real I/O happens during tests, and seed a row in
    ``smart_plugs`` so the legacy ``POST /api/effector`` shim has a
    fan to look up via ``smart_plugs.effector_type='fan' AND scope='hub'``.

    The MLSS topology Phase 2 migration moved the canonical effector
    surface to ``api_effectors_v2``. The legacy single-fan registry no
    longer drives state changes; instead the shim resolves ``fan1`` to
    the seeded row and calls ``apply_state``. The fixture has to seed
    that row explicitly because the production seed key
    (``MLSS_FAN_KASA_SMART_PLUG_IP``) is not set in test envs.
    """
    import sqlite3
    from datetime import datetime
    import mlss_monitor.routes.api_effectors_v2 as v2_module
    from mlss_monitor import state as app_state
    import database.db_logger as dbl

    mock_future = MagicMock()
    mock_future.result.return_value = None

    def _threadsafe(coro, loop):
        return mock_future

    monkeypatch.setattr(
        v2_module.asyncio, "run_coroutine_threadsafe", _threadsafe,
    )

    # The effector modules snapshot DB_FILE at import time, so the
    # ``db`` fixture's update to ``database.init_db.DB_FILE`` doesn't
    # propagate. Patch each module-level copy explicitly.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_effectors.DB_FILE", dbl.DB_FILE,
    )
    monkeypatch.setattr(
        "mlss_monitor.routes.api_effectors_v2.DB_FILE", dbl.DB_FILE,
    )
    monkeypatch.setattr(
        "mlss_monitor.effectors.store.DB_FILE", dbl.DB_FILE,
    )

    mock_plug = MagicMock()
    mock_plug.switch = MagicMock()
    monkeypatch.setattr(app_state, "fan_smart_plug", mock_plug)
    # state.smart_plugs is the runtime registry keyed by row id.
    monkeypatch.setattr(
        app_state, "smart_plugs", {1: mock_plug}, raising=False,
    )

    # Seed the fan row that the legacy shim looks up.
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(dbl.DB_FILE)
    conn.execute(
        "INSERT INTO smart_plugs "
        "(label, effector_type, scope, kasa_host, protocol, "
        " is_enabled, auto_mode, current_state, created_at) "
        "VALUES ('Room fan', 'fan', 'hub', '192.0.2.200', 'kasa', "
        "        1, 1, 'unknown', ?)",
        (now,),
    )
    conn.commit()
    conn.close()

    return mock_future, mock_plug


class TestApiEffectorPost:
    def test_fan_on(self, app_client, effector_mock):
        client, _ = app_client
        res = client.post("/api/effector",
                          json={"key": "fan1", "state": "on"})
        assert res.status_code == 200
        body = res.get_json()
        assert body["key"] == "fan1"
        assert body["state"] == "on"
        # Phase 2 added a deprecation marker — assert it surfaces.
        assert res.headers.get("Deprecation") == "true"

    def test_fan_off(self, app_client, effector_mock):
        client, _ = app_client
        res = client.post("/api/effector",
                          json={"key": "fan1", "state": "off"})
        assert res.status_code == 200

    def test_unknown_key_returns_404(self, app_client, effector_mock):
        client, _ = app_client
        res = client.post("/api/effector",
                          json={"key": "mystery", "state": "on"})
        assert res.status_code == 404

    def test_invalid_state_returns_400(self, app_client, effector_mock):
        client, _ = app_client
        res = client.post("/api/effector",
                          json={"key": "fan1", "state": "sideways"})
        assert res.status_code == 400

    def test_missing_key_returns_400(self, app_client, effector_mock):
        client, _ = app_client
        res = client.post("/api/effector", json={"state": "on"})
        assert res.status_code == 400


class TestApiEffectorsGet:
    def test_returns_v2_shape_with_seeded_fan(self, app_client, effector_mock):
        """Phase 2 migration: GET /api/effectors now returns the v2
        ``{"effectors": [...]}`` shape served by the v2 blueprint.

        The seeded row carries DB columns (id, label, effector_type,
        scope, kasa_host, ...) rather than the registry's
        (key, type, state, power_w). Frontends migrate alongside the
        backend; external consumers calling this endpoint will see the
        new shape.
        """
        client, _ = app_client
        res = client.get("/api/effectors")
        assert res.status_code == 200
        body = res.get_json()
        assert isinstance(body, dict)
        assert "effectors" in body
        fans = [e for e in body["effectors"] if e["effector_type"] == "fan"]
        assert len(fans) >= 1
        assert fans[0]["scope"] == "hub"


# ── Removed endpoint shapes must 404/405 ────────────────────────────────────
#
# Regression guards: a future refactor could silently re-register an old
# blueprint route; these tests fail if any of the retired shapes start
# answering successfully again.  We accept 404 (route is gone) OR 405 (the
# path still matches a new route on a different method) — the goal is to
# prove the old *method + path* no longer succeeds.

class TestOldShapesAreRetired:
    def _assert_retired(self, response):
        assert response.status_code in (404, 405), (
            f"Retired endpoint still answers with {response.status_code}; "
            "body: " + response.get_data(as_text=True)[:200]
        )

    def test_post_inferences_notes_is_retired(self, app_client, db):
        client, _ = app_client
        inf_id = save_inference(
            event_type="tvoc_spike", title="t", description="d",
            action="a", severity="warning", confidence=0.9, evidence={},
        )
        self._assert_retired(
            client.post(
                f"/api/inferences/{inf_id}/notes",
                json={"notes": "legacy call"},
            )
        )

    def test_post_inferences_dismiss_is_retired(self, app_client, db):
        client, _ = app_client
        inf_id = save_inference(
            event_type="tvoc_spike", title="t", description="d",
            action="a", severity="warning", confidence=0.9, evidence={},
        )
        self._assert_retired(client.post(f"/api/inferences/{inf_id}/dismiss"))

    def test_post_sources_enable_is_retired(self, app_client):
        client, _ = app_client
        self._assert_retired(
            client.post("/api/insights-engine/sources/sgp30/enable")
        )

    def test_post_sources_disable_is_retired(self, app_client):
        client, _ = app_client
        self._assert_retired(
            client.post("/api/insights-engine/sources/sgp30/disable")
        )

    def test_get_download_is_retired(self, app_client, db):
        client, _ = app_client
        self._assert_retired(client.get("/api/download?range=24h"))

    def test_get_forecast_daily_is_retired(self, app_client):
        client, _ = app_client
        self._assert_retired(client.get("/api/weather/forecast/daily"))

    def test_post_fan_state_on_is_retired(self, app_client):
        """POST /api/fan?state=on|off|auto is gone; /api/effector replaces it."""
        client, _ = app_client
        self._assert_retired(client.post("/api/fan?state=on"))
        self._assert_retired(client.post("/api/fan?state=off"))
        self._assert_retired(client.post("/api/fan?state=auto"))

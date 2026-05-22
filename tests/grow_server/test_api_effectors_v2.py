"""Tests for ``mlss_monitor.routes.api_effectors_v2`` blueprint.

Coverage:
* GET    /api/effectors             — list + single-row reads
* GET    /api/effectors/<id>        — 200 + 404
* POST   /api/effectors             — admin-only; 201 / 400 / 409
* PATCH  /api/effectors/<id>        — admin-only; rename / retype /
                                       rescope / auto_mode / rules
* DELETE /api/effectors/<id>        — admin-only; 200 / 404
* POST   /api/effectors/<id>/state  — controller+admin; on/off/auto;
                                       SSE event publish on success
* PATCH  /api/effectors/layout      — controller+admin; bulk positions
* Legacy POST /api/effector         — shim onto v2 state; Deprecation hdr

Fixtures: a Flask app composed of (auth_bp, api_effectors_v2_bp,
api_effectors_bp) plus an SQLite tempfile primed with the full schema
+ a single ``grow_units`` row so scope='grow_unit' POSTs validate.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from database.init_db import create_db


def _seed_grow_unit(db_path: str, unit_id: int = 1, label: str = "Tomato 1"):
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        " bearer_token_hash, phase_set_at) "
        "VALUES (?, ?, ?, ?, 'h', ?)",
        (unit_id, f"hw-{unit_id}", label, now, now),
    )
    conn.commit()
    conn.close()


def _login(client, role: str = "admin"):
    """Stamp a logged-in session of the given role on the test client."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = f"test-{role}"
        sess["user_role"] = role
        sess["user_id"] = None


@pytest.fixture
def v2_client(monkeypatch, tmp_path):
    """Flask app with the two effector blueprints + the schema-primed DB."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.store.DB_FILE", db_path)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_effectors_v2.DB_FILE", db_path,
    )
    # The legacy shim opens its own connection to look up the fan row,
    # so we must redirect its module-level snapshot too.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_effectors.DB_FILE", db_path,
    )
    create_db()
    _seed_grow_unit(db_path)

    from flask import Flask
    from mlss_monitor.routes.api_effectors_v2 import api_effectors_v2_bp
    from mlss_monitor.routes.api_effectors import api_effectors_bp

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(api_effectors_v2_bp)
    app.register_blueprint(api_effectors_bp)
    return app.test_client()


# ── GET /api/effectors ─────────────────────────────────────────────────────

class TestListEffectors:
    def test_empty_returns_empty_list(self, v2_client):
        _login(v2_client, "viewer")
        r = v2_client.get("/api/effectors")
        assert r.status_code == 200
        assert r.get_json() == {"effectors": []}

    def test_unauthenticated_returns_401(self, v2_client):
        r = v2_client.get("/api/effectors")
        assert r.status_code == 401

    def test_returns_row_with_rules_parsed(self, v2_client):
        _login(v2_client, "admin")
        v2_client.post("/api/effectors", json={
            "label": "Filter fan", "effector_type": "fan_carbon_filter",
            "scope": "hub", "kasa_host": "192.0.2.41",
            "rules": {"temp_max": 21},
        })
        r = v2_client.get("/api/effectors")
        assert r.status_code == 200
        body = r.get_json()
        assert len(body["effectors"]) == 1
        row = body["effectors"][0]
        assert row["label"] == "Filter fan"
        assert row["rules"] == {"temp_max": 21}
        assert row["layout"] is None
        # Defaults applied
        assert row["is_enabled"] == 1
        assert row["auto_mode"] == 1
        assert row["current_state"] == "unknown"


# ── GET /api/effectors/<id> ────────────────────────────────────────────────

class TestGetEffector:
    def test_existing_row(self, v2_client):
        _login(v2_client, "admin")
        post_body = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.110",
        }).get_json()
        new_id = post_body["id"]
        r = v2_client.get(f"/api/effectors/{new_id}")
        assert r.status_code == 200
        assert r.get_json()["id"] == new_id

    def test_missing_returns_404(self, v2_client):
        _login(v2_client, "viewer")
        r = v2_client.get("/api/effectors/99999")
        assert r.status_code == 404


# ── POST /api/effectors ────────────────────────────────────────────────────

class TestCreateEffector:
    def test_admin_can_create_hub_fan(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={
            "label": "Hub fan", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.120",
        })
        assert r.status_code == 201
        body = r.get_json()
        assert "id" in body
        assert body["label"] == "Hub fan"

    def test_controller_cannot_create(self, v2_client):
        _login(v2_client, "controller")
        r = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.121",
        })
        assert r.status_code == 403

    def test_invalid_effector_type_returns_400(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "spaceship", "scope": "hub",
            "kasa_host": "192.0.2.122",
        })
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_scope_mismatch_returns_400(self, v2_client):
        """heat_pad is grow_unit-only; scope='hub' must be rejected."""
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "heat_pad", "scope": "hub",
            "kasa_host": "192.0.2.123",
        })
        assert r.status_code == 400

    def test_grow_unit_scope_requires_grow_unit_id(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "heat_pad",
            "scope": "grow_unit", "kasa_host": "192.0.2.124",
        })
        assert r.status_code == 400

    def test_grow_unit_scope_with_id_succeeds(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={
            "label": "Pad", "effector_type": "heat_pad",
            "scope": "grow_unit", "grow_unit_id": 1,
            "kasa_host": "192.0.2.125",
        })
        assert r.status_code == 201

    def test_missing_fields_returns_400(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors", json={"label": "X"})
        assert r.status_code == 400

    def test_duplicate_host_returns_409(self, v2_client):
        _login(v2_client, "admin")
        v2_client.post("/api/effectors", json={
            "label": "A", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.130",
        })
        r = v2_client.post("/api/effectors", json={
            "label": "B", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.130",
        })
        assert r.status_code == 409


# ── PATCH /api/effectors/<id> ──────────────────────────────────────────────

class TestPatchEffector:
    def _create(self, client, **overrides):
        body = {
            "label": "Original", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.140",
        }
        body.update(overrides)
        return client.post("/api/effectors", json=body).get_json()["id"]

    def test_admin_can_rename(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client)
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"label": "Renamed"})
        assert r.status_code == 200
        assert (v2_client.get(f"/api/effectors/{new_id}")
                .get_json()["label"] == "Renamed")

    def test_controller_cannot_patch(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client)
        _login(v2_client, "controller")
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"label": "Hax"})
        assert r.status_code == 403

    def test_can_toggle_auto_mode(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client)
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"auto_mode": 0})
        assert r.status_code == 200
        assert (v2_client.get(f"/api/effectors/{new_id}")
                .get_json()["auto_mode"] == 0)

    def test_can_update_rules(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client)
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"rules": {"temp_max": 30}})
        assert r.status_code == 200
        assert (v2_client.get(f"/api/effectors/{new_id}")
                .get_json()["rules"] == {"temp_max": 30})

    def test_can_retype_with_compatible_scope(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client, effector_type="fan", scope="hub")
        # fan_carbon_filter is also hub-only
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"effector_type": "fan_carbon_filter"})
        assert r.status_code == 200

    def test_retype_to_incompatible_scope_returns_400(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client, effector_type="fan", scope="hub")
        # heat_pad is grow_unit-only; current scope is hub → reject
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"effector_type": "heat_pad"})
        assert r.status_code == 400

    def test_rescope_to_grow_unit_requires_id(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client,
                              effector_type="humidifier", scope="hub")
        # missing grow_unit_id should fail
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"scope": "grow_unit"})
        assert r.status_code == 400

    def test_rescope_with_grow_unit_id_succeeds(self, v2_client):
        _login(v2_client, "admin")
        new_id = self._create(v2_client,
                              effector_type="humidifier", scope="hub")
        r = v2_client.patch(f"/api/effectors/{new_id}",
                            json={"scope": "grow_unit", "grow_unit_id": 1})
        assert r.status_code == 200

    def test_patch_missing_row_returns_404(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.patch("/api/effectors/99999", json={"label": "X"})
        assert r.status_code == 404


# ── DELETE /api/effectors/<id> ─────────────────────────────────────────────

class TestDeleteEffector:
    def test_admin_can_delete(self, v2_client):
        _login(v2_client, "admin")
        new_id = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.150",
        }).get_json()["id"]
        r = v2_client.delete(f"/api/effectors/{new_id}")
        assert r.status_code == 200
        assert (v2_client.get(f"/api/effectors/{new_id}")
                .status_code == 404)

    def test_controller_cannot_delete(self, v2_client):
        _login(v2_client, "admin")
        new_id = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.151",
        }).get_json()["id"]
        _login(v2_client, "controller")
        r = v2_client.delete(f"/api/effectors/{new_id}")
        assert r.status_code == 403

    def test_missing_returns_404(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.delete("/api/effectors/99999")
        assert r.status_code == 404


# ── POST /api/effectors/<id>/state ─────────────────────────────────────────

@pytest.fixture
def state_mock(monkeypatch):
    """Stub the live plug handle + asyncio dispatch so state writes don't
    hit the network. Returns the mock plug for assertion convenience."""
    from mlss_monitor import state as app_state
    import mlss_monitor.routes.api_effectors_v2 as v2_module

    mock_plug = MagicMock()
    future = MagicMock()
    future.result.return_value = None

    def fake_threadsafe(coro, loop):
        return future

    monkeypatch.setattr(v2_module.asyncio, "run_coroutine_threadsafe",
                        fake_threadsafe)
    # state.smart_plugs is the runtime registry the evaluator will use.
    monkeypatch.setattr(app_state, "smart_plugs", {1: mock_plug}, raising=False)
    return mock_plug


class TestPostState:
    def _seed_one(self, client, plug_id=1):
        _login(client, "admin")
        body = client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.160",
        }).get_json()
        # Most tests want the seeded id == 1 so they can reuse the
        # default state_mock; assert that's what happened.
        assert body["id"] == plug_id

    def test_controller_can_set_on(self, v2_client, state_mock):
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        r = v2_client.post("/api/effectors/1/state", json={"state": "on"})
        assert r.status_code == 200
        row = v2_client.get("/api/effectors/1").get_json()
        assert row["current_state"] == "on"
        # ON also flips auto_mode → 0 (forced override per plan §2.7)
        assert row["auto_mode"] == 0

    def test_set_off_flips_auto_mode_off(self, v2_client, state_mock):
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        r = v2_client.post("/api/effectors/1/state", json={"state": "off"})
        assert r.status_code == 200
        row = v2_client.get("/api/effectors/1").get_json()
        assert row["auto_mode"] == 0

    def test_set_auto_re_enables_auto_mode(self, v2_client, state_mock):
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        v2_client.post("/api/effectors/1/state", json={"state": "off"})
        r = v2_client.post("/api/effectors/1/state", json={"state": "auto"})
        assert r.status_code == 200
        assert (v2_client.get("/api/effectors/1")
                .get_json()["auto_mode"] == 1)

    def test_viewer_cannot_set_state(self, v2_client, state_mock):
        self._seed_one(v2_client)
        _login(v2_client, "viewer")
        r = v2_client.post("/api/effectors/1/state", json={"state": "on"})
        assert r.status_code == 403

    def test_invalid_state_returns_400(self, v2_client, state_mock):
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        r = v2_client.post("/api/effectors/1/state",
                           json={"state": "diagonal"})
        assert r.status_code == 400

    def test_missing_plug_returns_404(self, v2_client, state_mock):
        _login(v2_client, "admin")
        r = v2_client.post("/api/effectors/99999/state",
                           json={"state": "on"})
        assert r.status_code == 404

    def test_publishes_effector_state_changed_event(
        self, v2_client, state_mock, monkeypatch,
    ):
        from mlss_monitor import state as app_state
        bus = MagicMock()
        monkeypatch.setattr(app_state, "event_bus", bus)
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        v2_client.post("/api/effectors/1/state", json={"state": "on"})
        bus.publish.assert_called_once()
        evt_name, payload = bus.publish.call_args[0]
        assert evt_name == "effector_state_changed"
        assert payload["id"] == 1
        assert payload["state"] == "on"

    @pytest.mark.parametrize("desired", ["on", "off"])
    def test_publishes_for_both_on_and_off(
        self, v2_client, state_mock, monkeypatch, desired,
    ):
        """Regression guard: Phase 3 wired apply_state to publish for
        every state change, including ``off`` (the original Phase 2
        test only checked ``on``)."""
        from mlss_monitor import state as app_state
        bus = MagicMock()
        monkeypatch.setattr(app_state, "event_bus", bus)
        self._seed_one(v2_client)
        _login(v2_client, "controller")
        v2_client.post("/api/effectors/1/state", json={"state": desired})
        bus.publish.assert_called_once()
        evt_name, payload = bus.publish.call_args[0]
        assert evt_name == "effector_state_changed"
        assert payload["id"] == 1
        assert payload["state"] == desired
        # ``auto`` is False when an explicit on/off is forced.
        assert payload["auto"] is False


# ── PATCH /api/effectors/layout ────────────────────────────────────────────

class TestPatchLayout:
    def test_controller_can_save_positions(self, v2_client):
        _login(v2_client, "admin")
        new_id = v2_client.post("/api/effectors", json={
            "label": "X", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.170",
        }).get_json()["id"]
        _login(v2_client, "controller")
        r = v2_client.patch("/api/effectors/layout", json={
            "positions": [
                {"kind": "effector", "id": new_id, "x": 10.5, "y": 20.0},
                {"kind": "hub",      "id": "hub",  "x": 0.0,  "y": 0.0},
                {"kind": "grow",     "id": "1",    "x": -5.0, "y": 50.0},
            ],
        })
        assert r.status_code == 200
        # Effector position lands in smart_plugs.layout_json
        row = v2_client.get(f"/api/effectors/{new_id}").get_json()
        assert row["layout"] == {"x": 10.5, "y": 20.0}

    def test_viewer_cannot_save_positions(self, v2_client):
        _login(v2_client, "viewer")
        r = v2_client.patch("/api/effectors/layout",
                            json={"positions": []})
        assert r.status_code == 403

    def test_invalid_kind_returns_400(self, v2_client):
        _login(v2_client, "admin")
        r = v2_client.patch("/api/effectors/layout", json={
            "positions": [
                {"kind": "spaceship", "id": "1", "x": 0, "y": 0},
            ],
        })
        assert r.status_code == 400

    def test_hub_and_grow_positions_persist_to_node_layout(
        self, v2_client, monkeypatch, tmp_path,
    ):
        """Hub/grow positions land in the node_layout table."""
        _login(v2_client, "admin")
        v2_client.patch("/api/effectors/layout", json={
            "positions": [
                {"kind": "hub",  "id": "hub", "x": 7.5, "y": -3.0},
                {"kind": "grow", "id": "1",   "x": 1.0, "y": 2.0},
            ],
        })
        # Pull straight from the test DB to confirm persistence
        import database.init_db as _dbi
        conn = sqlite3.connect(_dbi.DB_FILE)
        try:
            rows = conn.execute(
                "SELECT node_kind, node_id, x, y FROM node_layout"
            ).fetchall()
        finally:
            conn.close()
        as_set = set(rows)
        assert ("hub",  "hub", 7.5, -3.0) in as_set
        assert ("grow", "1",   1.0,  2.0) in as_set


# ── Legacy POST /api/effector shim ─────────────────────────────────────────

class TestLegacyEffectorShim:
    def test_fan1_legacy_shim_invokes_v2_state(self, v2_client, state_mock):
        """POST /api/effector {key:fan1, state:on} must route to v2."""
        _login(v2_client, "admin")
        # Seed the row the shim will look up: effector_type='fan' + hub
        v2_client.post("/api/effectors", json={
            "label": "Room fan", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.180",
        })
        _login(v2_client, "controller")
        r = v2_client.post(
            "/api/effector", json={"key": "fan1", "state": "on"},
        )
        assert r.status_code == 200
        # New row should now reflect the on state in the v2 table
        row = v2_client.get("/api/effectors/1").get_json()
        assert row["current_state"] == "on"

    def test_legacy_shim_emits_deprecation_header(
        self, v2_client, state_mock,
    ):
        _login(v2_client, "admin")
        v2_client.post("/api/effectors", json={
            "label": "Room fan", "effector_type": "fan", "scope": "hub",
            "kasa_host": "192.0.2.181",
        })
        _login(v2_client, "controller")
        r = v2_client.post(
            "/api/effector", json={"key": "fan1", "state": "on"},
        )
        # Per the user instructions: Deprecation: true on the shimmed
        # response so any external consumer notices it's on borrowed time.
        assert r.headers.get("Deprecation") == "true"

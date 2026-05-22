"""Pure CRUD tests for ``mlss_monitor.effectors.store``.

Mirrors the test pattern in ``tests/grow_server/test_grow_units_api.py``:
``tmp_path``-backed SQLite + monkeypatching every module-level snapshot
of ``DB_FILE`` so the store sees the test DB.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from database.init_db import create_db


@pytest.fixture
def db_with_grow_unit(monkeypatch, tmp_path):
    """Real schema + one grow_unit row so scope='grow_unit' tests work."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.store.DB_FILE", db_path)
    create_db()
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        " bearer_token_hash, phase_set_at) "
        "VALUES (1, 'hw-1', 'Tomato 1', ?, 'h', ?)",
        (now, now),
    )
    conn.commit()
    conn.close()
    return db_path


def test_create_smart_plug_returns_id(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="Filter fan",
        effector_type="fan_carbon_filter",
        scope="hub",
        kasa_host="192.0.2.41",
    )
    assert isinstance(new_id, int) and new_id > 0


def test_get_smart_plug_returns_row_dict(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="Filter fan",
        effector_type="fan_carbon_filter",
        scope="hub",
        kasa_host="192.0.2.42",
    )
    row = store.get_smart_plug(new_id)
    assert row is not None
    assert row["id"] == new_id
    assert row["label"] == "Filter fan"
    assert row["effector_type"] == "fan_carbon_filter"
    assert row["scope"] == "hub"
    assert row["kasa_host"] == "192.0.2.42"
    # Defaults
    assert row["is_enabled"] == 1
    assert row["auto_mode"] == 1
    assert row["current_state"] == "unknown"
    assert row["protocol"] == "kasa"
    # rules / layout are parsed dicts (or None)
    assert row["rules"] in (None, {})
    assert row["layout"] is None
    # Timestamps populated
    assert row["created_at"]


def test_get_smart_plug_missing_returns_none(db_with_grow_unit):
    from mlss_monitor.effectors import store
    assert store.get_smart_plug(99999) is None


def test_list_smart_plugs_returns_all(db_with_grow_unit):
    from mlss_monitor.effectors import store

    store.create_smart_plug(
        label="A", effector_type="fan", scope="hub", kasa_host="192.0.2.51",
    )
    store.create_smart_plug(
        label="B", effector_type="heat_pad", scope="grow_unit",
        grow_unit_id=1, kasa_host="192.0.2.52",
    )
    rows = store.list_smart_plugs()
    assert len(rows) == 2
    labels = {r["label"] for r in rows}
    assert labels == {"A", "B"}


def test_update_smart_plug_changes_label(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="Old", effector_type="fan", scope="hub",
        kasa_host="192.0.2.61",
    )
    ok = store.update_smart_plug(new_id, label="New")
    assert ok is True
    assert store.get_smart_plug(new_id)["label"] == "New"


def test_update_smart_plug_missing_returns_false(db_with_grow_unit):
    from mlss_monitor.effectors import store
    assert store.update_smart_plug(99999, label="Nope") is False


def test_update_smart_plug_sets_updated_at(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.62",
    )
    assert store.get_smart_plug(new_id)["updated_at"] is None
    store.update_smart_plug(new_id, auto_mode=0)
    after = store.get_smart_plug(new_id)
    assert after["updated_at"] is not None
    assert after["auto_mode"] == 0


def test_update_rules_round_trips_dict(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.63",
    )
    rules = {"temp_max": 22.5, "temp_enabled": True}
    store.update_smart_plug(new_id, rules=rules)
    assert store.get_smart_plug(new_id)["rules"] == rules


def test_delete_smart_plug_returns_true_on_hit(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.71",
    )
    assert store.delete_smart_plug(new_id) is True
    assert store.get_smart_plug(new_id) is None


def test_delete_smart_plug_returns_false_on_miss(db_with_grow_unit):
    from mlss_monitor.effectors import store
    assert store.delete_smart_plug(99999) is False


def test_update_layout_persists_xy(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.81",
    )
    ok = store.update_layout(new_id, x=123.5, y=-45.0)
    assert ok is True
    row = store.get_smart_plug(new_id)
    assert row["layout"] == {"x": 123.5, "y": -45.0}


def test_update_layout_missing_returns_false(db_with_grow_unit):
    from mlss_monitor.effectors import store
    assert store.update_layout(99999, x=0, y=0) is False


def test_update_last_state_persists_state_and_timestamp(db_with_grow_unit):
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.91",
    )
    ok = store.update_last_state(new_id, "on")
    assert ok is True
    row = store.get_smart_plug(new_id)
    assert row["current_state"] == "on"
    assert row["current_state_at"] is not None


def test_update_last_state_missing_returns_false(db_with_grow_unit):
    from mlss_monitor.effectors import store
    assert store.update_last_state(99999, "on") is False


def test_create_with_rules_round_trips(db_with_grow_unit):
    """The create path should accept a dict and surface it back parsed."""
    from mlss_monitor.effectors import store

    rules = {"temp_max": 22, "temp_enabled": True}
    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.95", rules=rules,
    )
    row = store.get_smart_plug(new_id)
    assert row["rules"] == rules


def test_create_raises_integrity_error_on_duplicate_host(db_with_grow_unit):
    from mlss_monitor.effectors import store

    store.create_smart_plug(
        label="A", effector_type="fan", scope="hub",
        kasa_host="192.0.2.99",
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.create_smart_plug(
            label="B", effector_type="fan", scope="hub",
            kasa_host="192.0.2.99",
        )


def test_list_smart_plugs_parses_rules_and_layout(db_with_grow_unit):
    """list() must surface dicts (not raw JSON strings) for rules/layout."""
    from mlss_monitor.effectors import store

    new_id = store.create_smart_plug(
        label="X", effector_type="fan", scope="hub",
        kasa_host="192.0.2.101",
        rules={"temp_max": 21},
    )
    store.update_layout(new_id, x=10, y=20)
    [row] = store.list_smart_plugs()
    assert row["rules"] == {"temp_max": 21}
    assert row["layout"] == {"x": 10.0, "y": 20.0}

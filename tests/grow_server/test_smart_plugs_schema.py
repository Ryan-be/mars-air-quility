"""Smart-plug schema regression suite.

Covers the new ``smart_plugs`` + ``node_layout`` tables created by
:func:`database.effectors_schema.create_effectors_schema` and the one-off
seed migration that imports the legacy single Kasa fan from the env var.

Test discipline (per ``docs/superpowers/plans/2026-05-22-mlss-topology.md``):
each test asserts ONE behaviour so a regression points at exactly the
constraint that broke.
"""
from __future__ import annotations

import sqlite3

import pytest

from database.init_db import create_db


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1]: r[2] for r in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()}
    finally:
        conn.close()


def _seed_fan_ip(monkeypatch, ip="192.0.2.10"):
    """Pretend the operator set MLSS_FAN_KASA_SMART_PLUG_IP in .env.

    ``config.get`` is read at seed time from
    :mod:`database.effectors_schema` so patching the underlying
    dynaconf object's ``get`` method is the cleanest hook.
    """
    from config import config as _config

    real_get = _config.get

    def fake_get(key, default=None):
        if key == "FAN_KASA_SMART_PLUG_IP":
            return ip
        return real_get(key, default)

    monkeypatch.setattr(_config, "get", fake_get)


def test_smart_plugs_table_columns(monkeypatch, tmp_path):
    """Every column the v2 API + evaluator expect is present after create_db()."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    cols = _columns(db_path, "smart_plugs")
    for c in [
        "id", "label", "effector_type", "scope", "grow_unit_id",
        "kasa_host", "protocol", "is_enabled", "auto_mode",
        "rules_json", "layout_json", "current_state",
        "current_state_at", "created_at", "updated_at",
    ]:
        assert c in cols, f"missing column: {c}"


def test_smart_plugs_idempotent(monkeypatch, tmp_path):
    """Re-running ``create_db()`` on an existing DB must not raise."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    create_db()  # second call: CREATE TABLE IF NOT EXISTS path


def test_existing_fan_seeded_on_first_create(monkeypatch, tmp_path):
    """When FAN_KASA_SMART_PLUG_IP is set, a hub-scope fan row is seeded."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    _seed_fan_ip(monkeypatch, ip="192.0.2.10")
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT label, effector_type, scope, grow_unit_id, "
            "       kasa_host, protocol, is_enabled, auto_mode, current_state "
            "FROM smart_plugs"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    label, etype, scope, grow_unit_id, host, protocol, enabled, auto, state = rows[0]
    assert etype == "fan"
    assert scope == "hub"
    assert grow_unit_id is None
    assert host == "192.0.2.10"
    assert protocol == "kasa"
    assert enabled == 1
    assert auto == 1
    assert state == "unknown"
    assert label  # non-empty label seeded


def test_seed_idempotent_when_run_twice(monkeypatch, tmp_path):
    """A second create_db() with the same IP must not insert a duplicate row."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    _seed_fan_ip(monkeypatch, ip="192.0.2.11")
    create_db()
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM smart_plugs"
        ).fetchone()
    finally:
        conn.close()
    assert count == 1


def test_no_seed_when_fan_ip_absent(monkeypatch, tmp_path):
    """Without the env var, no fan row is seeded — empty table is fine."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)

    from config import config as _config

    real_get = _config.get

    def fake_get(key, default=None):
        if key == "FAN_KASA_SMART_PLUG_IP":
            return None
        return real_get(key, default)

    monkeypatch.setattr(_config, "get", fake_get)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM smart_plugs"
        ).fetchone()
    finally:
        conn.close()
    assert count == 0


def test_effector_type_check_constraint(monkeypatch, tmp_path):
    """An unknown effector_type must be rejected by the CHECK constraint."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO smart_plugs "
                "(label, effector_type, scope, kasa_host, created_at) "
                "VALUES ('x', 'rocket_engine', 'hub', '192.0.2.20', "
                "        '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()


def test_scope_hub_requires_null_grow_unit(monkeypatch, tmp_path):
    """scope='hub' with grow_unit_id NOT NULL must violate the CHECK."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO smart_plugs "
                "(label, effector_type, scope, grow_unit_id, "
                " kasa_host, created_at) "
                "VALUES ('x', 'fan', 'hub', 1, '192.0.2.21', "
                "        '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()


def test_scope_grow_unit_requires_grow_unit_id(monkeypatch, tmp_path):
    """scope='grow_unit' with grow_unit_id NULL must violate the CHECK."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO smart_plugs "
                "(label, effector_type, scope, kasa_host, created_at) "
                "VALUES ('x', 'heat_pad', 'grow_unit', '192.0.2.22', "
                "        '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()


def test_kasa_host_unique(monkeypatch, tmp_path):
    """Two rows sharing a kasa_host must be rejected by UNIQUE."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO smart_plugs "
            "(label, effector_type, scope, kasa_host, created_at) "
            "VALUES ('a', 'fan', 'hub', '192.0.2.30', "
            "        '2026-05-22T00:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO smart_plugs "
                "(label, effector_type, scope, kasa_host, created_at) "
                "VALUES ('b', 'fan', 'hub', '192.0.2.30', "
                "        '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()


def test_node_layout_pk_blocks_duplicates(monkeypatch, tmp_path):
    """node_layout composite PK (kind, id) must reject duplicate rows."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO node_layout (node_kind, node_id, x, y, updated_at) "
            "VALUES ('hub', 'hub', 0, 0, '2026-05-22T00:00:00')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO node_layout "
                "(node_kind, node_id, x, y, updated_at) "
                "VALUES ('hub', 'hub', 1, 1, '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()


def test_node_layout_kind_check_constraint(monkeypatch, tmp_path):
    """node_layout.node_kind must be one of ('hub','grow','effector')."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    create_db()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO node_layout "
                "(node_kind, node_id, x, y, updated_at) "
                "VALUES ('bogus', '1', 0, 0, '2026-05-22T00:00:00')"
            )
            conn.commit()
    finally:
        conn.close()

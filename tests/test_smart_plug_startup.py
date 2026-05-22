"""Tests for the Phase 3 app.py startup wiring.

Covers the extracted ``_start_smart_plug_evaluator`` helper that:

* Populates ``state.smart_plugs`` from every is_enabled row in
  ``smart_plugs`` (one KasaSmartPlug constructed per row).
* Mirrors ``state.smart_plugs[1]`` into ``state.fan_smart_plug`` so
  the legacy import path keeps working.
* Spawns the evaluator daemon thread and stashes its handle on
  ``state.smart_plug_evaluator``.

Extracted into its own helper (mirroring ``_start_backup_workers``) so
these tests can drive the wiring without standing up the entire
background-services bootstrap.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from database.init_db import create_db


@pytest.fixture
def startup_env(monkeypatch, tmp_path):
    """Schema-primed tempfile DB + a clean state module."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.store.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.evaluator.DB_FILE", db_path)
    create_db()

    from mlss_monitor import state as state_module
    monkeypatch.setattr(state_module, "smart_plugs", {}, raising=False)
    monkeypatch.setattr(state_module, "smart_plug_evaluator", None,
                        raising=False)
    monkeypatch.setattr(state_module, "fan_smart_plug", None,
                        raising=False)
    return db_path, state_module


def _seed(db_path: str, label: str, host: str,
          is_enabled: int = 1, plug_id: int | None = None) -> int:
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    if plug_id is not None:
        cur = conn.execute(
            "INSERT INTO smart_plugs "
            "(id, label, effector_type, scope, kasa_host, protocol, "
            " is_enabled, auto_mode, current_state, created_at) "
            "VALUES (?, ?, 'fan', 'hub', ?, 'kasa', ?, 1, 'unknown', ?)",
            (plug_id, label, host, is_enabled, now),
        )
    else:
        cur = conn.execute(
            "INSERT INTO smart_plugs "
            "(label, effector_type, scope, kasa_host, protocol, "
            " is_enabled, auto_mode, current_state, created_at) "
            "VALUES (?, 'fan', 'hub', ?, 'kasa', ?, 1, 'unknown', ?)",
            (label, host, is_enabled, now),
        )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


class TestStartSmartPlugEvaluator:
    def test_populates_state_smart_plugs_from_enabled_rows(
        self, startup_env, monkeypatch,
    ):
        db_path, state_module = startup_env
        _seed(db_path, "Hub fan", "192.0.2.10", plug_id=1)
        _seed(db_path, "Carbon filter", "192.0.2.11", plug_id=2)
        # Disabled rows MUST be skipped — they don't get a live handle
        _seed(db_path, "Spare", "192.0.2.12",
              is_enabled=0, plug_id=3)

        # Stub the daemon-thread launch so we don't actually start a
        # background thread for this unit test.
        from mlss_monitor.app import _start_smart_plug_evaluator
        from mlss_monitor.effectors import evaluator
        monkeypatch.setattr(evaluator, "start_evaluator",
                            lambda: MagicMock(name="thread"))

        _start_smart_plug_evaluator(state_module)

        assert set(state_module.smart_plugs.keys()) == {1, 2}
        assert state_module.smart_plugs[1] is not None
        assert state_module.smart_plugs[2] is not None
        assert 3 not in state_module.smart_plugs

    def test_mirrors_id_1_into_legacy_fan_smart_plug(
        self, startup_env, monkeypatch,
    ):
        db_path, state_module = startup_env
        _seed(db_path, "Hub fan", "192.0.2.20", plug_id=1)

        from mlss_monitor.app import _start_smart_plug_evaluator
        from mlss_monitor.effectors import evaluator
        monkeypatch.setattr(evaluator, "start_evaluator",
                            lambda: MagicMock(name="thread"))
        _start_smart_plug_evaluator(state_module)

        assert state_module.fan_smart_plug is state_module.smart_plugs[1]

    def test_legacy_fan_alias_left_untouched_when_id_1_absent(
        self, startup_env, monkeypatch,
    ):
        """When the migration hasn't seeded a fan (no env var on first
        boot), don't clobber any pre-existing fan_smart_plug value the
        rest of app.py might have set."""
        db_path, state_module = startup_env
        sentinel = MagicMock(name="pre-existing fan handle")
        state_module.fan_smart_plug = sentinel
        # No rows seeded → smart_plugs stays empty → fan_smart_plug
        # should keep its sentinel value.
        from mlss_monitor.app import _start_smart_plug_evaluator
        from mlss_monitor.effectors import evaluator
        monkeypatch.setattr(evaluator, "start_evaluator",
                            lambda: MagicMock(name="thread"))
        _start_smart_plug_evaluator(state_module)

        assert state_module.fan_smart_plug is sentinel

    def test_starts_evaluator_thread_and_stashes_handle(
        self, startup_env, monkeypatch,
    ):
        db_path, state_module = startup_env
        sentinel_thread = MagicMock(name="evaluator-thread")
        from mlss_monitor.app import _start_smart_plug_evaluator
        from mlss_monitor.effectors import evaluator
        monkeypatch.setattr(evaluator, "start_evaluator",
                            lambda: sentinel_thread)
        _start_smart_plug_evaluator(state_module)
        assert state_module.smart_plug_evaluator is sentinel_thread

"""Tests for ``mlss_monitor.effectors.evaluator``.

Coverage:

* ``evaluate_once()`` reads the live plug list from the store, asks
  the type-matched controller whether it should be on, and switches
  via the live plug handle.
* Skips disabled rows, non-auto rows, missing controllers, missing
  handles, and missing readings.
* Only flips the plug when the desired state differs from
  ``current_state`` (idempotence + de-dupe).
* Persists ``current_state`` via :func:`store.update_last_state`.
* Publishes ``effector_state_changed`` on the event bus.
* Grow-scope rows read from ``grow_telemetry`` (not the hot tier).

Pure unit tests against a tempfile DB; no Flask, no real network.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from database.init_db import create_db


@pytest.fixture
def eval_env(monkeypatch, tmp_path):
    """Schema-primed tempfile DB + one grow_unit row + monkeypatched DB
    paths everywhere the evaluator touches.

    Returns ``(db_path, state_module)`` so individual tests can seed
    smart_plugs rows + populate state.smart_plugs as needed.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.store.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.effectors.evaluator.DB_FILE", db_path)
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

    from mlss_monitor import state as state_module
    # The evaluator dispatches the live plug switch through
    # state.thread_loop via asyncio.run_coroutine_threadsafe. Stub the
    # whole asyncio entry point at module level so the test never
    # touches a real event loop.
    import mlss_monitor.effectors.evaluator as ev_module

    fake_future = MagicMock()
    fake_future.result.return_value = None

    def _fake_threadsafe(coro, loop):
        return fake_future

    monkeypatch.setattr(ev_module.asyncio, "run_coroutine_threadsafe",
                        _fake_threadsafe)
    monkeypatch.setattr(state_module, "thread_loop", MagicMock(),
                        raising=False)
    # Each test populates this freshly via the seed_plug helper below.
    monkeypatch.setattr(state_module, "smart_plugs", {}, raising=False)
    monkeypatch.setattr(state_module, "event_bus", None, raising=False)
    monkeypatch.setattr(state_module, "hot_tier", None, raising=False)
    return db_path, state_module


def _seed_hub_fan(db_path: str, *, auto_mode: int = 1,
                  is_enabled: int = 1, current_state: str = "off",
                  rules: dict | None = None) -> int:
    """Insert a hub-scope fan row with the seeded defaults; return its id."""
    import json
    rules = rules if rules is not None else {
        "temp_max": 20.0, "tvoc_max": 500,
        "humidity_max": 70.0, "pm25_max": 25.0,
        "temp_enabled": True, "tvoc_enabled": True,
        "humidity_enabled": False, "pm25_enabled": False,
    }
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO smart_plugs "
        "(label, effector_type, scope, kasa_host, protocol, "
        " is_enabled, auto_mode, rules_json, current_state, created_at) "
        "VALUES ('Room fan', 'fan', 'hub', ?, 'kasa', ?, ?, ?, ?, ?)",
        (
            f"192.0.2.{auto_mode * 10 + is_enabled}",
            is_enabled, auto_mode, json.dumps(rules), current_state, now,
        ),
    )
    conn.commit()
    plug_id = cur.lastrowid
    conn.close()
    return plug_id


def _stub_hub_reading(state_module, **fields):
    """Stand in for state.hot_tier — the evaluator calls .snapshot() on it.

    Keys mirror :class:`mlss_monitor.data_sources.base.NormalisedReading`
    as surfaced by :func:`dataclasses.asdict` — that's what the real
    :func:`mlss_monitor.effectors.evaluator._read_for_plug` returns to
    the controllers, so the test fixture must use the same shape. The
    earlier legacy-named version of this fixture hid a controller-side
    bug where every hub-scope reading was read as ``None`` → ``0.0``
    → all rules NO_OPINION → fan stayed off at 26°C in production
    (2026-05-31 incident — see
    ``test_effectors_dispatch.py``'s ``TestHubControllersReadCanonicalFieldNames``
    regression guard).
    """
    base = {
        "temperature_c": 18.0,
        "humidity_pct":  50.0,
        "eco2_ppm":      400,
        "tvoc_ppb":      100,
    }
    base.update(fields)
    hot_tier = MagicMock()
    hot_tier.snapshot.return_value = [base]
    state_module.hot_tier = hot_tier


# ── evaluate_once: hot-path ────────────────────────────────────────────────


class TestEvaluateOnceHubFan:
    def test_switches_on_when_rule_votes_on(self, eval_env):
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        mock_plug = MagicMock()
        state_module.smart_plugs = {plug_id: mock_plug}
        _stub_hub_reading(state_module, temperature_c=25.0)  # > 20.0 max

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        # The mock plug's switch() was awaited via the patched
        # run_coroutine_threadsafe; with the coroutine swallowed by
        # the fake, we can at least assert the controller asked for ON
        # by reading back the persisted current_state.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "on"

    def test_switches_off_when_rule_votes_off(self, eval_env):
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="on")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=15.0)  # cool

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "off"

    def test_skips_when_already_in_desired_state(self, eval_env, monkeypatch):
        """Idempotence: no switch + no DB write + no SSE publish when
        current_state already matches what the rule wants."""
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="on")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)  # ON desired

        # Sentinel last-updated stamp; if the evaluator writes again
        # the column will move.
        import mlss_monitor.effectors.store as store
        before = store.get_smart_plug(plug_id)["current_state_at"]

        bus = MagicMock()
        state_module.event_bus = bus

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        after = store.get_smart_plug(plug_id)["current_state_at"]
        assert before == after  # no UPDATE issued
        bus.publish.assert_not_called()

    def test_publishes_event_on_state_change(self, eval_env):
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)
        bus = MagicMock()
        state_module.event_bus = bus

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()
        bus.publish.assert_called_once()
        evt_name, payload = bus.publish.call_args[0]
        assert evt_name == "effector_state_changed"
        assert payload["id"] == plug_id
        assert payload["state"] == "on"
        assert payload["auto"] is True

    def test_persists_last_evaluation_on_every_pass(self, eval_env):
        """Side-panel "Why?" surface depends on a fresh evaluation blob
        every tick (not just on state changes) so operators see the
        latest rule-by-rule reasoning even when the fan stays ON."""
        import mlss_monitor.effectors.store as store_module
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)  # > 20.0 max

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        row = store_module.get_smart_plug(plug_id)
        assert row["last_evaluation"] is not None, (
            "evaluator must persist last_evaluation on every pass"
        )
        ev = row["last_evaluation"]
        assert ev["decision"] == "on"
        assert "evaluated_at" in ev
        assert isinstance(ev["reasons"], list)
        assert ev["reasons"], "fan controller produces at least one reason"
        # Temperature rule should have fired with a human-readable detail.
        temp_reasons = [r for r in ev["reasons"] if r["rule"] == "TemperatureRule"]
        assert temp_reasons and temp_reasons[0]["fired"] is True
        assert "25" in temp_reasons[0]["detail"]
        assert "20" in temp_reasons[0]["detail"]

    def test_persists_evaluation_even_when_decision_unchanged(self, eval_env):
        """Idempotence note: store.update_last_state and SSE publish are
        skipped when current_state matches the desired state — but the
        evaluation blob still gets refreshed so the side-panel timestamp
        keeps ticking."""
        import mlss_monitor.effectors.store as store_module
        db_path, state_module = eval_env
        # current_state already "on", and the reading keeps it ON.
        plug_id = _seed_hub_fan(db_path, current_state="on")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        row = store_module.get_smart_plug(plug_id)
        assert row["last_evaluation"] is not None
        assert row["last_evaluation"]["decision"] == "on"


# ── evaluate_once: skip conditions ─────────────────────────────────────────


class TestEvaluateOnceSkips:
    def test_skips_disabled_row(self, eval_env):
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, is_enabled=0, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        # Disabled row → no UPDATE → current_state remains 'off'
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "off"

    def test_skips_non_auto_row(self, eval_env):
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, auto_mode=0, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "off"

    def test_skips_when_no_live_handle(self, eval_env):
        """Plug row exists but state.smart_plugs has no entry — skip."""
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {}  # no entry for plug_id
        _stub_hub_reading(state_module, temperature_c=25.0)

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()  # Must NOT raise.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "off"

    def test_skips_when_no_reading_available(self, eval_env):
        """No hot_tier data + no telemetry → skip the plug entirely."""
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        # state.hot_tier is None (set by the fixture), so no reading
        # is available for hub-scope plugs.
        state_module.hot_tier = None

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()  # Must NOT raise.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "off"


# ── Grow-scope reading lookup ──────────────────────────────────────────────


class TestEvaluateOnceGrowScope:
    def test_reads_from_grow_telemetry(self, eval_env):
        """Grow-scope plug reads the latest grow_telemetry row for its unit."""
        import json
        db_path, state_module = eval_env
        # Seed a grow_telemetry row with soil_temp_c well below the
        # heat-pad's target so the controller votes ON.
        now = datetime.utcnow().isoformat()
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO grow_telemetry "
            "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
            " light_state, pump_state, soil_temp_c, ambient_lux, "
            " air_temp_c, air_humidity_pct) "
            "VALUES (1, ?, 1000, 40.0, 0, 0, 12.0, 1000, 22.0, 55.0)",
            (now,),
        )
        cur = conn.execute(
            "INSERT INTO smart_plugs "
            "(label, effector_type, scope, grow_unit_id, kasa_host, "
            " protocol, is_enabled, auto_mode, rules_json, "
            " current_state, created_at) "
            "VALUES ('Pad', 'heat_pad', 'grow_unit', 1, "
            "        '192.0.2.50', 'kasa', 1, 1, ?, 'off', ?)",
            (json.dumps({"target": 18.0}), now),
        )
        plug_id = cur.lastrowid
        conn.commit()
        conn.close()
        state_module.smart_plugs = {plug_id: MagicMock()}
        # hot_tier should NOT be consulted for grow-scope rows.
        state_module.hot_tier = MagicMock()
        state_module.hot_tier.snapshot.side_effect = AssertionError(
            "hot_tier consulted for grow-scope plug",
        )

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT current_state FROM smart_plugs WHERE id=?", (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "on"


# ── start_evaluator: daemon thread machinery ───────────────────────────────


class TestStateModuleSmartPlugsRegistry:
    """``state.smart_plugs`` is the runtime registry the evaluator + the
    v2 API both look up. The bare-import default must be a usable empty
    dict so test fixtures that don't explicitly initialise it (and the
    real app before app.py's startup hook fires) can ``state.smart_plugs.get(id)``
    without an AttributeError."""

    def test_smart_plugs_attribute_exists_as_dict(self):
        from mlss_monitor import state
        # NB: we can't reload here — other tests may have populated it.
        # Just assert the attribute is present + dict-like.
        assert hasattr(state, "smart_plugs")
        assert isinstance(state.smart_plugs, dict)

    def test_smart_plug_evaluator_handle_attribute_exists(self):
        from mlss_monitor import state
        assert hasattr(state, "smart_plug_evaluator")

    def test_legacy_fan_smart_plug_alias_still_present(self):
        """The legacy import path ``state.fan_smart_plug`` must remain
        until Phase 12 removes it (so every existing test fixture that
        monkeypatches it keeps working)."""
        from mlss_monitor import state
        assert hasattr(state, "fan_smart_plug")


class TestStartEvaluator:
    def test_returns_running_daemon_thread(self, eval_env, monkeypatch):
        """start_evaluator() spawns a daemon thread named 'effector-evaluator'."""
        # Make the loop bail immediately by patching the inner call;
        # we only care that the thread was launched.
        import mlss_monitor.effectors.evaluator as ev
        monkeypatch.setattr(ev, "EVAL_INTERVAL_S", 0.01)
        call_count = {"n": 0}
        def _fake_eval():
            call_count["n"] += 1
        monkeypatch.setattr(ev, "evaluate_once", _fake_eval)

        thread = ev.start_evaluator()
        try:
            assert thread.is_alive()
            assert thread.daemon
            assert thread.name == "effector-evaluator"
            # Give the loop one tick to confirm it actually runs.
            import time
            time.sleep(0.05)
            assert call_count["n"] >= 1
        finally:
            # The fixture's monkeypatches will tear down when the test
            # ends; the daemon thread dies with the process. No
            # graceful shutdown hook needed (loop only sleeps).
            pass


# ── Regression: deployed-test bugs ──────────────────────────────────────


class TestEvaluatorDeployedRegressions:
    """Bugs caught by the deployed test on the live hub. Each test is the
    minimum reproduction of an issue the unit tests above missed because
    they were mocking the shape too loosely.
    """

    def test_read_for_plug_coerces_NormalisedReading_to_dict(self, eval_env):
        """Bug: production hot_tier.snapshot() returns NormalisedReading
        dataclass instances, not dicts. The controllers call
        reading.get(...) which raised AttributeError, the whole tick
        aborted, and no plug ever got update_last_evaluation called —
        operators saw "Not yet evaluated" forever on the live page.
        """
        from dataclasses import dataclass

        @dataclass
        class FakeReading:
            temperature: float = 25.0
            humidity: float = 50.0
            eco2: int = 400
            tvoc: int = 100

        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        hot_tier = MagicMock()
        hot_tier.snapshot.return_value = [FakeReading()]
        state_module.hot_tier = hot_tier

        from mlss_monitor.effectors.evaluator import evaluate_once
        evaluate_once()

        # The Why? surface relies on this row being non-NULL after every
        # tick. Pre-fix the AttributeError prevented the persistence call.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT last_evaluation_json FROM smart_plugs WHERE id = ?",
                (plug_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] is not None, \
            "last_evaluation_json must populate when snapshot returns a dataclass"
        import json
        evaluation = json.loads(row[0])
        assert "decision" in evaluation
        assert "reasons" in evaluation
        assert len(evaluation["reasons"]) >= 1, \
            "Fan controller must populate per-rule reasons"

    def test_evaluate_once_does_not_raise_when_one_plug_evaluation_explodes(
        self, eval_env, monkeypatch,
    ):
        """Bug: a single raising plug bubbled up to the _loop()'s broad
        except and the whole tick aborted. The per-plug try/except inside
        evaluate_once is the belt-and-braces fix — log the bad row and
        continue iterating so the next tick's persistence still happens
        for healthy plugs.
        """
        db_path, state_module = eval_env
        plug_id = _seed_hub_fan(db_path, current_state="off")
        state_module.smart_plugs = {plug_id: MagicMock()}
        _stub_hub_reading(state_module, temperature_c=25.0)

        import mlss_monitor.effectors.evaluator as ev
        # Force _evaluate_one to raise — proves evaluate_once's outer
        # try/except eats the exception rather than propagating.
        def _blow_up(plug):
            raise RuntimeError("simulated controller crash")
        monkeypatch.setattr(ev, "_evaluate_one", _blow_up)

        # Must NOT raise — pre-fix the RuntimeError propagated out and
        # _loop()'s broad except swallowed the whole tick.
        ev.evaluate_once()

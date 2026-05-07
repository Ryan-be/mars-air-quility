"""Unit tests for mlss_monitor.grow.health_watchdog.

The watchdog is a small in-memory state machine consulted on each
GET /api/grow/units/<id>: did follow-up evidence (a watering_event for
pump, a light_state=1 telemetry row for light) arrive within
``timeout_s`` seconds of the last command we sent? If not, the GET
overlay flips the capability to ``unresponsive``.

The module is exercised end-to-end via three tests in
``test_grow_units_api.py`` + ``test_sense_only_mode_e2e.py``, but the
boundary cases (timeout edge, channel branches, lock-protected
concurrent writes, evidence outside the window) need direct coverage.

All tests call ``health_watchdog.clear()`` in setup/teardown so the
module-level ``_last_command_at`` dict doesn't leak between tests.
"""
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def db(monkeypatch):
    """Tmp DB + DB_FILE patched into the watchdog module + cleared state.

    The watchdog reads grow_watering_events / grow_telemetry directly via
    sqlite3 — no ORM — so we just patch DB_FILE on the module and seed
    the schema. ``clear()`` runs both before and after to guarantee
    isolation regardless of fixture ordering.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    from mlss_monitor.grow import health_watchdog
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.grow.health_watchdog.DB_FILE", tmp.name
    )
    init_db.create_db()

    # A unit row is required by the FK on grow_watering_events / telemetry
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    health_watchdog.clear()
    yield tmp.name
    health_watchdog.clear()


def _seed_watering_event(db_path: str, unit_id: int, ts: datetime) -> None:
    """Insert a single watering_event row at ``ts`` (UTC)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_watering_events "
        "(unit_id, timestamp_utc, trigger, duration_s, triggered_by) "
        "VALUES (?, ?, 'manual', 5, 'user')",
        (unit_id, ts),
    )
    conn.commit()
    conn.close()


def _seed_telemetry(db_path: str, unit_id: int, ts: datetime,
                    light_state: int = 0, pump_state: int = 0) -> None:
    """Insert a telemetry row at ``ts`` with the given actuator states."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
        " light_state, pump_state) VALUES (?, ?, ?, ?, ?, ?)",
        (unit_id, ts, 600, 50.0, light_state, pump_state),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# record_command_sent
# ---------------------------------------------------------------------------


def test_record_command_sent_stores_per_channel(db):
    """A record() call updates _last_command_at for the (unit, channel) key."""
    from mlss_monitor.grow import health_watchdog
    health_watchdog.record_command_sent(1, "pump")
    assert (1, "pump") in health_watchdog._last_command_at


def test_record_command_sent_with_explicit_at_uses_provided_time(db):
    """An explicit ``at=<dt>`` overrides the now() default."""
    from mlss_monitor.grow import health_watchdog
    fixed = datetime(2026, 5, 1, 12, 0, 0)
    health_watchdog.record_command_sent(1, "pump", at=fixed)
    assert health_watchdog._last_command_at[(1, "pump")] == fixed


def test_record_command_sent_default_at_is_now(db):
    """No ``at`` → recorded ~datetime.utcnow().

    Within 5 seconds of utcnow is a generous bound — covers slow CI hosts
    without false-flagging actual bugs."""
    from mlss_monitor.grow import health_watchdog
    before = datetime.utcnow()
    health_watchdog.record_command_sent(1, "pump")
    after = datetime.utcnow()
    recorded = health_watchdog._last_command_at[(1, "pump")]
    assert before - timedelta(seconds=5) <= recorded <= after + timedelta(seconds=5)


# ---------------------------------------------------------------------------
# check_unresponsive — timeout / evidence branches
# ---------------------------------------------------------------------------


def test_check_unresponsive_returns_false_when_no_command_recorded(db):
    """Idle pump with no command history → never unresponsive.

    The watchdog only reports on commands the server actually sent."""
    from mlss_monitor.grow import health_watchdog
    assert health_watchdog.check_unresponsive(1, "pump") is False


def test_check_unresponsive_returns_false_within_timeout(db):
    """Recorded 15s ago with timeout_s=30 → still in the grace window."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    health_watchdog.record_command_sent(1, "pump", at=now - timedelta(seconds=15))
    assert health_watchdog.check_unresponsive(
        1, "pump", timeout_s=30, now=now,
    ) is False


def test_check_unresponsive_returns_true_after_timeout_with_no_evidence(db):
    """Recorded 60s ago, timeout_s=30, no watering_event in window → unresponsive."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    health_watchdog.record_command_sent(1, "pump", at=now - timedelta(seconds=60))
    assert health_watchdog.check_unresponsive(
        1, "pump", timeout_s=30, now=now,
    ) is True


def test_check_unresponsive_returns_false_when_pump_evidence_arrived_after_command(db):
    """Pump command at T-60s + watering_event at T-30s (after command) → False.

    The event is the strongest evidence the pump fired — beats any
    timeout-based heuristic."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    cmd_at = now - timedelta(seconds=60)
    event_at = now - timedelta(seconds=30)  # after cmd_at, in the window
    health_watchdog.record_command_sent(1, "pump", at=cmd_at)
    _seed_watering_event(db, 1, event_at)
    assert health_watchdog.check_unresponsive(
        1, "pump", timeout_s=30, now=now,
    ) is False


def test_check_unresponsive_pump_evidence_not_in_window(db):
    """Pump command at T-60s + watering_event at T-120s (BEFORE command) → True.

    Stale evidence from before the command isn't evidence for *this* command."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    cmd_at = now - timedelta(seconds=60)
    event_at = now - timedelta(seconds=120)  # before cmd_at
    health_watchdog.record_command_sent(1, "pump", at=cmd_at)
    _seed_watering_event(db, 1, event_at)
    assert health_watchdog.check_unresponsive(
        1, "pump", timeout_s=30, now=now,
    ) is True


def test_check_unresponsive_for_light_uses_telemetry_light_state_evidence(db):
    """Light command at T-60s + telemetry with light_state=1 at T-30s → False.

    Telemetry is the canonical evidence channel for light (no
    light_event row equivalent — the firmware emits light_state in
    every telemetry frame)."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    cmd_at = now - timedelta(seconds=60)
    tele_at = now - timedelta(seconds=30)
    health_watchdog.record_command_sent(1, "light", at=cmd_at)
    _seed_telemetry(db, 1, tele_at, light_state=1)
    assert health_watchdog.check_unresponsive(
        1, "light", timeout_s=30, now=now,
    ) is False


def test_check_unresponsive_light_telemetry_off_does_not_count_as_evidence(db):
    """A telemetry row with light_state=0 doesn't prove the light reacted to
    a turn-on command — it actually proves the opposite. Should still
    flag unresponsive after the timeout."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    cmd_at = now - timedelta(seconds=60)
    tele_at = now - timedelta(seconds=30)
    health_watchdog.record_command_sent(1, "light", at=cmd_at)
    _seed_telemetry(db, 1, tele_at, light_state=0)
    assert health_watchdog.check_unresponsive(
        1, "light", timeout_s=30, now=now,
    ) is True


def test_check_unresponsive_for_unknown_channel_returns_false(db):
    """Unknown channel → defensive False (UI uses untested fallback).

    The watchdog can't reason about evidence channels it doesn't know;
    flagging would be a false-positive."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    health_watchdog.record_command_sent(1, "bogus", at=now - timedelta(seconds=60))
    assert health_watchdog.check_unresponsive(
        1, "bogus", timeout_s=30, now=now,
    ) is False


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------


def test_clear_resets_state(db):
    """clear() drops every recorded (unit, channel) → check returns False."""
    from mlss_monitor.grow import health_watchdog
    health_watchdog.record_command_sent(1, "pump")
    health_watchdog.record_command_sent(2, "light")
    assert health_watchdog._last_command_at  # populated
    health_watchdog.clear()
    assert not health_watchdog._last_command_at


# ---------------------------------------------------------------------------
# Internal helpers — direct coverage of the SQL boundary
# ---------------------------------------------------------------------------


def test_has_pump_evidence_since_returns_true_when_event_after_since(db):
    """Direct test of the private _has_pump_evidence_since helper.

    We test this because the public ``check_unresponsive`` masks the
    helper's failure modes (e.g. SQL syntax error) behind a boolean,
    making them hard to debug if the helper itself regresses."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    _seed_watering_event(db, 1, now - timedelta(seconds=10))
    assert health_watchdog._has_pump_evidence_since(
        1, now - timedelta(seconds=60),
    ) is True


def test_has_pump_evidence_since_returns_false_when_no_event_in_window(db):
    """Empty table → False. Stale event before the cutoff → False."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    # Empty table
    assert health_watchdog._has_pump_evidence_since(1, now) is False
    # Event before the cutoff
    _seed_watering_event(db, 1, now - timedelta(seconds=120))
    assert health_watchdog._has_pump_evidence_since(
        1, now - timedelta(seconds=60),
    ) is False


def test_has_light_evidence_since_requires_light_state_one(db):
    """light_state=0 telemetry doesn't count; only light_state=1 does."""
    from mlss_monitor.grow import health_watchdog
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=60)

    _seed_telemetry(db, 1, now - timedelta(seconds=30), light_state=0)
    assert health_watchdog._has_light_evidence_since(1, cutoff) is False

    _seed_telemetry(db, 1, now - timedelta(seconds=10), light_state=1)
    assert health_watchdog._has_light_evidence_since(1, cutoff) is True


# ---------------------------------------------------------------------------
# Concurrent writes — the lock-protected dict
# ---------------------------------------------------------------------------


def test_thread_safe_record_doesnt_drop_concurrent_writes(db):
    """Two threads recording for different (unit, channel) pairs in
    parallel must both end up in the dict.

    We don't exercise serialisability of writes to the *same* key (that's
    a property of dict.__setitem__ + the lock; harder to assert and not
    actually load-bearing for the watchdog's correctness — the last
    write wins is fine semantically). We just make sure the lock doesn't
    silently drop one of two distinct-key writes."""
    from mlss_monitor.grow import health_watchdog
    health_watchdog.clear()

    barrier = threading.Barrier(2)

    def writer(unit_id: int, channel: str):
        barrier.wait()  # Maximise the chance of a real race
        for _ in range(50):
            health_watchdog.record_command_sent(unit_id, channel)

    t1 = threading.Thread(target=writer, args=(1, "pump"))
    t2 = threading.Thread(target=writer, args=(2, "light"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert (1, "pump") in health_watchdog._last_command_at
    assert (2, "light") in health_watchdog._last_command_at

"""Connection-event writer for the WS listener — Phase 3 Task 1.

The writer is best-effort: a DB error must NOT tear down the WS handler,
so the function swallows exceptions and emits a WARNING. The semantics:
  - online  → severity='info', also resolves any open kind='offline' row
              for this unit (so the row pair records the outage duration).
  - offline → severity='warning', always inserts (a reconnect storm
              leaving multiple open offline rows is itself diagnostic;
              we don't dedupe).
"""
import logging
import sqlite3
from datetime import datetime, timedelta

import pytest

from database.init_db import create_db


@pytest.fixture
def writer_env(tmp_path, monkeypatch):
    """Fresh DB + a seeded grow_unit. Patches DB_FILE on init_db AND on
    api_grow_ws so the writer reads/writes the same file."""
    db_path = str(tmp_path / "grow_conn.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", db_path)
    create_db()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) "
        "VALUES (1, 'hw1', 'X', ?, 'hash', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    return db_path


def _rows(db_path, where="1=1", params=()):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        f"SELECT id, unit_id, severity, kind, message, resolved_at "
        f"FROM grow_errors WHERE {where} ORDER BY id",
        params,
    ).fetchall()
    conn.close()
    return rows


def test_record_connection_event_online_inserts_grow_errors_row(writer_env):
    """A bare 'online' call inserts an info-severity 'online' row with
    resolved_at=NULL (the row stays open until... well, indefinitely; the
    pair is the *previous* offline row + this online row)."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    _record_connection_event(1, "online")

    rows = _rows(writer_env, "unit_id=1")
    assert len(rows) == 1, f"expected 1 row, got {rows}"
    _id, unit_id, severity, kind, message, resolved_at = rows[0]
    assert unit_id == 1
    assert severity == "info"
    assert kind == "online"
    assert resolved_at is None
    assert "online" in message.lower()


def test_record_connection_event_offline_inserts_warning_row(writer_env):
    """Offline → severity='warning'. Operators want offline rows to surface
    in a default 'warning+' filter on the errors page."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    _record_connection_event(1, "offline")

    rows = _rows(writer_env, "unit_id=1")
    assert len(rows) == 1
    _id, _unit, severity, kind, _msg, resolved_at = rows[0]
    assert severity == "warning"
    assert kind == "offline"
    assert resolved_at is None


def test_record_connection_event_online_resolves_prior_open_offline(writer_env):
    """When a unit comes back online, its open offline row gets resolved_at
    set. This is what lets the UI compute outage duration as
    (online.timestamp_utc - offline.timestamp_utc) ≈ resolved_at - timestamp_utc."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    # Seed an open offline row from a prior outage
    earlier = datetime.utcnow() - timedelta(minutes=5)
    conn = sqlite3.connect(writer_env)
    conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message) "
        "VALUES (1, ?, 'warning', 'offline', 'unit offline')",
        (earlier,),
    )
    conn.commit()
    conn.close()

    _record_connection_event(1, "online")

    # The seeded offline row must now be resolved
    conn = sqlite3.connect(writer_env)
    offline_rows = conn.execute(
        "SELECT id, resolved_at FROM grow_errors "
        "WHERE unit_id=1 AND kind='offline'"
    ).fetchall()
    online_rows = conn.execute(
        "SELECT id, resolved_at FROM grow_errors "
        "WHERE unit_id=1 AND kind='online'"
    ).fetchall()
    conn.close()

    assert len(offline_rows) == 1
    assert offline_rows[0][1] is not None, (
        "prior open offline row must be resolved by the online event"
    )
    assert len(online_rows) == 1
    assert online_rows[0][1] is None, "online row itself stays open"


def test_record_connection_event_online_does_not_resolve_already_resolved_offline_row(
    writer_env,
):
    """Already-resolved offline rows must NOT have their resolved_at
    overwritten by a fresh online event. The UPDATE is filtered on
    resolved_at IS NULL precisely to preserve historical durations."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    earlier_offline = datetime.utcnow() - timedelta(hours=2)
    earlier_resolved = datetime.utcnow() - timedelta(hours=1, minutes=30)

    conn = sqlite3.connect(writer_env)
    conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message, resolved_at) "
        "VALUES (1, ?, 'warning', 'offline', 'old outage', ?)",
        (earlier_offline, earlier_resolved),
    )
    conn.commit()
    conn.close()

    _record_connection_event(1, "online")

    conn = sqlite3.connect(writer_env)
    row = conn.execute(
        "SELECT resolved_at FROM grow_errors "
        "WHERE unit_id=1 AND kind='offline'"
    ).fetchone()
    conn.close()

    # The resolved_at on the historical row should still equal what we seeded.
    assert row[0] is not None
    # Compare as strings — sqlite default adapter uses 'YYYY-MM-DD HH:MM:SS.ffffff'
    # (space, not T). Match on the leading 19 chars (Y-M-D H:M:S) which
    # uniquely identify the seeded value within test runtime.
    expected_prefix = earlier_resolved.isoformat(sep=" ")[:19]
    assert str(row[0]).startswith(expected_prefix), (
        f"historical resolved_at clobbered: stored={row[0]!r}, "
        f"expected~={expected_prefix!r}"
    )


def test_record_connection_event_offline_does_not_clobber_existing_open_offline_row(
    writer_env,
):
    """Calling offline twice in a row must NOT dedupe: the second call
    inserts another open offline row. Reconnect storms leaving multiple
    open offlines are diagnostic — we want to see them, not hide them."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    _record_connection_event(1, "offline")
    _record_connection_event(1, "offline")

    rows = _rows(writer_env, "unit_id=1 AND kind='offline'")
    assert len(rows) == 2, (
        f"expected two open offline rows after two offline calls; got {rows}"
    )
    # Both must have resolved_at IS NULL — neither resolves the other.
    for row in rows:
        assert row[5] is None, f"unexpected resolved_at on row {row!r}"


def test_record_connection_event_db_failure_does_not_raise(monkeypatch, writer_env):
    """Whole point of best-effort: even if sqlite3.connect explodes, the
    caller (the WS handler) must keep going. Function returns None."""
    from mlss_monitor.routes import api_grow_ws as mod

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(mod.sqlite3, "connect", _boom)

    # No raise allowed. _record_connection_event returns None by design;
    # we deliberately bind the return to assert it stays None even when
    # the underlying sqlite3.connect blows up.
    result = mod._record_connection_event(1, "online")  # pylint: disable=assignment-from-no-return
    assert result is None


def test_record_connection_event_db_failure_logs_warning(
    monkeypatch, writer_env, caplog,
):
    """When the DB write fails, the writer must log a WARNING so ops can
    see audit-row writes failing (silent swallow would hide a real
    problem like the disk filling)."""
    from mlss_monitor.routes import api_grow_ws as mod

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(mod.sqlite3, "connect", _boom)

    with caplog.at_level(logging.WARNING, logger="mlss_monitor.routes.api_grow_ws"):
        mod._record_connection_event(42, "offline")

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("connection_event" in r.getMessage() for r in warnings), (
        f"expected a WARNING mentioning connection_event; got "
        f"{[r.getMessage() for r in warnings]}"
    )
    # Unit id should appear in the message so ops can tell which unit failed
    assert any("42" in r.getMessage() for r in warnings)


def test_record_connection_event_online_resolves_only_open_offline_for_same_unit(
    writer_env,
):
    """A unit's online event must NOT resolve an open offline row for a
    DIFFERENT unit. The UPDATE is scoped on unit_id."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    # Seed unit 2 in addition to unit 1
    conn = sqlite3.connect(writer_env)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) "
        "VALUES (2, 'hw2', 'Y', ?, 'hash2', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    # Each unit has an open offline row
    earlier = datetime.utcnow() - timedelta(minutes=5)
    for uid in (1, 2):
        conn.execute(
            "INSERT INTO grow_errors "
            "(unit_id, timestamp_utc, severity, kind, message) "
            "VALUES (?, ?, 'warning', 'offline', 'unit offline')",
            (uid, earlier),
        )
    conn.commit()
    conn.close()

    _record_connection_event(1, "online")

    conn = sqlite3.connect(writer_env)
    u1 = conn.execute(
        "SELECT resolved_at FROM grow_errors "
        "WHERE unit_id=1 AND kind='offline'"
    ).fetchone()
    u2 = conn.execute(
        "SELECT resolved_at FROM grow_errors "
        "WHERE unit_id=2 AND kind='offline'"
    ).fetchone()
    conn.close()

    assert u1[0] is not None, "unit 1's offline row must be resolved"
    assert u2[0] is None, "unit 2's offline row must remain open"

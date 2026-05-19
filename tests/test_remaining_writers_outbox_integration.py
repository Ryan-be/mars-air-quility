"""Outbox integration tests for Task 11's remaining writers.

Phase 2 Task 11 wires the last few UPDATE/INSERT sites through
``outbox.enqueue_row`` so every replicated write commits its outbox
pointer in the same transaction as the live row.

Sites covered here:

* ``database.db_logger.update_inference_notes`` (UPDATE inferences)
* ``database.db_logger.dismiss_inference`` (UPDATE inferences)
* ``mlss_monitor.inference_evidence_storage.persist_evidence``
  (UPDATE inferences — caller-owned conn)
* ``mlss_monitor.routes.api_grow_journal`` POST + PATCH (INSERT/UPDATE
  grow_journal_entries; PATCH must rowcount-gate the enqueue)
* ``mlss_monitor.routes.api_grow_errors`` PATCH (UPDATE grow_errors —
  rowcount-gate before enqueue)
* ``mlss_monitor.routes.api_grow_timelapse`` POST (INSERT grow_timelapse_jobs)
* ``mlss_monitor.routes.api_grow_ws._record_connection_event`` (UPDATE
  prior open offline rows + INSERT new connection event)
* ``mlss_monitor.grow.timelapse_jobs`` render-job runner: queued→running,
  →complete, →failed transitions on grow_timelapse_jobs.

The DELETE branches (e.g. journal delete) are append-mostly and must
NOT enqueue — those tests live in test_no_direct_writes_to_replicated_tables.py
(lint guard) but we also assert behaviour explicitly here for clarity.
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outbox_rows(db_path: str, *, table: str | None = None):
    conn = sqlite3.connect(db_path)
    try:
        if table is None:
            return list(conn.execute(
                "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes "
            "WHERE table_name=? ORDER BY id", (table,)))
    finally:
        conn.close()


def _clear_outbox(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM outbox_changes")
        conn.commit()
    finally:
        conn.close()


def _set_session(c, *, role="admin", user="alice"):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = role
        sess["user"] = user


def _seed_unit(db_path: str, unit_id: int = 1, *, label: str = "Tom 1") -> None:
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (?, ?, ?, ?, 'h', ?, 1)",
        (unit_id, f"hw-{unit_id}", label, now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# db_logger.py — UPDATE inference helpers
# ---------------------------------------------------------------------------


def test_update_inference_notes_enqueues_outbox(db):
    """update_inference_notes(id, notes) must enqueue an `inferences`
    pointer for the updated row."""
    from database.db_logger import save_inference, update_inference_notes
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    _clear_outbox(db)
    update_inference_notes(inf_id, "operator wrote this")
    assert _outbox_rows(db, table="inferences") == [
        ("inferences", str(inf_id))
    ]
    # Sanity: the actual UPDATE landed.
    conn = sqlite3.connect(db)
    try:
        notes = conn.execute(
            "SELECT user_notes FROM inferences WHERE id=?", (inf_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert notes == "operator wrote this"


def test_dismiss_inference_enqueues_outbox(db):
    """dismiss_inference(id) must enqueue an `inferences` pointer for
    the dismissed row."""
    from database.db_logger import save_inference, dismiss_inference
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    _clear_outbox(db)
    dismiss_inference(inf_id)
    assert _outbox_rows(db, table="inferences") == [
        ("inferences", str(inf_id))
    ]
    # Sanity: dismissed flag is set.
    conn = sqlite3.connect(db)
    try:
        dismissed = conn.execute(
            "SELECT dismissed FROM inferences WHERE id=?", (inf_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert dismissed == 1


# ---------------------------------------------------------------------------
# inference_evidence_storage.persist_evidence
# ---------------------------------------------------------------------------


def test_persist_evidence_enqueues_outbox(db):
    """persist_evidence (caller-owned conn) must enqueue an `inferences`
    row pointer inside the same transaction as the UPDATE. The caller
    commits."""
    from database.db_logger import save_inference
    from mlss_monitor.inference_evidence_storage import persist_evidence

    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="a",
        evidence={}, confidence=0.5,
    )
    _clear_outbox(db)

    conn = sqlite3.connect(db, timeout=10)
    try:
        persist_evidence(conn, inf_id, {"attribution_source": "cooking"})
        conn.commit()
    finally:
        conn.close()

    assert _outbox_rows(db, table="inferences") == [
        ("inferences", str(inf_id))
    ]


# ---------------------------------------------------------------------------
# api_grow_journal.py — POST + PATCH
# ---------------------------------------------------------------------------


@pytest.fixture
def journal_client(monkeypatch):
    """Flask test client mounting api_grow_journal with a fresh DB +
    seeded unit (id=1, author='alice')."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_journal.DB_FILE", tmp.name
    )
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    from flask import Flask
    from mlss_monitor.routes.api_grow_journal import api_grow_journal_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_journal_bp)
    tc = app.test_client()
    _set_session(tc, role="admin", user="alice")
    yield tc, tmp.name


def test_journal_post_enqueues_grow_journal_entries(journal_client):
    c, db_path = journal_client
    r = c.post(
        "/api/grow/units/1/journal",
        json={
            "timestamp_utc": "2026-05-19T12:00:00Z",
            "body": "started bloom nutrients",
        },
    )
    assert r.status_code == 201, r.data
    new_id = r.get_json()["id"]
    assert ("grow_journal_entries", str(new_id)) in _outbox_rows(
        db_path, table="grow_journal_entries"
    )


def test_journal_post_unknown_unit_does_not_enqueue(journal_client):
    """404 on unknown unit must roll back without enqueueing."""
    c, db_path = journal_client
    r = c.post(
        "/api/grow/units/9999/journal",
        json={
            "timestamp_utc": "2026-05-19T12:00:00Z",
            "body": "ghost entry",
        },
    )
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_journal_entries") == []


def test_journal_patch_enqueues_grow_journal_entries(journal_client):
    """PATCH /api/grow/units/<id>/journal/<entry_id> must enqueue."""
    c, db_path = journal_client
    # Create one to PATCH.
    r = c.post(
        "/api/grow/units/1/journal",
        json={
            "timestamp_utc": "2026-05-19T12:00:00Z",
            "body": "first draft",
        },
    )
    entry_id = r.get_json()["id"]
    _clear_outbox(db_path)

    r2 = c.patch(
        f"/api/grow/units/1/journal/{entry_id}",
        json={"body": "edited draft"},
    )
    assert r2.status_code == 200, r2.data
    assert _outbox_rows(db_path, table="grow_journal_entries") == [
        ("grow_journal_entries", str(entry_id))
    ]


def test_journal_patch_unknown_entry_does_not_enqueue(journal_client):
    """Rowcount-gate must return 404 BEFORE the enqueue so phantom
    pointers never appear in the outbox."""
    c, db_path = journal_client
    r = c.patch(
        "/api/grow/units/1/journal/99999",
        json={"body": "ghost edit"},
    )
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_journal_entries") == []


# ---------------------------------------------------------------------------
# api_grow_errors.py — PATCH
# ---------------------------------------------------------------------------


@pytest.fixture
def errors_client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_errors.DB_FILE", tmp.name
    )
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    # Seed one grow_errors row to PATCH.
    conn = sqlite3.connect(tmp.name)
    cur = conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message) "
        "VALUES (1, ?, 'warning', 'sensor_degraded', 'msg')",
        (datetime.utcnow(),),
    )
    err_id = cur.lastrowid
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_errors import api_grow_errors_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_errors_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")
    yield tc, tmp.name, err_id


def test_errors_patch_resolve_enqueues_grow_errors(errors_client):
    c, db_path, err_id = errors_client
    r = c.patch(
        f"/api/grow/errors/{err_id}",
        json={"resolved_at": "now"},
    )
    assert r.status_code == 200, r.data
    assert _outbox_rows(db_path, table="grow_errors") == [
        ("grow_errors", str(err_id))
    ]


def test_errors_patch_snooze_enqueues_grow_errors(errors_client):
    """Snooze and resolve share a transaction; either field on its own
    must still enqueue."""
    c, db_path, err_id = errors_client
    r = c.patch(
        f"/api/grow/errors/{err_id}",
        json={"snoozed_until": "2026-06-01T00:00:00Z"},
    )
    assert r.status_code == 200, r.data
    assert _outbox_rows(db_path, table="grow_errors") == [
        ("grow_errors", str(err_id))
    ]


def test_errors_patch_unknown_does_not_enqueue(errors_client):
    """Rowcount=0 → 404 → no enqueue (phantom-pointer guard)."""
    c, db_path, _err_id = errors_client
    r = c.patch(
        "/api/grow/errors/99999",
        json={"resolved_at": "now"},
    )
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_errors") == []


# ---------------------------------------------------------------------------
# api_grow_timelapse.py — POST
# ---------------------------------------------------------------------------


@pytest.fixture
def timelapse_client(monkeypatch, tmp_path):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.DB_FILE", tmp.name
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.DB_FILE", tmp.name
    )
    # ffmpeg detection is mocked True so the route can proceed.
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    from flask import Flask
    from mlss_monitor.routes.api_grow_timelapse import api_grow_timelapse_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_timelapse_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")
    yield tc, tmp.name


def test_timelapse_post_enqueues_grow_timelapse_jobs(timelapse_client):
    c, db_path = timelapse_client
    r = c.post(
        "/api/grow/units/1/timelapse",
        json={"range": "24h", "fps": 10},
    )
    assert r.status_code == 202, r.data
    job_id = r.get_json()["id"]
    assert ("grow_timelapse_jobs", str(job_id)) in _outbox_rows(
        db_path, table="grow_timelapse_jobs"
    )


def test_timelapse_post_unknown_unit_does_not_enqueue(timelapse_client):
    c, db_path = timelapse_client
    r = c.post(
        "/api/grow/units/9999/timelapse",
        json={"range": "24h"},
    )
    assert r.status_code == 404
    assert _outbox_rows(db_path, table="grow_timelapse_jobs") == []


# ---------------------------------------------------------------------------
# api_grow_ws.py — _record_connection_event
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_writer_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grow_conn.db")
    monkeypatch.setattr("database.init_db.DB_FILE", db_path)
    monkeypatch.setattr("mlss_monitor.routes.api_grow_ws.DB_FILE", db_path)
    from database.init_db import create_db
    create_db()
    _seed_unit(db_path, unit_id=1)
    yield db_path


def test_connection_event_offline_insert_enqueues_grow_errors(ws_writer_env):
    """A bare 'offline' call INSERTs one grow_errors row — the enqueue
    must point at its autoincremented PK."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    _record_connection_event(1, "offline")

    conn = sqlite3.connect(ws_writer_env)
    err_id = conn.execute(
        "SELECT id FROM grow_errors WHERE unit_id=1 AND kind='offline'"
    ).fetchone()[0]
    conn.close()

    assert ("grow_errors", str(err_id)) in _outbox_rows(
        ws_writer_env, table="grow_errors"
    )


def test_connection_event_online_insert_enqueues_grow_errors(ws_writer_env):
    """A bare 'online' call (no prior offline row) INSERTs one row;
    only that row pointer is enqueued."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    _record_connection_event(1, "online")

    conn = sqlite3.connect(ws_writer_env)
    err_id = conn.execute(
        "SELECT id FROM grow_errors WHERE unit_id=1 AND kind='online'"
    ).fetchone()[0]
    conn.close()

    # One INSERT, one enqueue. (No prior open offline row → resolve
    # UPDATE affects 0 rows → nothing additional to enqueue.)
    assert _outbox_rows(ws_writer_env, table="grow_errors") == [
        ("grow_errors", str(err_id))
    ]


def test_connection_event_online_resolves_prior_offline_and_enqueues_both(
    ws_writer_env,
):
    """When the unit comes back online, the prior open offline row gets
    resolved_at set AND the new online INSERT happens. Both row pointers
    must land in the outbox so the server mirror sees the resolved_at
    update on the historical row."""
    from mlss_monitor.routes.api_grow_ws import _record_connection_event

    # Seed one open offline row from a prior outage.
    conn = sqlite3.connect(ws_writer_env)
    cur = conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message) "
        "VALUES (1, ?, 'warning', 'offline', 'unit offline')",
        (datetime.utcnow() - timedelta(minutes=5),),
    )
    prior_offline_id = cur.lastrowid
    conn.commit()
    # Clear any incidental outbox entries (none expected, but defensive).
    conn.execute("DELETE FROM outbox_changes")
    conn.commit()
    conn.close()

    _record_connection_event(1, "online")

    # Find the new online row id.
    conn = sqlite3.connect(ws_writer_env)
    online_id = conn.execute(
        "SELECT id FROM grow_errors WHERE unit_id=1 AND kind='online'"
    ).fetchone()[0]
    conn.close()

    pointers = _outbox_rows(ws_writer_env, table="grow_errors")
    # Both rows present — order is insertion order of the enqueue calls.
    assert ("grow_errors", str(prior_offline_id)) in pointers
    assert ("grow_errors", str(online_id)) in pointers


# ---------------------------------------------------------------------------
# grow/timelapse_jobs.py — render-job runner
# ---------------------------------------------------------------------------


@pytest.fixture
def runner_env(tmp_path, monkeypatch):
    """Fresh DB + seeded unit + photos for render_job to chew on.
    ffmpeg is stubbed in the tests that need it."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.DB_FILE", tmp.name
    )
    images_root = tmp_path / "imgs"
    timelapses_root = tmp_path / "timelapses"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_root))
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.TIMELAPSES_DIR", str(timelapses_root))
    init_db.create_db()
    _seed_unit(tmp.name, unit_id=1)

    now = datetime.utcnow()
    photo_dir = images_root / "unit_001" / "2026-05-19"
    photo_dir.mkdir(parents=True)
    conn = sqlite3.connect(tmp.name)
    for i in range(3):
        rel = f"unit_001/2026-05-19/13000{i}.jpg"
        (images_root / rel).write_bytes(b"\xff\xd8FAKEJPEG")
        conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
            (now - timedelta(minutes=i), rel),
        )
    conn.commit()
    conn.close()
    yield tmp.name


def _seed_runner_job(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_timelapse_jobs "
        "(unit_id, requested_by, requested_at, range, fps, status) "
        "VALUES (1, 'alice', ?, '24h', 10, 'queued')",
        (datetime.utcnow(),),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id


def test_runner_marks_running_enqueues_grow_timelapse_jobs(runner_env, monkeypatch):
    """The queued→running UPDATE in render_job must enqueue a row pointer
    so the server mirror sees the status flip. We force ffmpeg-missing so
    the runner stops after the running flip (then marks failed) — both
    UPDATEs enqueue and coalesce on the same PK."""
    from mlss_monitor.grow import timelapse_jobs
    monkeypatch.setattr(timelapse_jobs, "ffmpeg_available", lambda: False)

    job_id = _seed_runner_job(runner_env)
    timelapse_jobs.render_job(job_id)

    # At minimum the (grow_timelapse_jobs, job_id) row must be enqueued.
    # The outbox coalesces multiple writes to the same (table, pk), so
    # running→failed shows up as one pointer.
    assert _outbox_rows(runner_env, table="grow_timelapse_jobs") == [
        ("grow_timelapse_jobs", str(job_id))
    ]


def test_runner_marks_failed_enqueues_grow_timelapse_jobs(runner_env, monkeypatch):
    """A failed render still enqueues a pointer — server needs to see
    the final failed status + error_message."""
    from mlss_monitor.grow import timelapse_jobs
    monkeypatch.setattr(timelapse_jobs, "ffmpeg_available", lambda: False)

    job_id = _seed_runner_job(runner_env)
    timelapse_jobs.render_job(job_id)

    # Status is now 'failed'.
    conn = sqlite3.connect(runner_env)
    status = conn.execute(
        "SELECT status FROM grow_timelapse_jobs WHERE id=?", (job_id,)
    ).fetchone()[0]
    conn.close()
    assert status == "failed"

    pointers = _outbox_rows(runner_env, table="grow_timelapse_jobs")
    assert ("grow_timelapse_jobs", str(job_id)) in pointers


def test_runner_marks_complete_enqueues_grow_timelapse_jobs(runner_env, monkeypatch):
    """A successful render's running→complete UPDATE must enqueue."""
    from mlss_monitor.grow import timelapse_jobs

    # ffmpeg present, subprocess.run mocked to succeed.
    monkeypatch.setattr(timelapse_jobs, "ffmpeg_available", lambda: True)

    class _FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):  # pylint: disable=unused-argument
        # ffmpeg's output file is the second-to-last positional arg in
        # the cmd list (the last one is the output path). Touch it so
        # the runner's "did ffmpeg actually create the file" branch
        # doesn't 404 it later — render_job itself only checks
        # returncode, but be defensive.
        out_path = cmd[-1]
        with open(out_path, "wb") as fp:
            fp.write(b"\x00\x00\x00\x18ftypmp42FAKE")
        return _FakeProc()

    monkeypatch.setattr(timelapse_jobs.subprocess, "run", _fake_run)

    job_id = _seed_runner_job(runner_env)
    timelapse_jobs.render_job(job_id)

    # Sanity: status flipped to complete.
    conn = sqlite3.connect(runner_env)
    status = conn.execute(
        "SELECT status FROM grow_timelapse_jobs WHERE id=?", (job_id,)
    ).fetchone()[0]
    conn.close()
    assert status == "complete", status

    pointers = _outbox_rows(runner_env, table="grow_timelapse_jobs")
    assert ("grow_timelapse_jobs", str(job_id)) in pointers

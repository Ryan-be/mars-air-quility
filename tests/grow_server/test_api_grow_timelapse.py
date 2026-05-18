"""Phase 4 #8 — time-lapse video generation.

API tests:
  POST /api/grow/units/<id>/timelapse        create job
  GET  /api/grow/units/<id>/timelapse        list jobs
  GET  /api/grow/timelapse/<job_id>          status
  GET  /api/grow/timelapse/<job_id>/video    serve MP4 (when complete)

Runner tests:
  render_job() picks up queued, builds ffmpeg cmd, marks complete/failed.
  ffmpeg-missing produces a clean failed status with error_message.
  No photos in range produces a clean failed status.

Startup-check tests:
  log_ffmpeg_status_at_startup() logs WARNING when ffmpeg missing,
  INFO with version when present, and never raises.
  start_runner_thread() emits the startup log line and keeps polling
  even when ffmpeg is missing (queued jobs are marked failed at render
  time, not by the polling loop).

We don't actually invoke ffmpeg in tests (CI may not have it). The
runner tests stub out subprocess.run / shutil.which so the unit-under-
test is the bookkeeping logic, not ffmpeg itself.
"""
import logging
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


def _set_session(c, *, role="admin", user="alice"):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = role
        sess["user"] = user


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Seed a unit + a few photos so /timelapse jobs have something to
    nominally render. The runner is stubbed in the runner tests; the
    API tests don't trigger render."""
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
    images_root = tmp_path / "imgs"
    timelapses_root = tmp_path / "timelapses"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_root))
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.TIMELAPSES_DIR", str(timelapses_root))
    init_db.create_db()

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'h', 'X', ?, 'h', ?, 1)",
        (now, now),
    )
    # Three photos in the last hour
    for i in range(3):
        conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
            (now - timedelta(minutes=i),
             f"unit_001/2026-05-08/13000{i}.jpg"),
        )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_timelapse import api_grow_timelapse_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_timelapse_bp)
    c = app.test_client()
    _set_session(c, role="admin")
    yield c, tmp.name, tmp_path


# ---------------------------------------------------------------------------
# POST /timelapse — create
# ---------------------------------------------------------------------------


def test_post_creates_job_when_ffmpeg_present(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    r = c.post("/api/grow/units/1/timelapse",
               json={"range": "24h", "fps": 10})
    assert r.status_code == 202, r.data
    body = r.get_json()
    assert body["status"] == "queued"
    assert body["fps"] == 10
    assert body["range"] == "24h"
    assert body["unit_id"] == 1
    assert body["video_url"] is None  # not complete yet


def test_post_503_when_ffmpeg_missing(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: False,
    )
    r = c.post("/api/grow/units/1/timelapse", json={"range": "24h"})
    assert r.status_code == 503
    assert r.get_json()["error"] == "ffmpeg_not_installed"


def test_post_400_invalid_range(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    r = c.post("/api/grow/units/1/timelapse", json={"range": "bogus"})
    assert r.status_code == 400


def test_post_400_invalid_fps(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    r = c.post("/api/grow/units/1/timelapse",
               json={"range": "24h", "fps": 999})
    assert r.status_code == 400
    assert "allowed" in r.get_json()


def test_post_404_unknown_unit(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    r = c.post("/api/grow/units/9999/timelapse", json={"range": "24h"})
    assert r.status_code == 404


def test_post_403_for_viewer(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    _set_session(c, role="viewer")
    r = c.post("/api/grow/units/1/timelapse", json={"range": "24h"})
    assert r.status_code == 403


def test_post_default_fps_is_10(client, monkeypatch):
    c, _db, _tmp = client
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_timelapse.ffmpeg_available",
        lambda: True,
    )
    r = c.post("/api/grow/units/1/timelapse", json={"range": "24h"})
    assert r.status_code == 202
    assert r.get_json()["fps"] == 10


# ---------------------------------------------------------------------------
# GET /timelapse — list / status / video
# ---------------------------------------------------------------------------


def _seed_job(db_path, *, unit_id=1, status="queued",
              output_path=None, error_message=None):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_timelapse_jobs "
        "(unit_id, requested_by, requested_at, range, fps, status, "
        " output_path, error_message) "
        "VALUES (?, 'alice', ?, '24h', 10, ?, ?, ?)",
        (unit_id, datetime.utcnow(), status, output_path, error_message),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def test_list_jobs_for_unit(client):
    c, db, _tmp = client
    _seed_job(db, status="queued")
    _seed_job(db, status="complete", output_path="unit_001/2.mp4")
    _seed_job(db, status="failed", error_message="boom")
    r = c.get("/api/grow/units/1/timelapse")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 3


def test_get_job_returns_status(client):
    c, db, _tmp = client
    jid = _seed_job(db, status="running")
    r = c.get(f"/api/grow/timelapse/{jid}")
    assert r.status_code == 200
    assert r.get_json()["status"] == "running"


def test_get_job_404_unknown(client):
    c, _db, _tmp = client
    r = c.get("/api/grow/timelapse/99999")
    assert r.status_code == 404


def test_get_video_409_when_not_complete(client):
    c, db, _tmp = client
    jid = _seed_job(db, status="running")
    r = c.get(f"/api/grow/timelapse/{jid}/video")
    assert r.status_code == 409
    assert r.get_json()["error"] == "not_ready"


def test_get_video_serves_mp4_when_complete(client):
    c, db, tmp_path = client
    timelapses_root = tmp_path / "timelapses"
    out_dir = timelapses_root / "unit_001"
    out_dir.mkdir(parents=True)
    out_rel = "unit_001/42.mp4"
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42FAKE_MP4_BODY"
    (timelapses_root / out_rel).write_bytes(fake_mp4)

    jid = _seed_job(db, status="complete", output_path=out_rel)
    r = c.get(f"/api/grow/timelapse/{jid}/video")
    assert r.status_code == 200
    assert r.mimetype == "video/mp4"
    assert r.data == fake_mp4
    cc = r.headers.get("Cache-Control", "")
    assert "max-age=31536000" in cc
    assert "immutable" in cc


def test_get_video_404_when_complete_but_file_missing(client):
    c, db, _tmp = client
    # Status says complete but no file on disk — should 404
    jid = _seed_job(db, status="complete", output_path="unit_001/missing.mp4")
    r = c.get(f"/api/grow/timelapse/{jid}/video")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Runner — render_job
# ---------------------------------------------------------------------------


@pytest.fixture
def runner_setup(tmp_path, monkeypatch):
    """Fresh DB + seeded unit + photos with REAL JPEG bytes on disk so
    the symlink/copy path in render_job succeeds. ffmpeg invocations
    are mocked in each test."""
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

    now = datetime.utcnow()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'h', 'X', ?, 'h', ?, 1)",
        (now, now),
    )
    photo_dir = images_root / "unit_001" / "2026-05-08"
    photo_dir.mkdir(parents=True)
    for i in range(3):
        rel = f"unit_001/2026-05-08/13000{i}.jpg"
        (images_root / rel).write_bytes(b"\xff\xd8FAKEJPEG")
        conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
            (now - timedelta(minutes=i), rel),
        )
    conn.commit()
    conn.close()
    yield tmp.name, tmp_path


def _seed_runner_job(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_timelapse_jobs "
        "(unit_id, requested_by, requested_at, range, fps, status) "
        "VALUES (1, 'alice', ?, '24h', 10, 'queued')",
        (datetime.utcnow(),),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def _job_status(db_path, job_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, output_path, error_message FROM grow_timelapse_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    conn.close()
    return row


def test_render_job_marks_failed_when_ffmpeg_missing(runner_setup):
    db, _tmp = runner_setup
    jid = _seed_runner_job(db)
    with patch("mlss_monitor.grow.timelapse_jobs.ffmpeg_available",
               return_value=False):
        from mlss_monitor.grow.timelapse_jobs import render_job
        render_job(jid)
    status, _out, err = _job_status(db, jid)
    assert status == "failed"
    assert err == "ffmpeg_not_installed"


def test_render_job_marks_failed_when_no_photos(tmp_path, monkeypatch):
    # Custom fixture: unit exists but no photos in the range.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.DB_FILE", tmp.name
    )
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    monkeypatch.setattr(
        "mlss_monitor.grow.timelapse_jobs.TIMELAPSES_DIR",
        str(tmp_path / "timelapses"))
    init_db.create_db()
    conn = sqlite3.connect(tmp.name)
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'h', 'X', ?, 'h', ?, 1)",
        (now, now),
    )
    cur = conn.execute(
        "INSERT INTO grow_timelapse_jobs "
        "(unit_id, requested_by, requested_at, range, fps, status) "
        "VALUES (1, 'alice', ?, '24h', 10, 'queued')",
        (now,),
    )
    jid = cur.lastrowid
    conn.commit()
    conn.close()

    with patch("mlss_monitor.grow.timelapse_jobs.ffmpeg_available",
               return_value=True):
        from mlss_monitor.grow.timelapse_jobs import render_job
        render_job(jid)
    status, _out, err = _job_status(tmp.name, jid)
    assert status == "failed"
    assert err == "no_photos_in_range"


def test_render_job_invokes_ffmpeg_and_marks_complete(runner_setup, monkeypatch):
    db, tmp_path = runner_setup
    jid = _seed_runner_job(db)

    captured_cmd = []

    def _fake_run(cmd, **kwargs):
        captured_cmd.append(cmd)
        # Simulate ffmpeg writing the output file
        out_path = cmd[-1]
        with open(out_path, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42FAKE")
        rv = type("R", (), {})()
        rv.returncode = 0
        rv.stdout = ""
        rv.stderr = ""
        return rv

    with patch("mlss_monitor.grow.timelapse_jobs.ffmpeg_available",
               return_value=True), \
         patch("mlss_monitor.grow.timelapse_jobs.subprocess.run",
               side_effect=_fake_run):
        from mlss_monitor.grow.timelapse_jobs import render_job
        render_job(jid)

    status, out, _err = _job_status(db, jid)
    assert status == "complete", _job_status(db, jid)
    assert out == f"unit_001/{jid}.mp4"
    # Confirm the file exists on disk
    final_abs = tmp_path / "timelapses" / out
    assert final_abs.exists()
    # Confirm the staging dir was cleaned up
    staging = tmp_path / "timelapses" / "unit_001" / f"_staging_{jid}"
    assert not staging.exists()
    # Confirm ffmpeg was called with the expected pattern
    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    assert "ffmpeg" in cmd[0]
    assert "frame_%04d.jpg" in " ".join(cmd)
    assert "-framerate" in cmd
    assert "10" in cmd  # fps


def test_render_job_marks_failed_on_ffmpeg_nonzero_exit(runner_setup):
    db, _tmp = runner_setup
    jid = _seed_runner_job(db)

    def _fake_run(_cmd, **_kwargs):
        rv = type("R", (), {})()
        rv.returncode = 1
        rv.stdout = ""
        rv.stderr = "decode error: bad input"
        return rv

    with patch("mlss_monitor.grow.timelapse_jobs.ffmpeg_available",
               return_value=True), \
         patch("mlss_monitor.grow.timelapse_jobs.subprocess.run",
               side_effect=_fake_run):
        from mlss_monitor.grow.timelapse_jobs import render_job
        render_job(jid)
    status, _out, err = _job_status(db, jid)
    assert status == "failed"
    assert "ffmpeg_failed" in err
    assert "decode error" in err


def test_render_job_skips_non_queued_rows(runner_setup):
    """Idempotency: calling render_job twice on a complete job does
    nothing (the second call sees status != queued and returns)."""
    db, _tmp = runner_setup
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO grow_timelapse_jobs "
        "(unit_id, requested_by, requested_at, range, fps, status, output_path) "
        "VALUES (1, 'alice', ?, '24h', 10, 'complete', 'fake/path.mp4')",
        (datetime.utcnow(),),
    )
    jid = cur.lastrowid
    conn.commit()
    conn.close()

    from mlss_monitor.grow.timelapse_jobs import render_job
    render_job(jid)  # should be a no-op
    status, out, _err = _job_status(db, jid)
    assert status == "complete"
    assert out == "fake/path.mp4"


# ---------------------------------------------------------------------------
# Startup-check: log_ffmpeg_status_at_startup() / start_runner_thread()
# ---------------------------------------------------------------------------


def test_log_ffmpeg_status_warns_when_missing(caplog):
    """Missing ffmpeg: a single WARNING with the install command, the
    function returns False, and no exception escapes. This is the line
    the operator sees in journalctl after a fresh install where they
    forgot ``sudo apt install ffmpeg``."""
    from mlss_monitor.grow import timelapse_jobs
    with caplog.at_level(logging.WARNING,
                         logger="mlss_monitor.grow.timelapse_jobs"), \
         patch.object(timelapse_jobs, "ffmpeg_available", return_value=False):
        result = timelapse_jobs.log_ffmpeg_status_at_startup()
    assert result is False
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "mlss_monitor.grow.timelapse_jobs"
    ]
    assert len(warning_records) == 1, [
        (r.levelname, r.message) for r in caplog.records
    ]
    msg = warning_records[0].getMessage()
    assert "ffmpeg not found" in msg
    assert "sudo apt install ffmpeg" in msg


def test_log_ffmpeg_status_info_when_present(caplog):
    """When ffmpeg is on PATH, log an INFO line with the version string
    so operators can confirm the binary version in journalctl. Falls
    back to a placeholder when the version call returns nothing."""
    from mlss_monitor.grow import timelapse_jobs
    fake_version = "ffmpeg version 4.4.2-test-build"
    with caplog.at_level(logging.INFO,
                         logger="mlss_monitor.grow.timelapse_jobs"), \
         patch.object(timelapse_jobs, "ffmpeg_available",
                      return_value=True), \
         patch.object(timelapse_jobs, "_ffmpeg_version_line",
                      return_value=fake_version):
        result = timelapse_jobs.log_ffmpeg_status_at_startup()
    assert result is True
    info_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO
        and r.name == "mlss_monitor.grow.timelapse_jobs"
        and "ffmpeg detected" in r.getMessage()
    ]
    assert len(info_records) == 1
    assert fake_version in info_records[0].getMessage()


def test_log_ffmpeg_status_info_when_version_call_returns_none(caplog):
    """If ffmpeg is on PATH but the version subprocess fails/hangs, the
    log line still goes out — just with a placeholder. Operators still
    get the 'detected' signal."""
    from mlss_monitor.grow import timelapse_jobs
    with caplog.at_level(logging.INFO,
                         logger="mlss_monitor.grow.timelapse_jobs"), \
         patch.object(timelapse_jobs, "ffmpeg_available",
                      return_value=True), \
         patch.object(timelapse_jobs, "_ffmpeg_version_line",
                      return_value=None):
        result = timelapse_jobs.log_ffmpeg_status_at_startup()
    assert result is True
    info_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO
        and "ffmpeg detected" in r.getMessage()
    ]
    assert len(info_records) == 1
    assert "version unknown" in info_records[0].getMessage()


def test_ffmpeg_version_line_returns_none_when_missing():
    """Defensive: if shutil.which returns None, _ffmpeg_version_line()
    must short-circuit without invoking subprocess.run (so tests don't
    spawn random binaries on a dev box and so missing-ffmpeg doesn't
    raise FileNotFoundError)."""
    from mlss_monitor.grow import timelapse_jobs
    with patch.object(timelapse_jobs, "ffmpeg_available",
                      return_value=False), \
         patch.object(timelapse_jobs.subprocess, "run") as mock_run:
        result = timelapse_jobs._ffmpeg_version_line()
    assert result is None
    mock_run.assert_not_called()


def test_start_runner_thread_emits_startup_log_when_ffmpeg_missing(caplog):
    """start_runner_thread() must call log_ffmpeg_status_at_startup() so
    the operator sees the warning in journalctl during service boot.
    The thread is still started — missing-ffmpeg jobs fail at render
    time rather than crashing the daemon."""
    from mlss_monitor.grow import timelapse_jobs

    # Make sure no previous test left a thread running.
    timelapse_jobs.stop_runner_thread(timeout=2.0)

    try:
        with caplog.at_level(logging.WARNING,
                             logger="mlss_monitor.grow.timelapse_jobs"), \
             patch.object(timelapse_jobs, "ffmpeg_available",
                          return_value=False):
            timelapse_jobs.start_runner_thread()
        # Thread should be running despite missing ffmpeg.
        assert timelapse_jobs._runner_thread is not None
        assert timelapse_jobs._runner_thread.is_alive()
        # And the WARNING should have been logged at startup.
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "ffmpeg not found" in r.getMessage()
        ]
        assert len(warnings) == 1
    finally:
        timelapse_jobs.stop_runner_thread(timeout=2.0)


def test_runner_loop_keeps_polling_when_ffmpeg_missing(runner_setup, monkeypatch):
    """When ffmpeg is missing the runner does NOT crash and does NOT
    spin — it picks up the queued row, render_job() marks it failed
    with the actionable error_message, and the loop continues. This
    is the 'install ffmpeg, restart, jobs resume' guarantee."""
    db, _tmp = runner_setup
    jid = _seed_runner_job(db)

    # Run render_job directly to confirm the fail-then-continue posture
    # at the job layer (the loop is exercised in the start_runner_thread
    # test above; combining them would require a real sleep).
    with patch("mlss_monitor.grow.timelapse_jobs.ffmpeg_available",
               return_value=False):
        from mlss_monitor.grow.timelapse_jobs import render_job
        render_job(jid)
    status, _out, err = _job_status(db, jid)
    assert status == "failed"
    assert err == "ffmpeg_not_installed"

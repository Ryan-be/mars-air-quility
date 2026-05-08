"""Phase 4 #8 — time-lapse video render queue + runner.

The operator submits a render request via POST /api/grow/units/<id>/timelapse;
the row lands in ``grow_timelapse_jobs`` with ``status='queued'``. A
single in-process daemon thread polls the table every 30 seconds, picks
the oldest queued row, builds the ffmpeg command from the unit's
grow_photos, writes the MP4 under ``data/timelapses/<unit>/<job_id>.mp4``,
and updates the row to ``complete`` (or ``failed`` with an error_message).

In-process is intentional for v1 — Celery / RQ would be over-engineering
for the actual workload (one MLSS server, one operator, render bursts).
The runner survives gunicorn's preload+post_fork dance the same way the
existing background services do (see mlss_monitor/app.py).

ffmpeg gotcha: the photo file_paths in the DB use timestamped filenames
that are NOT a sequential ``%04d.jpg`` pattern. ffmpeg's pattern matcher
chokes on non-sequential names, so the runner symlinks the photos into a
temp dir as ``frame_0001.jpg`` etc. and runs ffmpeg against that.

If ffmpeg isn't installed the job fails fast with a clear error_message.
The route layer also detects ffmpeg-missing and returns 503 on the
create endpoint so the operator knows up-front (see api_grow_timelapse).
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from database.init_db import DB_FILE
from mlss_monitor.grow.api_helpers import RANGE_TO_HOURS
from mlss_monitor.grow.photo_storage import _resolve_images_dir

log = logging.getLogger(__name__)

# Default poll interval. The runner waits this long between checks for
# new queued rows. Tunable via env so a load test can crank it down.
_POLL_INTERVAL_S = float(os.environ.get("MLSS_TIMELAPSE_POLL_S", "30"))

# Where rendered MP4s live, relative to project root. Same posture as
# data/grow_images/ — overridable via env so an admin can point this at
# an external SSD with more headroom than the system disk.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TIMELAPSES_DIR = os.environ.get(
    "MLSS_GROW_TIMELAPSES_DIR", str(_PROJECT_ROOT / "data" / "timelapses")
)


def _resolve_timelapses_dir() -> str:
    return TIMELAPSES_DIR


def ffmpeg_available() -> bool:
    """True iff the ``ffmpeg`` binary is on PATH. Cached negatively only —
    if ffmpeg gets installed at runtime (apt install) the next call picks
    it up. Cheap enough not to need positive caching."""
    return shutil.which("ffmpeg") is not None


def _photos_in_range(conn, unit_id: int, range_str: str):
    """Fetch (file_path, taken_at) for a unit's photos within ``range_str``,
    sorted ASC. Returns [] if the range token is invalid (the route
    rejects that case before insert, but defend in depth)."""
    if range_str not in RANGE_TO_HOURS:
        return []
    hours = RANGE_TO_HOURS[range_str]
    if hours is None:
        rows = conn.execute(
            "SELECT file_path, taken_at FROM grow_photos "
            "WHERE unit_id=? ORDER BY taken_at ASC",
            (unit_id,),
        ).fetchall()
    else:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = conn.execute(
            "SELECT file_path, taken_at FROM grow_photos "
            "WHERE unit_id=? AND taken_at >= ? ORDER BY taken_at ASC",
            (unit_id, cutoff),
        ).fetchall()
    return rows


def _link_or_copy(src: str, dst: str) -> None:
    """Symlink src → dst if possible, else copy. Symlinks are vastly
    faster (no I/O) but Windows + Linux without CAP_SYMLINK falls back
    to copy. The ffmpeg pattern matcher doesn't care which one we use."""
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        # Permission or OS-not-supported: copy. This adds I/O cost but
        # is correct on every platform.
        shutil.copyfile(src, dst)


def render_job(job_id: int) -> None:
    """Render one job synchronously. Called by the daemon thread but also
    callable directly from tests (avoids spinning up the runner thread).

    Reads the row, sets status=running, builds the staging dir, invokes
    ffmpeg, writes the output to data/timelapses/<unit>/<job_id>.mp4,
    flips status to complete (or failed with an error_message). All DB
    state mutations are committed individually so a crash mid-render
    leaves the row in a recoverable state.
    """
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM grow_timelapse_jobs WHERE id=?", (job_id,),
        ).fetchone()
        if row is None:
            log.warning("render_job: no row id=%s", job_id)
            return
        if row["status"] != "queued":
            log.info(
                "render_job: skipping job %s with status %s (not queued)",
                job_id, row["status"],
            )
            return

        # Mark running so a sibling worker (future scale-up) doesn't
        # double-claim this row.
        conn.execute(
            "UPDATE grow_timelapse_jobs SET status='running', started_at=? "
            "WHERE id=? AND status='queued'",
            (datetime.utcnow(), job_id),
        )
        conn.commit()
        # Re-read to pick up the latest values
        row = conn.execute(
            "SELECT * FROM grow_timelapse_jobs WHERE id=?", (job_id,),
        ).fetchone()

        unit_id = row["unit_id"]
        range_str = row["range"]
        fps = max(1, int(row["fps"] or 10))

        if not ffmpeg_available():
            _mark_failed(conn, job_id, "ffmpeg_not_installed")
            return

        photos = _photos_in_range(conn, unit_id, range_str)
        if not photos:
            _mark_failed(conn, job_id, "no_photos_in_range")
            return

        images_dir = _resolve_images_dir()
        timelapses_dir = _resolve_timelapses_dir()
        unit_out_dir = Path(timelapses_dir) / f"unit_{unit_id:03d}"
        unit_out_dir.mkdir(parents=True, exist_ok=True)
        out_rel = f"unit_{unit_id:03d}/{job_id}.mp4"
        out_abs = unit_out_dir / f"{job_id}.mp4"

        # Stage symlinks so ffmpeg's %04d pattern works.
        staging = unit_out_dir / f"_staging_{job_id}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            for i, p in enumerate(photos, start=1):
                src = os.path.join(images_dir, p["file_path"])
                if not os.path.exists(src):
                    # Tolerate gaps — skip and continue. The lapse will
                    # have a small jump but a missing source is rare.
                    continue
                dst = staging / f"frame_{i:04d}.jpg"
                _link_or_copy(src, str(dst))

            cmd = [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", str(staging / "frame_%04d.jpg"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                # Even-resolution scale for libx264; auto-pads odd dims.
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                str(out_abs),
            ]
            log.info("render_job %s: invoking ffmpeg for %d frames -> %s",
                     job_id, len(photos), out_abs)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                timeout=60 * 30,  # 30 minute hard ceiling
            )
            if proc.returncode != 0:
                # Trim stderr to a manageable size for the DB column —
                # ffmpeg can spew thousands of lines on a malformed input.
                err = (proc.stderr or proc.stdout or "")[-2000:]
                _mark_failed(conn, job_id, f"ffmpeg_failed: {err}")
                return
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        _mark_complete(conn, job_id, out_rel)
    except Exception as exc:  # pylint: disable=broad-except
        log.exception("render_job %s: crashed", job_id)
        try:
            _mark_failed(conn, job_id, f"runner_crashed: {exc}")
        except Exception:  # pylint: disable=broad-except
            pass
    finally:
        conn.close()


def _mark_failed(conn, job_id: int, message: str) -> None:
    conn.execute(
        "UPDATE grow_timelapse_jobs "
        "SET status='failed', error_message=?, completed_at=? "
        "WHERE id=?",
        (message, datetime.utcnow(), job_id),
    )
    conn.commit()


def _mark_complete(conn, job_id: int, output_path: str) -> None:
    conn.execute(
        "UPDATE grow_timelapse_jobs "
        "SET status='complete', output_path=?, completed_at=? "
        "WHERE id=?",
        (output_path, datetime.utcnow(), job_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Background daemon thread
# ---------------------------------------------------------------------------


_runner_thread = None
_runner_stop = threading.Event()


def _runner_loop():
    """Daemon-thread loop: pick the oldest queued job, render it,
    repeat. Sleeps ``_POLL_INTERVAL_S`` between empty polls to avoid
    a hot loop on an idle queue."""
    log.info("timelapse_jobs runner started (poll=%ss)", _POLL_INTERVAL_S)
    while not _runner_stop.is_set():
        try:
            conn = sqlite3.connect(DB_FILE, timeout=10)
            try:
                row = conn.execute(
                    "SELECT id FROM grow_timelapse_jobs "
                    "WHERE status='queued' ORDER BY requested_at ASC LIMIT 1",
                ).fetchone()
            finally:
                conn.close()
        except Exception:  # pylint: disable=broad-except
            log.exception("timelapse runner: DB poll failed")
            row = None

        if row is None:
            # Empty queue — long sleep before checking again. The Event
            # wait makes shutdown responsive (set the event from
            # start_runner_thread on app teardown to exit promptly).
            _runner_stop.wait(timeout=_POLL_INTERVAL_S)
            continue

        job_id = row[0]
        try:
            render_job(job_id)
        except Exception:  # pylint: disable=broad-except
            log.exception("timelapse runner: job %s render crashed", job_id)
        # Brief pause between jobs so a bursty queue doesn't starve
        # other Flask handlers of CPU. Real workloads max out at
        # 1 render/min so this isn't a contention bottleneck.
        _runner_stop.wait(timeout=1)
    log.info("timelapse_jobs runner stopped")


def start_runner_thread() -> None:
    """Start the daemon-thread runner if not already running. Idempotent
    — repeated calls are no-ops. Call from gunicorn's post_fork hook so
    the runner lives inside the worker process (matches the existing
    safety-loop / inference-engine pattern in app.py)."""
    global _runner_thread  # pylint: disable=global-statement
    if _runner_thread is not None and _runner_thread.is_alive():
        return
    _runner_stop.clear()
    _runner_thread = threading.Thread(
        target=_runner_loop, name="timelapse-runner", daemon=True,
    )
    _runner_thread.start()


def stop_runner_thread(timeout: float = 5.0) -> None:
    """Signal the daemon thread to exit and wait. Useful in tests; not
    called in production (daemon threads die with the process)."""
    global _runner_thread  # pylint: disable=global-statement
    _runner_stop.set()
    if _runner_thread is not None:
        _runner_thread.join(timeout=timeout)
    _runner_thread = None

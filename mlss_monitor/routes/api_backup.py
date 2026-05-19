"""Admin API endpoints for the backup subsystem.

Five operator-facing endpoints under ``/api/admin/backup/``:

  GET    /config        — masked config (password_set bool, no cleartext)
  PUT    /config        — save + reconcile worker state + hot-reload event
  GET    /status        — pipeline status + thread liveness + last snapshot
  POST   /test          — exercise connection with current credentials
  POST   /init          — apply server schema (db, stub) / create buckets
  POST   /maintenance   — confirm-gated actions (clear_outbox / pause /
                          resume / force_rebootstrap)

The PUT /config endpoint implements the user constraint that "the
worker should only run if backups are enabled":

  - enabled False → True : instantiate (if needed) + start the worker
  - enabled True  → False: stop the worker thread + discard
  - still-enabled change : publish backup_config_changed for hot-reload

All routes require admin role. Anonymous / viewer / controller sessions
receive 401 / 403 / 403 via ``rbac.require_role("admin")``.

Plan ref: docs/superpowers/plans/2026-05-18-mlss-backup.md (Phase 6 Tasks 19+20)
Spec:     docs/superpowers/specs/2026-05-18-mlss-backup-design.md
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from flask import Blueprint, jsonify, request, session

from database.init_db import DB_FILE
from mlss_monitor import state
from mlss_monitor.backup import config, server_schema
from mlss_monitor.backup.bootstrap import BootstrapScanner
from mlss_monitor.backup.postgres_client import PostgresClient
from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES
from mlss_monitor.backup.s3_client import S3Client
from mlss_monitor.backup.worker import BackupWorker
from mlss_monitor.rbac import require_role

log = logging.getLogger(__name__)

api_backup_bp = Blueprint("api_backup", __name__)


# Bucket suffixes the files pipeline ships to. Mirrors the prefixes
# handled in ``mlss_monitor.backup._drain._bucket_suffix_for_key``:
# photos (camera JPEGs), anomaly (per-channel detector pickles),
# multivar-anomaly (multivariate detector pickles), attribution
# (classifier pickles).
_BUCKET_SUFFIXES = ("photos", "anomaly", "multivar-anomaly", "attribution")


# ─────────────────────────────────────────────────────────────────────
# Model-artefact discovery — single source of truth
# ─────────────────────────────────────────────────────────────────────
#
# The on-disk locations of ML model pickles are defined in two places:
# ``mlss_monitor.app`` (where DetectionEngine is instantiated) and
# ``mlss_monitor.attribution.engine`` (the AttributionEngine's pkl
# path). Both compute their paths from ``Path(__file__).resolve()`` so
# they're stable regardless of the gunicorn cwd. We mirror the same
# computation here so the bootstrap walks exactly the trees the live
# writers populate.
#
# CRITICAL: if a future change moves either model dir, update BOTH the
# writer side AND this constant — they MUST stay in lockstep or the
# bootstrap will walk an empty tree and miss pre-existing artefacts.

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Directories holding model pickles. Each entry is (kind, path):
#   ('photo', data/grow_images)         — JPEGs from handle_photo_frame
#   ('model', data/anomaly_models)      — AnomalyDetector + MultivarAnomalyDetector
#                                          share this directory per
#                                          DetectionEngine's constructor.
#   ('model', data/)                    — AttributionEngine writes a single
#                                          classifier.pkl at data/classifier.pkl;
#                                          scanned by walking the data root
#                                          would also pick up unrelated files,
#                                          so we keep this list minimal and
#                                          rely on the live writer to enqueue
#                                          classifier.pkl going forward.
_MODEL_DIRS_FOR_BOOTSTRAP: list[tuple[str, Path]] = [
    ("photo", _PROJECT_ROOT / "data" / "grow_images"),
    ("model", _PROJECT_ROOT / "data" / "anomaly_models"),
]


def _default_file_roots() -> list[tuple[str, Path]]:
    """Filesystem roots a bootstrap (auto-run on first enable OR
    force_rebootstrap maintenance action) should walk.

    Each entry is (``kind``, ``root``) where ``kind`` is the
    ``outbox_blobs.kind`` discriminator. Paths are computed from
    ``_PROJECT_ROOT`` so they're stable regardless of the gunicorn
    process cwd — they must match the trees the live writers
    populate (``photo_storage.handle_photo_frame``,
    ``AnomalyDetector._save_models``, etc.).

    The ``classifier.pkl`` that ``AttributionEngine`` writes lives
    at ``data/classifier.pkl`` (a single file, not a directory).
    The live writer enqueues it via ``outbox.enqueue_blob`` the
    next time training runs; we don't include the ``data/`` root
    here because rglob would scan unrelated files. A first-time
    operator who never re-trains attribution before enabling
    backups gets the model on the next training cycle — acceptable
    because attribution models are retrained whenever a tag is
    added or removed.
    """
    return list(_MODEL_DIRS_FOR_BOOTSTRAP)


# ─────────────────────────────────────────────────────────────────────
# Bootstrap kickoff — shared by auto-run + force_rebootstrap
# ─────────────────────────────────────────────────────────────────────


def _kick_off_bootstrap(*, force_reset: bool) -> None:
    """Spawn the bootstrap scan in a daemon thread.

    Single source of truth for the two callers that need to launch a
    bootstrap:

      - PUT /api/admin/backup/config calls this with
        ``force_reset=False`` after every config save. The function
        gates on ``bootstrap_progress`` being empty so it auto-runs
        exactly once — on the first successful enable — and is a
        no-op on subsequent saves.

      - POST /api/admin/backup/maintenance ``force_rebootstrap`` calls
        this with ``force_reset=True``. ``bootstrap_progress`` is
        wiped first so the scan re-enqueues every row + file from
        zero.

    The scan runs in a daemon thread so the HTTP request returns
    promptly — a full re-bootstrap on a Pi with months of history can
    take many minutes.
    """
    scanner = BootstrapScanner(db_file=DB_FILE)

    if force_reset:
        scanner.reset("db")
        scanner.reset("files")
    else:
        # Auto-run case: skip if bootstrap_progress already has any rows.
        # A partial in-progress bootstrap counts as "already started" —
        # we don't want PUT /config racing a still-running scan.
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM bootstrap_progress"
            ).fetchone()[0]
        if count > 0:
            log.info(
                "Bootstrap auto-run skipped — bootstrap_progress has %d row(s)",
                count,
            )
            return

    def _run():
        try:
            scanner.start_db_bootstrap()
            scanner.start_files_bootstrap(_default_file_roots())
        except Exception as exc:  # pylint: disable=broad-except
            log.warning("Bootstrap thread failed: %s", exc)

    threading.Thread(
        target=_run,
        daemon=True,
        name="backup-bootstrap-oneshot",
    ).start()


# ─────────────────────────────────────────────────────────────────────
# GET /config
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/config", methods=["GET"])
@require_role("admin")
def get_backup_config():
    """Return the masked backup config — ``password_set``/``secret_key_set``
    booleans rather than cleartext credentials."""
    return jsonify(config.load())


# ─────────────────────────────────────────────────────────────────────
# PUT /config
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/config", methods=["PUT"])
@require_role("admin")
def put_backup_config():
    """Save partial config, reconcile worker threads, fire hot-reload.

    Returns the post-save masked config so the UI sees the canonical
    state without a second GET.
    """
    body = request.get_json(silent=True) or {}
    old_cfg = config.load()
    try:
        config.save(body)
    except ValueError as exc:
        # ``source_pi_id`` validation rejection — surface as 400 so the
        # UI sees the failure rather than a Flask 500 HTML page.
        return jsonify({"ok": False, "error": str(exc)}), 400
    new_cfg = config.load()

    _reconcile_workers(old_cfg, new_cfg)

    # Auto-run bootstrap iff this is the first time backups have been
    # enabled (i.e. bootstrap_progress is empty). Idempotent: subsequent
    # config saves see populated bootstrap_progress and no-op. Spawned
    # in a daemon thread so this PUT returns promptly even on a Pi
    # with months of history to scan.
    _kick_off_bootstrap(force_reset=False)

    # Publish AFTER reconcile so still-running workers see the new
    # config when they reload. Workers that were just stopped never
    # observe this event (their listener thread has exited); workers
    # that were just started observe their own .start() pre-subscribe
    # and pick up the event on the first listener-loop iteration.
    if state.event_bus is not None:
        state.event_bus.publish("backup_config_changed", {})

    return jsonify(new_cfg)


def _reconcile_workers(old_cfg: dict, new_cfg: dict) -> None:
    """Apply enabled-flag transitions by starting / stopping workers.

    Per the design constraint, a BackupWorker thread only exists when
    its pipeline is enabled. The four transitions:

      off → off : no-op
      off → on  : instantiate (if needed) + start
      on  → off : stop + discard (so a re-enable creates a fresh worker)
      on  → on  : no-op — the ``backup_config_changed`` event published
                  by the caller drives hot-reload inside the worker

    Each pipeline ("db" / "files") is reconciled independently. The
    worker handle is stored at ``state.backup_{pipeline}_worker``;
    absent attribute is treated as ``None`` so the first PUT on a fresh
    app boot (where Phase 8 hasn't created the handles yet) still works.
    """
    for pipeline in ("db", "files"):
        was_on = old_cfg.get("enabled", False) and old_cfg.get(pipeline, {}).get("enabled", False)
        is_on = new_cfg.get("enabled", False) and new_cfg.get(pipeline, {}).get("enabled", False)
        attr_name = f"backup_{pipeline}_worker"
        worker = getattr(state, attr_name, None)

        if was_on and not is_on:
            # Stop and discard so a future re-enable builds a fresh
            # worker (config snapshots are captured at construction
            # time inside the listener subscription setup).
            if worker is not None:
                try:
                    worker._on_disabled()
                    worker.stop()
                except Exception as exc:  # pylint: disable=broad-except
                    log.warning(
                        "backup: error stopping %s worker: %s",
                        pipeline, exc,
                    )
                setattr(state, attr_name, None)

        elif not was_on and is_on:
            # Create the worker if Phase 8 hasn't already (e.g. when
            # PUT /config runs before app.py wires the handles).
            if worker is None:
                worker = BackupWorker(
                    pipeline=pipeline,
                    event_bus=state.event_bus,
                )
                setattr(state, attr_name, worker)
            worker._on_enabled()
            worker.start()


# ─────────────────────────────────────────────────────────────────────
# GET /status
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/status", methods=["GET"])
@require_role("admin")
def get_backup_status():
    """Return current per-pipeline state plus the most recent
    ``backup_status_changed`` snapshot from the event-bus history.

    The snapshot is whatever the worker last published (see
    ``BackupWorker._publish_status``). When no event has been
    published yet (e.g. workers disabled or freshly started), the
    snapshot key is ``None`` and the UI shows a "waiting for first
    drain" placeholder.
    """
    cfg = config.load()
    result: dict = {
        "enabled": cfg["enabled"],
        "paused": cfg["paused"],
        "pipelines": {},
    }
    for pipeline in ("db", "files"):
        worker = getattr(state, f"backup_{pipeline}_worker", None)
        thread = getattr(worker, "_thread", None) if worker is not None else None
        result["pipelines"][pipeline] = {
            "enabled": cfg[pipeline]["enabled"],
            "thread_alive": bool(thread is not None and thread.is_alive()),
            "snapshot": _latest_status_snapshot(pipeline),
        }
    return jsonify(result)


def _latest_status_snapshot(pipeline: str) -> dict | None:
    """Pull the most recent ``backup_status_changed`` event whose
    ``data.pipeline`` matches ``pipeline``. Returns ``None`` if no such
    event has been published yet, or if the event_bus isn't wired."""
    if state.event_bus is None:
        return None
    history = state.event_bus.get_history(event_type="backup_status_changed")
    # Iterate newest-first — bus stores oldest-first in the deque.
    for msg in reversed(history):
        if msg["data"].get("pipeline") == pipeline:
            return msg["data"]
    return None


# ─────────────────────────────────────────────────────────────────────
# POST /test
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/test", methods=["POST"])
@require_role("admin")
def test_backup_connection():
    """Try to connect with the currently-stored credentials. Used by
    the admin UI's 'Test connection' button before flipping enabled.

    Always returns JSON — the underlying client wraps exceptions in
    a ``{"ok": False, "error": ...}`` shape so a failing connection
    doesn't 500 the UI."""
    pipeline = request.args.get("pipeline")
    if pipeline not in ("db", "files"):
        return jsonify({"error": "pipeline must be 'db' or 'files'"}), 400

    cfg = config.load()
    try:
        if pipeline == "db":
            client = PostgresClient(
                host=cfg["db"]["host"],
                port=cfg["db"]["port"],
                database=cfg["db"]["database"],
                user=cfg["db"]["user"],
                password=config.get_secret("db", "password") or "",
                source_pi_id=cfg["source_pi_id"],
                timeout=cfg["advanced"]["connection_timeout_s"],
            )
        else:
            client = S3Client(
                endpoint=cfg["files"]["endpoint"],
                region=cfg["files"]["region"],
                access_key=cfg["files"]["access_key_id"],
                secret_key=config.get_secret("files", "secret_key") or "",
                bucket_prefix=cfg["files"]["bucket_prefix"],
                timeout=cfg["advanced"]["connection_timeout_s"],
            )
        return jsonify(client.test_connection())
    except ValueError as exc:
        # PostgresClient raises ValueError when source_pi_id is empty;
        # surface that as a 400 rather than a 500.
        return jsonify({"ok": False, "error": str(exc)}), 400


# ─────────────────────────────────────────────────────────────────────
# POST /init
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/init", methods=["POST"])
@require_role("admin")
def init_backup_pipeline():
    """One-time server-side setup.

    Files: iterate the four known bucket suffixes + create each
    (idempotent — S3Client.make_bucket swallows BucketAlreadyOwnedByYou).

    DB: derive the server-side DDL from the live SQLite schema via
    server_schema.generate_ddl + apply it through PostgresClient.run_ddl.
    Every CREATE in the DDL is IF NOT EXISTS so re-running init on a
    populated server is a no-op.
    """
    pipeline = request.args.get("pipeline")
    if pipeline not in ("db", "files"):
        return jsonify({"error": "pipeline must be 'db' or 'files'"}), 400

    if pipeline == "db":
        cfg = config.load()
        try:
            client = PostgresClient(
                host=cfg["db"]["host"],
                port=cfg["db"]["port"],
                database=cfg["db"]["database"],
                user=cfg["db"]["user"],
                password=config.get_secret("db", "password") or "",
                source_pi_id=cfg["source_pi_id"],
                timeout=cfg["advanced"]["connection_timeout_s"],
            )
            ddl = server_schema.generate_ddl(DB_FILE)
            client.run_ddl(ddl)
            return jsonify({
                "ok": True,
                "tables_created": list(REPLICATED_TABLES.keys()),
            })
        except ValueError as exc:
            # Missing local SQLite table (live schema not init'd) or
            # PostgresClient empty source_pi_id sentinel — both are
            # 400-class misconfigurations rather than 500 server errors.
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # pylint: disable=broad-except
            return jsonify({"ok": False, "error": str(exc)}), 500

    cfg = config.load()
    try:
        client = S3Client(
            endpoint=cfg["files"]["endpoint"],
            region=cfg["files"]["region"],
            access_key=cfg["files"]["access_key_id"],
            secret_key=config.get_secret("files", "secret_key") or "",
            bucket_prefix=cfg["files"]["bucket_prefix"],
            timeout=cfg["advanced"]["connection_timeout_s"],
        )
        for suffix in _BUCKET_SUFFIXES:
            client.make_bucket(suffix)
        return jsonify({
            "ok": True,
            "buckets_created": [
                f"{cfg['files']['bucket_prefix']}{s}" for s in _BUCKET_SUFFIXES
            ],
        })
    except Exception as exc:  # pylint: disable=broad-except
        return jsonify({"ok": False, "error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────
# POST /maintenance
# ─────────────────────────────────────────────────────────────────────


@api_backup_bp.route("/api/admin/backup/maintenance", methods=["POST"])
@require_role("admin")
def backup_maintenance():
    """Confirm-gated admin actions.

    Body shape: ``{action: str, confirm: bool, ...}``.

    Without ``confirm: true`` the request 400s — every action is
    destructive (clears the outbox, halts shipping, or restarts a
    full re-scan) and the UI is expected to surface a confirmation
    dialog before submitting.

    Supported actions:
      - ``clear_outbox``      — wipe outbox_changes + outbox_blobs +
                                outbox_delete_scope. Audit-logged at
                                WARNING level with the admin's user.
      - ``pause``             — set ``paused=True`` + notify workers
                                via ``_on_paused``.
      - ``resume``            — set ``paused=False`` + notify workers
                                via ``_on_resumed``.
      - ``force_rebootstrap`` — reset bootstrap_progress for both
                                pipelines + spawn a one-shot thread
                                that re-runs the full scan.
    """
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if not body.get("confirm", False):
        return jsonify({"error": "missing confirm flag"}), 400

    if action == "clear_outbox":
        log.warning(
            "admin %s cleared backup outbox",
            session.get("user") or "<unknown>",
        )
        with closing(sqlite3.connect(DB_FILE, timeout=10)) as conn:
            with conn:
                conn.execute("DELETE FROM outbox_changes")
                conn.execute("DELETE FROM outbox_blobs")
                conn.execute("DELETE FROM outbox_delete_scope")
        return jsonify({"ok": True, "action": "outbox cleared"})

    if action == "pause":
        config.save({"paused": True})
        for pipeline in ("db", "files"):
            worker = getattr(state, f"backup_{pipeline}_worker", None)
            if worker is not None:
                worker._on_paused()
        if state.event_bus is not None:
            state.event_bus.publish("backup_config_changed", {})
        return jsonify({"ok": True, "action": "paused"})

    if action == "resume":
        config.save({"paused": False})
        for pipeline in ("db", "files"):
            worker = getattr(state, f"backup_{pipeline}_worker", None)
            if worker is not None:
                worker._on_resumed()
        if state.event_bus is not None:
            state.event_bus.publish("backup_config_changed", {})
        return jsonify({"ok": True, "action": "resumed"})

    if action == "force_rebootstrap":
        # Shares the kickoff helper with PUT /config's auto-run path —
        # ``force_reset=True`` wipes bootstrap_progress first so the
        # scan re-enqueues every row + file from zero.
        _kick_off_bootstrap(force_reset=True)
        return jsonify({"ok": True, "action": "force_rebootstrap started"})

    return jsonify({"error": f"unknown action {action!r}"}), 400

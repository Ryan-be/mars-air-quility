"""BootstrapScanner — walks historical replicated-table rows + filesystem
trees and enqueues them so existing data lands on the backup server when
the operator first enables backups.

Covered:
  - start_db_bootstrap enqueues every row from every replicated table
  - resumable: cursor advances per batch, resume picks up from last_pk
  - skips tables already marked completed
  - composite-PK tables produce `f"{a}:{b}"` strings matching live writers
  - empty tables get a completed_at without errors
  - start_files_bootstrap walks rglob and enqueues every file
  - resumable: cursor lets resume skip already-processed files
  - target_key shape matches live photo_storage writers (relative-to-root,
    not relative-to-root.parent)
  - sha256 computed from on-disk bytes
  - reset(pipeline) clears all scopes for a pipeline
  - reset(pipeline, scope) clears one scope only
  - non-existent scope reset is a no-op (no error)

The ``db_path`` fixture is provided by ``tests/conftest.py``.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from mlss_monitor.backup import outbox
from mlss_monitor.backup.bootstrap import BootstrapScanner


# ── Helpers ──────────────────────────────────────────────────────────


def _outbox_rows(db_path: str) -> list[tuple[str, str]]:
    """Return (table_name, pk) for every entry in outbox_changes,
    in insertion order. Order matters because the resumable cursor
    advances row-by-row."""
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes ORDER BY id"
        ))


def _outbox_blobs(db_path: str) -> list[tuple[str, str, str, str]]:
    """Return (kind, source_path, target_key, sha256) for every entry."""
    with sqlite3.connect(db_path) as conn:
        return list(conn.execute(
            "SELECT kind, source_path, target_key, sha256 "
            "FROM outbox_blobs ORDER BY id"
        ))


def _bootstrap_progress(db_path: str, pipeline: str) -> list[dict]:
    """Snapshot bootstrap_progress for a pipeline."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT scope, last_pk, total_rows, started_at, completed_at "
            "FROM bootstrap_progress WHERE pipeline=? ORDER BY scope",
            (pipeline,),
        ).fetchall()
    return [dict(r) for r in rows]


def _seed_sensor_data(db_path: str, n: int) -> list[int]:
    """Insert N sensor_data rows directly (bypassing the outbox enqueue
    decorators) — bootstrap is meant to backfill rows that pre-existed
    BEFORE the outbox machinery was wired in, so seeding through the
    decorators would defeat the test."""
    now = datetime.utcnow().isoformat()
    ids = []
    with sqlite3.connect(db_path) as conn:
        for i in range(n):
            cur = conn.execute(
                "INSERT INTO sensor_data (timestamp, temperature, humidity, eco2, tvoc) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, 20.0 + i, 50.0, 400, 100),
            )
            ids.append(cur.lastrowid)
    return ids


def _seed_grow_unit(db_path: str, unit_id: int = 1) -> None:
    """Seed a grow_units row so foreign-key dependent tables have a parent."""
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
            "bearer_token_hash, phase_set_at) VALUES (?, ?, ?, ?, ?, ?)",
            (unit_id, f"hw-{unit_id}", f"Unit {unit_id}", now, "hash", now),
        )


def _seed_grow_unit_capabilities(db_path: str, unit_id: int,
                                 channels: list[str]) -> None:
    """Seed grow_unit_capabilities rows (composite PK unit_id+channel)."""
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        for ch in channels:
            conn.execute(
                "INSERT INTO grow_unit_capabilities "
                "(unit_id, channel, hardware, is_required, installed_at, health, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unit_id, ch, "gpio", 1, now, "untested", now),
            )


def _seed_incident_with_alerts(db_path: str, incident_id: str,
                               n_alerts: int) -> list[int]:
    """Seed an incident + composite-PK incident_alerts rows.

    Returns the list of real alert IDs (autoincremented inference PKs)
    so the assertion can construct the exact expected pk strings."""
    now = datetime.utcnow().isoformat()
    real_alert_ids: list[int] = []
    with sqlite3.connect(db_path) as conn:
        # incidents PK is TEXT id, started_at/ended_at not created_at.
        conn.execute(
            "INSERT INTO incidents (id, started_at, ended_at, "
            "max_severity, confidence, title) "
            "VALUES (?, ?, ?, 'warning', 0.5, 'test')",
            (incident_id, now, now),
        )
        for _ in range(n_alerts):
            cur = conn.execute(
                "INSERT INTO inferences "
                "(created_at, event_type, severity, title) "
                "VALUES (?, 'temp_high', 'warning', 't')",
                (now,),
            )
            real_alert_ids.append(cur.lastrowid)
        # incident_alerts has composite PK (incident_id, alert_id)
        for real_id in real_alert_ids:
            conn.execute(
                "INSERT INTO incident_alerts "
                "(incident_id, alert_id, is_primary) VALUES (?, ?, 1)",
                (incident_id, real_id),
            )
    return real_alert_ids


# ── DB bootstrap ─────────────────────────────────────────────────────


def test_start_db_bootstrap_empty_tables_complete_without_error(db_path):
    """A schema with no rows in a given replicated table still marks
    every table completed and never errors. Some tables get seed rows
    on create_db (grow_plant_profiles, grow_medium_defaults — shipped
    defaults), so we don't assert outbox == [] globally; we only
    require that empty tables produce zero outbox entries while the
    seeded ones produce one entry per seed row."""
    # Wipe the seed rows so all replicated tables ARE empty at bootstrap
    # time. This lets us assert outbox_changes == [] cleanly.
    from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES
    with sqlite3.connect(db_path) as conn:
        for table in REPLICATED_TABLES:
            conn.execute(f"DELETE FROM {table}")

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    assert _outbox_rows(db_path) == []

    progress = _bootstrap_progress(db_path, "db")
    # Every replicated table got a progress row, all completed.
    assert {p["scope"] for p in progress} == set(REPLICATED_TABLES)
    assert all(p["completed_at"] is not None for p in progress)
    assert all(p["total_rows"] == 0 for p in progress)


def test_start_db_bootstrap_enqueues_single_pk_rows(db_path):
    """The most common case: int-PK rows get enqueued one-by-one with
    `str(pk)` in the outbox."""
    ids = _seed_sensor_data(db_path, n=5)
    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    rows = _outbox_rows(db_path)
    sensor_rows = [(t, pk) for t, pk in rows if t == "sensor_data"]
    assert sorted(sensor_rows) == sorted([("sensor_data", str(i)) for i in ids])


def test_start_db_bootstrap_composite_pk_grow_unit_capabilities(db_path):
    """Composite-PK rows must produce `f"{unit_id}:{channel}"` strings —
    same format the live writer in grow/handlers.py uses, so the
    worker's _parse_pk can read them back."""
    _seed_grow_unit(db_path, unit_id=3)
    _seed_grow_unit_capabilities(db_path, unit_id=3,
                                 channels=["pump", "light", "fan"])

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    rows = _outbox_rows(db_path)
    caps = [pk for t, pk in rows if t == "grow_unit_capabilities"]
    assert sorted(caps) == sorted(["3:pump", "3:light", "3:fan"])


def test_start_db_bootstrap_composite_pk_incident_alerts(db_path):
    """incident_alerts: composite (str incident_id, int alert_id) where
    the incident_id itself contains colons (ISO 8601 timestamp).

    The pk strings produced by bootstrap MUST round-trip through
    ``worker._parse_pk`` so the timestamp's internal colons stay
    intact and the alert_id parses as an int."""
    from mlss_monitor.backup.worker import _parse_pk

    incident_id = "INC-2026-05-18T12:00:00"
    real_alert_ids = _seed_incident_with_alerts(db_path, incident_id, n_alerts=2)

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    rows = _outbox_rows(db_path)
    alerts = [pk for t, pk in rows if t == "incident_alerts"]
    expected = {f"{incident_id}:{aid}" for aid in real_alert_ids}
    assert set(alerts) == expected

    # Round-trip through the worker's parser — bootstrap's pk strings
    # must be readable by the same logic that ships them.
    for pk in alerts:
        parsed_incident, parsed_alert = _parse_pk(pk, [str, int])
        assert parsed_incident == incident_id
        assert parsed_alert in real_alert_ids


def test_start_db_bootstrap_skips_completed_tables(db_path):
    """A table already marked completed_at IS NOT NULL is skipped.
    Re-running bootstrap is idempotent — no double-enqueue."""
    _seed_sensor_data(db_path, n=3)

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()
    first_pass = _outbox_rows(db_path)

    # Seed more rows AFTER the first bootstrap completed — these
    # shouldn't be picked up because sensor_data is marked completed.
    _seed_sensor_data(db_path, n=2)

    scanner.start_db_bootstrap()
    second_pass = _outbox_rows(db_path)

    assert first_pass == second_pass, (
        "second run shouldn't enqueue the newly-seeded rows because "
        "sensor_data was already marked completed"
    )


def test_start_db_bootstrap_resumable_from_cursor(db_path):
    """Simulate a crash mid-scan: hand-roll a partially-completed
    progress row with last_pk pointing into the middle of the seeded
    rows. Re-running bootstrap picks up from the cursor, not from zero."""
    ids = _seed_sensor_data(db_path, n=10)

    # Manually plant a partial-progress row: bootstrap got through
    # ROWID 5 already (so rows 1-5 are "shipped"), then crashed.
    # On resume we expect rows 6-10 to be enqueued and nothing else.
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('db', 'sensor_data', '5', 10, ?)",
            (now,),
        )

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    rows = _outbox_rows(db_path)
    sensor_pks = [pk for t, pk in rows if t == "sensor_data"]
    # Only rows 6..10 were enqueued — the first 5 were already shipped
    # in the simulated crashed run.
    assert sensor_pks == [str(i) for i in ids[5:]]


def test_start_db_bootstrap_cursor_advances_per_row(db_path):
    """After a successful full scan, last_pk should be the ROWID of the
    final row processed, and completed_at should be set."""
    ids = _seed_sensor_data(db_path, n=4)

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    progress = _bootstrap_progress(db_path, "db")
    sd = next(p for p in progress if p["scope"] == "sensor_data")
    assert sd["completed_at"] is not None
    assert sd["total_rows"] == 4
    # last_pk should track ROWID of the final seeded row. ROWIDs match
    # autoincrement PK for a fresh schema.
    assert sd["last_pk"] == str(ids[-1])


def test_start_db_bootstrap_small_batch_size(db_path):
    """The default batch size is 1000; test with a small batch size to
    exercise the multi-batch loop. Use the public method with the
    private kwarg or fall back to calling _scan_table directly."""
    ids = _seed_sensor_data(db_path, n=7)

    scanner = BootstrapScanner(db_path)
    # Call the private _scan_table directly with a tiny batch_size so
    # the loop iterates multiple times.
    scanner._scan_table("sensor_data", batch_size=3)

    rows = _outbox_rows(db_path)
    sensor_pks = [pk for t, pk in rows if t == "sensor_data"]
    assert sensor_pks == [str(i) for i in ids]

    progress = _bootstrap_progress(db_path, "db")
    sd = next(p for p in progress if p["scope"] == "sensor_data")
    assert sd["completed_at"] is not None


# ── Files bootstrap ──────────────────────────────────────────────────


def test_start_files_bootstrap_empty_directory_completes(db_path, tmp_path):
    """Empty filesystem tree → completed_at set, no blobs enqueued."""
    root = tmp_path / "grow_images"
    root.mkdir()

    scanner = BootstrapScanner(db_path)
    scanner.start_files_bootstrap([("photo", root)])

    assert _outbox_blobs(db_path) == []
    progress = _bootstrap_progress(db_path, "files")
    assert len(progress) == 1
    assert progress[0]["scope"] == str(root)
    assert progress[0]["completed_at"] is not None


def test_start_files_bootstrap_enqueues_every_file(db_path, tmp_path):
    """Walks rglob and creates one outbox_blobs entry per file with
    target_key relative to the root (not root.parent) — matches the
    live writer in photo_storage.handle_photo_frame which uses
    `unit_NNN/YYYY-MM-DD/HHMMSS_mmm.jpg`."""
    root = tmp_path / "grow_images"
    (root / "unit_001" / "2026-05-18").mkdir(parents=True)
    (root / "unit_002" / "2026-05-19").mkdir(parents=True)

    f1 = root / "unit_001" / "2026-05-18" / "120000.jpg"
    f2 = root / "unit_002" / "2026-05-19" / "081530.jpg"
    f1.write_bytes(b"fake-jpeg-1")
    f2.write_bytes(b"fake-jpeg-2")

    scanner = BootstrapScanner(db_path)
    scanner.start_files_bootstrap([("photo", root)])

    blobs = _outbox_blobs(db_path)
    assert len(blobs) == 2

    expected_keys = {
        "unit_001/2026-05-18/120000.jpg",
        "unit_002/2026-05-19/081530.jpg",
    }
    actual_keys = {target_key for _kind, _src, target_key, _sha in blobs}
    assert actual_keys == expected_keys

    # sha256 must match the on-disk bytes.
    for kind, src, target_key, sha in blobs:
        assert kind == "photo"
        # source_path is absolute on disk.
        assert Path(src).read_bytes() == (
            b"fake-jpeg-1" if target_key.startswith("unit_001/") else b"fake-jpeg-2"
        )
        expected_sha = hashlib.sha256(Path(src).read_bytes()).hexdigest()
        assert sha == expected_sha


def test_start_files_bootstrap_resumable_skips_processed(db_path, tmp_path):
    """If a previous run set last_pk to a particular relative path, the
    resume must skip files at-or-before that path and only process the
    rest. Without this, every crash would re-hash the entire tree."""
    root = tmp_path / "grow_images"
    (root / "unit_001").mkdir(parents=True)
    files = []
    for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]:
        f = root / "unit_001" / name
        f.write_bytes(name.encode())
        files.append(f)

    # Plant a partial-progress row: previous bootstrap got through
    # unit_001/b.jpg before crashing. Expect resume to enqueue c.jpg
    # and d.jpg only.
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, last_pk, total_rows, started_at) "
            "VALUES ('files', ?, 'unit_001/b.jpg', 4, ?)",
            (str(root), now),
        )

    scanner = BootstrapScanner(db_path)
    scanner.start_files_bootstrap([("photo", root)])

    blobs = _outbox_blobs(db_path)
    target_keys = sorted(target_key for _k, _s, target_key, _sh in blobs)
    assert target_keys == ["unit_001/c.jpg", "unit_001/d.jpg"]


def test_start_files_bootstrap_skips_completed_root(db_path, tmp_path):
    """Re-running file bootstrap on an already-completed root is a
    no-op — even if new files have appeared since the original
    completion. Same semantics as the DB pipeline (completed scopes
    are skipped)."""
    root = tmp_path / "grow_images"
    (root / "unit_001").mkdir(parents=True)
    (root / "unit_001" / "first.jpg").write_bytes(b"first")

    scanner = BootstrapScanner(db_path)
    scanner.start_files_bootstrap([("photo", root)])
    first_pass = _outbox_blobs(db_path)

    # Add a new file AFTER the first scan completed.
    (root / "unit_001" / "second.jpg").write_bytes(b"second")

    scanner.start_files_bootstrap([("photo", root)])
    second_pass = _outbox_blobs(db_path)

    assert first_pass == second_pass, (
        "completed-scope check should skip the second pass entirely"
    )


def test_start_files_bootstrap_multiple_roots(db_path, tmp_path):
    """The list-of-(kind, root) shape lets callers wire multiple
    pipeline kinds in one call — e.g. photos + anomaly artefacts."""
    photos_root = tmp_path / "grow_images"
    anomaly_root = tmp_path / "anomaly"
    (photos_root / "unit_001").mkdir(parents=True)
    anomaly_root.mkdir()

    (photos_root / "unit_001" / "x.jpg").write_bytes(b"j")
    (anomaly_root / "model.pkl").write_bytes(b"p")

    scanner = BootstrapScanner(db_path)
    scanner.start_files_bootstrap([
        ("photo", photos_root),
        ("anomaly", anomaly_root),
    ])

    blobs = _outbox_blobs(db_path)
    kinds = {kind for kind, _s, _k, _sh in blobs}
    assert kinds == {"photo", "anomaly"}

    progress = _bootstrap_progress(db_path, "files")
    scopes = {p["scope"] for p in progress}
    assert scopes == {str(photos_root), str(anomaly_root)}
    assert all(p["completed_at"] is not None for p in progress)


# ── reset() ──────────────────────────────────────────────────────────


def test_reset_db_pipeline_clears_all_scopes(db_path):
    """reset(pipeline='db') wipes every bootstrap_progress row for the
    db pipeline so a Force-re-bootstrap admin action can restart from
    zero. The files pipeline rows are left untouched."""
    _seed_sensor_data(db_path, n=2)
    # Also need to plant a 'files' row to verify isolation.
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, started_at, completed_at) "
            "VALUES ('files', '/some/dir', ?, ?)",
            (now, now),
        )

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()
    assert len(_bootstrap_progress(db_path, "db")) > 0

    scanner.reset("db")
    assert _bootstrap_progress(db_path, "db") == []
    # Files row untouched.
    assert len(_bootstrap_progress(db_path, "files")) == 1


def test_reset_specific_scope(db_path, tmp_path):
    """reset(pipeline, scope=...) clears one scope only."""
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO bootstrap_progress "
            "(pipeline, scope, started_at, completed_at) VALUES (?, ?, ?, ?)",
            [
                ("db", "sensor_data", now, now),
                ("db", "weather_log", now, now),
                ("files", "/foo", now, now),
            ],
        )

    scanner = BootstrapScanner(db_path)
    scanner.reset("db", "sensor_data")

    remaining = _bootstrap_progress(db_path, "db")
    assert {p["scope"] for p in remaining} == {"weather_log"}
    # files row untouched
    assert len(_bootstrap_progress(db_path, "files")) == 1


def test_reset_nonexistent_is_noop(db_path):
    """Calling reset on a pipeline/scope that has no rows is a silent
    no-op — used during the admin Force-re-bootstrap flow which may
    fire before the first bootstrap has ever run."""
    scanner = BootstrapScanner(db_path)
    scanner.reset("db")  # no rows yet — must not raise
    scanner.reset("files", "nonexistent")  # ditto


# ── Full-circle: bootstrap → reset → re-bootstrap ────────────────────


def test_reset_then_re_bootstrap_picks_up_again(db_path):
    """After reset() the next start_db_bootstrap re-enqueues every
    row — this is the admin Force-re-bootstrap flow end-to-end."""
    ids = _seed_sensor_data(db_path, n=3)

    scanner = BootstrapScanner(db_path)
    scanner.start_db_bootstrap()

    # First pass enqueued every row.
    first_pks = [pk for t, pk in _outbox_rows(db_path) if t == "sensor_data"]
    assert first_pks == [str(i) for i in ids]

    # Clear pending outbox + progress, then re-bootstrap.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM outbox_changes")
    scanner.reset("db")

    scanner.start_db_bootstrap()
    second_pks = [pk for t, pk in _outbox_rows(db_path) if t == "sensor_data"]
    assert second_pks == [str(i) for i in ids], (
        "after reset, every row is re-enqueued from scratch"
    )

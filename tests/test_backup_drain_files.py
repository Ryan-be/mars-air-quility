"""_drain.drain_files_batch — drains outbox_blobs to S3.

Uses a real SQLite tempfile (the outbox tables are created by
init_db.create_db) + a mocked S3Client (no actual S3 needed; the live
integration test wires a real instance in Phase 6).

Covered:
  - _bucket_suffix_for_key helper (all 4 known prefixes + unknown raises)
  - empty outbox → no-op, returns False
  - happy path: HEAD missing → PUT → outbox entry dropped
  - idempotency: HEAD already present → skip PUT but still drop entry
  - source-missing on disk → log + drop without network round-trip
  - bucket routing for all 4 target_key prefixes in one batch
  - unknown target_key prefix → log + drop (schema drift guard)
  - errors propagate from put + head (run loop catches → BACKOFF)
  - missing-source drop on one entry doesn't abort the rest of the batch

The ``db_path`` fixture is provided by ``tests/conftest.py``.
"""
import sqlite3
from unittest.mock import MagicMock
import pytest

from mlss_monitor.backup import outbox
from mlss_monitor.backup._drain import drain_files_batch, _bucket_suffix_for_key


@pytest.fixture
def s3_client():
    return MagicMock()


@pytest.fixture
def tmp_jpeg(tmp_path):
    """A small file on disk that pretends to be a photo. Just needs to
    exist for the source-path existence check; contents are irrelevant
    (S3Client.put is mocked)."""
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 256)
    return str(p)


# ── Bucket suffix derivation ──────────────────────────────────────

def test_bucket_suffix_for_photo_key():
    """unit_NNN/... → photos bucket. Source: grow photo uploads from
    Phase 2 file pipeline writers."""
    assert _bucket_suffix_for_key("unit_001/2026-05-18/120000.jpg") == "photos"


def test_bucket_suffix_for_anomaly_key():
    """anomaly/... → anomaly bucket. Source: per-channel anomaly model
    snapshots."""
    assert _bucket_suffix_for_key("anomaly/tvoc_ppb/2026-05-18T12:00:00.pkl") == "anomaly"


def test_bucket_suffix_for_multivar_anomaly_key():
    """multivar_anomaly/... → multivar-anomaly bucket (note the
    underscore→hyphen normalisation: S3 bucket naming uses hyphens by
    convention, but the on-disk filesystem layout uses underscores to
    match the Python module names)."""
    assert _bucket_suffix_for_key("multivar_anomaly/voc_combo/2026-05-18T12:00:00.pkl") == "multivar-anomaly"


def test_bucket_suffix_for_attribution_key():
    """attribution/... → attribution bucket. Source: attribution
    classifier model snapshots."""
    assert _bucket_suffix_for_key("attribution/classifier/2026-05-18T12:00:00.pkl") == "attribution"


def test_bucket_suffix_for_unknown_key_raises():
    """A target_key with no recognised prefix is a programming error —
    the drain loop catches this and treats it as schema drift (log +
    drop), but the helper itself raises so the caller can decide."""
    with pytest.raises(ValueError, match="Cannot derive S3 bucket"):
        _bucket_suffix_for_key("some_random/path/file.bin")


# ── Drain function: empty / basic shipping ────────────────────────

def test_drain_files_batch_empty_returns_false(db_path, s3_client):
    """No pending blobs → no S3 calls, returns False. The run loop
    (Task 15) uses False to flip the worker state back to IDLE."""
    with sqlite3.connect(db_path) as conn:
        result = drain_files_batch(conn, s3_client)
    assert result is False
    s3_client.put.assert_not_called()
    s3_client.head.assert_not_called()


def test_drain_files_batch_uploads_one_blob(db_path, s3_client, tmp_jpeg):
    """Happy path: HEAD says missing, PUT succeeds, outbox entry
    drops. This is the dominant case in production."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path=tmp_jpeg,
                target_key="unit_001/2026-05-18/120000.jpg",
                sha256="abc",
            )
    s3_client.head.return_value = False  # missing on server
    with sqlite3.connect(db_path) as conn:
        result = drain_files_batch(conn, s3_client)
    assert result is True
    s3_client.head.assert_called_once_with(
        bucket_suffix="photos", key="unit_001/2026-05-18/120000.jpg",
    )
    s3_client.put.assert_called_once_with(
        bucket_suffix="photos",
        key="unit_001/2026-05-18/120000.jpg",
        source_path=tmp_jpeg,
        sha256="abc",
    )
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_blobs(conn) == 0


def test_drain_files_batch_skips_put_when_head_returns_true(db_path, s3_client, tmp_jpeg):
    """Idempotency: if the blob is already on S3 (e.g. previous ship
    succeeded but the worker crashed before the outbox delete
    committed), HEAD-check returns True and we skip the upload but
    still drop the outbox entry. Re-uploading would be a waste of
    bandwidth + would overwrite metadata."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path=tmp_jpeg,
                target_key="unit_001/2026-05-18/120000.jpg",
                sha256="abc",
            )
    s3_client.head.return_value = True  # already on server
    with sqlite3.connect(db_path) as conn:
        drain_files_batch(conn, s3_client)
    s3_client.put.assert_not_called()
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_blobs(conn) == 0


def test_drain_files_batch_drops_entry_when_source_missing(db_path, s3_client):
    """Source file deleted between enqueue and ship (e.g. operator
    cleared photos via clear_photos route, which also unlinks the
    on-disk JPEG). Log + drop the outbox entry — don't HEAD-check,
    don't upload. Same shape as the DB drain's missing-live-row
    case: append-mostly artefacts disappear on the Pi side without
    propagating a delete to the server."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path="/tmp/this/path/does/not/exist.jpg",
                target_key="unit_001/2026-05-18/120000.jpg",
                sha256="abc",
            )
    with sqlite3.connect(db_path) as conn:
        drain_files_batch(conn, s3_client)
    # No network round-trip for dead-source entries.
    s3_client.head.assert_not_called()
    s3_client.put.assert_not_called()
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_blobs(conn) == 0


def test_drain_files_batch_routes_by_target_key_prefix(db_path, s3_client, tmp_path):
    """Four entries with different target_key prefixes → four different
    bucket_suffix values on the S3Client.put call args. Verifies the
    end-to-end routing via _bucket_suffix_for_key."""
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"j")
    anom = tmp_path / "anom.pkl"
    anom.write_bytes(b"p")
    multi = tmp_path / "multi.pkl"
    multi.write_bytes(b"m")
    attr = tmp_path / "attr.pkl"
    attr.write_bytes(b"a")

    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(conn, kind="photo", source_path=str(photo),
                                target_key="unit_001/2026-05-18/120000.jpg", sha256="1")
            outbox.enqueue_blob(conn, kind="model", source_path=str(anom),
                                target_key="anomaly/tvoc_ppb/2026.pkl", sha256="2")
            outbox.enqueue_blob(conn, kind="model", source_path=str(multi),
                                target_key="multivar_anomaly/voc_combo/2026.pkl", sha256="3")
            outbox.enqueue_blob(conn, kind="model", source_path=str(attr),
                                target_key="attribution/classifier/2026.pkl", sha256="4")
    s3_client.head.return_value = False

    with sqlite3.connect(db_path) as conn:
        drain_files_batch(conn, s3_client)

    bucket_suffixes_used = {call.kwargs["bucket_suffix"]
                            for call in s3_client.put.call_args_list}
    assert bucket_suffixes_used == {"photos", "anomaly", "multivar-anomaly", "attribution"}


def test_drain_files_batch_unknown_target_key_logs_and_drops(db_path, s3_client, tmp_path):
    """An outbox entry with a target_key that doesn't match any known
    bucket prefix shouldn't crash the drain — log + drop so the queue
    progresses past the schema-drift entry. (If this fires in
    production, _bucket_suffix_for_key is out of date relative to a
    new file pipeline writer.)

    Uses explicit close (not ``with sqlite3.connect``) because the
    drain emits a WARNING log on the schema-drift branch — pytest's
    log capture appears to retain the connection object via the
    captured stack frame on Windows, blocking the tempfile unlink in
    the fixture teardown."""
    f = tmp_path / "x.bin"
    f.write_bytes(b"x")
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            outbox.enqueue_blob(
                conn, kind="model",
                source_path=str(f),
                target_key="some_unknown/path/file.bin",
                sha256="x",
            )
    finally:
        conn.close()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            drain_files_batch(conn, s3_client)
    finally:
        conn.close()
    s3_client.put.assert_not_called()
    conn = sqlite3.connect(db_path)
    try:
        assert outbox.pending_count_blobs(conn) == 0
    finally:
        conn.close()


# ── Failure semantics ─────────────────────────────────────────────

def test_drain_files_batch_propagates_s3_errors(db_path, s3_client, tmp_jpeg):
    """S3 upload failures (network, 5xx, etc.) propagate so the Task 15
    run loop transitions to BACKOFF. Outbox entry is NOT deleted on
    failure — the retry picks it up next cycle."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path=tmp_jpeg,
                target_key="unit_001/2026-05-18/120000.jpg",
                sha256="abc",
            )
    s3_client.head.return_value = False
    s3_client.put.side_effect = Exception("S3 connection refused")
    # Explicit close in finally — `with sqlite3.connect(...) as conn:`
    # only commits/rolls back on exit, it does NOT close the connection,
    # and an unclosed handle blocks the Windows teardown unlink.
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="S3 connection refused"):
            drain_files_batch(conn, s3_client)
    finally:
        conn.close()
    # Outbox entry NOT deleted on failure.
    conn = sqlite3.connect(db_path)
    try:
        assert outbox.pending_count_blobs(conn) > 0
    finally:
        conn.close()


def test_drain_files_batch_propagates_head_errors(db_path, s3_client, tmp_jpeg):
    """HEAD-check errors (auth, network) propagate too — caller (the
    run loop) decides the retry strategy, not the drain function. PUT
    must not be attempted when HEAD fails because we don't know whether
    the object exists."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(
                conn, kind="photo",
                source_path=tmp_jpeg,
                target_key="unit_001/2026-05-18/120000.jpg",
                sha256="abc",
            )
    s3_client.head.side_effect = Exception("AccessDenied")
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(Exception, match="AccessDenied"):
            drain_files_batch(conn, s3_client)
    finally:
        conn.close()
    s3_client.put.assert_not_called()


def test_drain_files_batch_continues_after_drop_on_missing_source(db_path, s3_client, tmp_jpeg):
    """If entry 1's source is missing (drop+continue) but entry 2 has a
    valid source, entry 2 should still be processed in the same batch.
    One bad entry must not abort the rest of the queue."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            outbox.enqueue_blob(conn, kind="photo",
                                source_path="/nonexistent.jpg",
                                target_key="unit_001/2026-05-18/120000.jpg",
                                sha256="x")
            outbox.enqueue_blob(conn, kind="photo",
                                source_path=tmp_jpeg,
                                target_key="unit_002/2026-05-18/120000.jpg",
                                sha256="y")
    s3_client.head.return_value = False
    with sqlite3.connect(db_path) as conn:
        result = drain_files_batch(conn, s3_client)
    assert result is True
    # Only entry 2 was actually uploaded.
    assert s3_client.put.call_count == 1
    assert s3_client.put.call_args.kwargs["sha256"] == "y"
    # Both entries gone from outbox (1 dropped, 1 shipped).
    with sqlite3.connect(db_path) as conn:
        assert outbox.pending_count_blobs(conn) == 0

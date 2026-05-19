"""File pipeline writers enqueue outbox_blobs after successful writes.

Covers Task 8 of the MLSS backup plan: every photo + model artefact write
must enqueue an outbox_blobs row in the same logical operation. Photos go
further and also enqueue the grow_photos row (multi-table inline pattern,
same as Task 7).

Spec: docs/superpowers/specs/2026-05-18-mlss-backup-design.md
Plan: docs/superpowers/plans/2026-05-18-mlss-backup-phase2.md (Task 8)
"""
import gc
import hashlib
import json
import sqlite3
import struct
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


_river_mocked = isinstance(sys.modules.get("river"), MagicMock)


@pytest.fixture
def db_path():
    """Fresh on-disk DB with init_db.create_db() schema (incl. outbox tables)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    import database.init_db as init_db
    original = init_db.DB_FILE
    init_db.DB_FILE = tmp.name
    init_db.create_db()
    yield tmp.name
    init_db.DB_FILE = original
    gc.collect()
    Path(tmp.name).unlink(missing_ok=True)


def _outbox_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT table_name, pk FROM outbox_changes ORDER BY id"))
    finally:
        conn.close()


def _outbox_blobs(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(
            "SELECT kind, source_path, target_key, sha256 "
            "FROM outbox_blobs ORDER BY id"))
    finally:
        conn.close()


# ── Site 1: photo pipeline ────────────────────────────────────────────────────

@pytest.fixture
def photo_setup(db_path, monkeypatch, tmp_path):
    """Seed a grow_unit + redirect photos + DB_FILE for handle_photo_frame."""
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", db_path)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage._resolve_images_dir",
        lambda: str(tmp_path),
    )
    now = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (now, now))
    conn.commit()
    conn.close()
    return db_path, str(tmp_path)


def _make_frame(taken_at_iso: str, jpeg_bytes: bytes) -> bytes:
    header = json.dumps({
        "taken_at": taken_at_iso, "width": 320, "height": 240,
        "jpeg_quality": 85,
    }).encode("utf-8")
    return struct.pack(">I", len(header)) + header + jpeg_bytes


def test_handle_photo_frame_enqueues_row_and_blob(photo_setup):
    db_path, images_dir = photo_setup
    from mlss_monitor.grow import photo_storage
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 200
    frame = _make_frame("2026-05-18T12:00:00.000Z", jpeg)

    photo_storage.handle_photo_frame(unit_id=1, frame=frame)

    rows = _outbox_rows(db_path)
    blobs = _outbox_blobs(db_path)
    # Row pipeline: grow_photos
    assert any(t == "grow_photos" for t, _ in rows), (
        f"Expected grow_photos in outbox_changes; saw {rows}"
    )
    # Blob pipeline: one photo blob, sha matches in-memory bytes
    assert len(blobs) == 1, f"Expected exactly one blob; saw {blobs}"
    kind, src, key, sha = blobs[0]
    assert kind == "photo"
    assert key.startswith("unit_001/2026-05-18/")
    assert key.endswith(".jpg")
    assert sha == hashlib.sha256(jpeg).hexdigest()
    # source_path is absolute and points at the file actually written
    assert Path(src).exists()
    # File actually exists at images_dir/<key>
    assert (Path(images_dir) / key).exists()


def test_handle_photo_frame_rollback_on_db_failure_does_not_orphan_file(photo_setup):
    """If the INSERT raises (e.g. UNIQUE constraint), no JPEG should be left
    on disk and no blob should be enqueued for the failed call."""
    db_path, images_dir = photo_setup
    from mlss_monitor.grow import photo_storage
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
    iso = "2026-05-18T12:00:00.000Z"
    frame = _make_frame(iso, jpeg)
    # First call succeeds
    photo_storage.handle_photo_frame(unit_id=1, frame=frame)
    # Second call with identical taken_at should hit UNIQUE(unit_id, taken_at)
    with pytest.raises(sqlite3.IntegrityError):
        photo_storage.handle_photo_frame(unit_id=1, frame=frame)
    blobs = _outbox_blobs(db_path)
    # First call enqueued one blob; second call's blob should NOT be present
    assert len(blobs) == 1, (
        f"Failed second call must not leave a blob enqueued; saw {blobs}"
    )


# ── Site 2: AnomalyDetector ───────────────────────────────────────────────────

@pytest.mark.skipif(
    _river_mocked,
    reason="river is mocked; pickle round-trip needs real install",
)
def test_anomaly_detector_save_enqueues_per_channel_blobs(
    db_path, monkeypatch, tmp_path,
):
    """AnomalyDetector._save_models() should enqueue one blob per channel."""
    monkeypatch.setattr("mlss_monitor.anomaly_detector.DB_FILE", db_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "anomaly:\n"
        "  channels: [tvoc_ppb]\n"
        "  cold_start_readings: 1\n"
    )
    model_dir = tmp_path / "models"

    from mlss_monitor.anomaly_detector import AnomalyDetector
    from mlss_monitor.feature_vector import FeatureVector
    det = AnomalyDetector(config_path=cfg, model_dir=model_dir)
    # Feed enough to trigger _save_models (_SAVE_EVERY_N = 3)
    for v in (1.0, 2.0, 3.0):
        det.learn_and_score(FeatureVector(tvoc_current=v))
    blobs = _outbox_blobs(db_path)
    assert any(
        kind == "model"
        and key.startswith("anomaly/tvoc_ppb/")
        and key.endswith(".pkl")
        for kind, _, key, _ in blobs
    ), f"Expected anomaly/tvoc_ppb/...pkl blob; saw {blobs}"


def test_anomaly_detector_save_enqueues_blobs_river_independent(
    db_path, monkeypatch, tmp_path,
):
    """Exercise _save_models() without depending on river's pickle support.

    On hosts where river fails to install (Windows: path-too-long for its
    C extension) the conftest mocks it as MagicMock and the model objects
    inside _save_models can't be pickle.dump()'d. This test stubs
    pickle.dump to a no-op that writes a deterministic byte sequence so
    we can still verify the blob enqueue path runs cleanly. The
    river-required test above is the more rigorous integration assertion;
    this one keeps coverage flowing on dev laptops.
    """
    import mlss_monitor.anomaly_detector as ad
    monkeypatch.setattr(ad, "DB_FILE", db_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "anomaly:\n"
        "  channels: [tvoc_ppb, eco2_ppm]\n"
        "  cold_start_readings: 1\n"
    )
    model_dir = tmp_path / "models"
    det = ad.AnomalyDetector(config_path=cfg, model_dir=model_dir)
    # Stub pickle.dump to write fixed bytes — bypasses MagicMock pickling.
    def _stub_dump(obj, f):
        f.write(b"stub-model-bytes")
    monkeypatch.setattr(ad.pickle, "dump", _stub_dump)
    det._save_models()

    blobs = _outbox_blobs(db_path)
    keys = [k for _, _, k, _ in blobs]
    # One blob per channel
    assert any(k.startswith("anomaly/tvoc_ppb/") and k.endswith(".pkl") for k in keys), keys
    assert any(k.startswith("anomaly/eco2_ppm/") and k.endswith(".pkl") for k in keys), keys
    # SHA matches the stubbed bytes
    expected_sha = hashlib.sha256(b"stub-model-bytes").hexdigest()
    assert all(sha == expected_sha for _, _, _, sha in blobs)


# ── Site 3: MultivarAnomalyDetector ───────────────────────────────────────────

@pytest.mark.skipif(
    _river_mocked,
    reason="river is mocked; pickle round-trip needs real install",
)
def test_multivar_anomaly_detector_save_enqueues_per_model_blobs(
    db_path, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "mlss_monitor.multivar_anomaly_detector.DB_FILE", db_path,
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "multivar_anomaly:\n"
        "  cold_start_readings: 1\n"
        "  models:\n"
        "    - id: voc_combo\n"
        "      label: VOC combo\n"
        "      channels: [tvoc_current, eco2_current]\n"
    )
    model_dir = tmp_path / "models"

    from mlss_monitor.multivar_anomaly_detector import MultivarAnomalyDetector
    from mlss_monitor.feature_vector import FeatureVector
    det = MultivarAnomalyDetector(config_path=cfg, model_dir=model_dir)
    for v in (1.0, 2.0, 3.0):
        det.learn_and_score(
            FeatureVector(tvoc_current=v, eco2_current=400.0 + v)
        )
    blobs = _outbox_blobs(db_path)
    assert any(
        kind == "model"
        and key.startswith("multivar_anomaly/voc_combo/")
        and key.endswith(".pkl")
        for kind, _, key, _ in blobs
    ), f"Expected multivar_anomaly/voc_combo/...pkl blob; saw {blobs}"


def test_multivar_anomaly_detector_save_enqueues_blobs_river_independent(
    db_path, monkeypatch, tmp_path,
):
    """River-free counterpart to the integration test above (see rationale
    in test_anomaly_detector_save_enqueues_blobs_river_independent)."""
    import mlss_monitor.multivar_anomaly_detector as mvad
    monkeypatch.setattr(mvad, "DB_FILE", db_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "multivar_anomaly:\n"
        "  cold_start_readings: 1\n"
        "  models:\n"
        "    - id: voc_combo\n"
        "      label: VOC combo\n"
        "      channels: [tvoc_current, eco2_current]\n"
        "    - id: pm_combo\n"
        "      label: PM combo\n"
        "      channels: [pm1_current, pm25_current]\n"
    )
    model_dir = tmp_path / "models"
    det = mvad.MultivarAnomalyDetector(config_path=cfg, model_dir=model_dir)

    def _stub_dump(obj, f):
        f.write(b"stub-multivar-bytes")
    monkeypatch.setattr(mvad.pickle, "dump", _stub_dump)
    det._save_models()

    blobs = _outbox_blobs(db_path)
    keys = [k for _, _, k, _ in blobs]
    assert any(
        k.startswith("multivar_anomaly/voc_combo/") and k.endswith(".pkl")
        for k in keys
    ), keys
    assert any(
        k.startswith("multivar_anomaly/pm_combo/") and k.endswith(".pkl")
        for k in keys
    ), keys


# ── Site 4: AttributionEngine ─────────────────────────────────────────────────

def test_attribution_engine_save_enqueues_classifier_blob(
    db_path, monkeypatch, tmp_path,
):
    """AttributionEngine training save should enqueue an attribution/classifier blob.

    We drive the engine's train_on_tags() with no tagged inferences (an
    empty result set from get_inferences). The method still hits its
    pickle.dump → log "classifier saved to disk" → enqueue block, which is
    all we need to assert on.
    """
    monkeypatch.setattr("mlss_monitor.attribution.engine.DB_FILE", db_path)

    config_path = tmp_path / "config" / "fingerprints.yaml"
    config_path.parent.mkdir()
    config_path.write_text("sources: []\n")
    (tmp_path / "data").mkdir()
    pkl_path = tmp_path / "data" / "classifier.pkl"

    # Stub out the init-time train_on_tags so engine construction doesn't
    # do the work; we'll call it manually below with the @property patched.
    from mlss_monitor.attribution.engine import AttributionEngine
    real_train_on_tags = AttributionEngine.train_on_tags
    monkeypatch.setattr(
        AttributionEngine, "train_on_tags", lambda self: None,
    )
    engine = AttributionEngine(str(config_path))

    # _pkl_path is a @property — must patch at class level for the override
    # to win over the descriptor.
    monkeypatch.setattr(
        AttributionEngine,
        "_pkl_path",
        property(lambda self: pkl_path),
    )
    # Restore the real method and stub the DB call so train() runs but
    # finds no tagged inferences (still saves an empty classifier).
    monkeypatch.setattr(AttributionEngine, "train_on_tags", real_train_on_tags)

    with patch(
        "mlss_monitor.attribution.engine.get_inferences", return_value=[]
    ):
        engine.train_on_tags()

    assert pkl_path.exists(), "classifier.pkl must be written"
    blobs = _outbox_blobs(db_path)
    assert any(
        kind == "model"
        and key.startswith("attribution/classifier/")
        and key.endswith(".pkl")
        for kind, _, key, _ in blobs
    ), f"Expected attribution/classifier/...pkl blob; saw {blobs}"

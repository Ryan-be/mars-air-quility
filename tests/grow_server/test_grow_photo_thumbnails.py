"""Phase 4 Item #1: server-side thumbnail variant for grow photos.

Endpoints under test:
  GET /api/grow/units/<id>/photo/latest?size=thumb
  GET /api/grow/units/<id>/photos/<photo_id>?size=thumb

Cache invalidation under test:
  DELETE /api/grow/units/<id>/photos must wipe the thumbnail directory
  too, otherwise stale thumbnails of deleted photos linger on disk.

Test images are generated via Pillow rather than seeded as fake-JPEG
bytes (as in test_grow_photos_api.py) because the resize path actually
calls Image.open() and would fail on a non-decodable payload. The
generator returns a 1280x720 solid-color JPEG so we can verify the
thumbnail came back at the requested 320px width.
"""
import io
import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image


def _make_jpeg(width=1280, height=720, color=(180, 90, 40)) -> bytes:
    """Generate a real JPEG of the given dimensions for fixture seeding."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _set_session(c, *, role="admin"):
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_role"] = role


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Single-photo fixture for /photo/latest tests."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    images_root = tmp_path / "imgs"
    thumbs_root = tmp_path / "thumbs"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_root))
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_THUMBNAILS_DIR", str(thumbs_root))
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp.name)
    init_db.create_db()

    img_dir = images_root / "unit_001" / "2026-05-08"
    img_dir.mkdir(parents=True)
    jpeg_body = _make_jpeg()
    (img_dir / "120000.jpg").write_bytes(jpeg_body)

    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
        "height_px, size_bytes) VALUES (1, ?, ?, 1280, 720, ?)",
        (datetime(2026, 5, 8, 12, 0, 0), "unit_001/2026-05-08/120000.jpg",
         len(jpeg_body)),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_photos_bp)
    return {
        "client": app.test_client(),
        "db_path": tmp.name,
        "images_root": images_root,
        "thumbs_root": thumbs_root,
        "jpeg_body": jpeg_body,
    }


@pytest.fixture
def photos_client(tmp_path, monkeypatch):
    """Multi-photo fixture wired through api_grow_units (for clear-photos
    cache invalidation test) plus api_grow_photos (for thumbnail GETs).

    Three photos with real JPEG bodies live on disk under
    ``imgs/unit_001/2026-05-08/``. Tests can hit ``?size=thumb`` to
    populate the thumbnail cache, then call ``DELETE /photos`` and
    confirm the thumb dir was wiped.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.routes.api_grow_units.DB_FILE", tmp.name
    )
    monkeypatch.setattr("mlss_monitor.grow.auth.DB_FILE", tmp.name)
    monkeypatch.setattr("mlss_monitor.grow.health_watchdog.DB_FILE", tmp.name)
    images_root = tmp_path / "imgs"
    thumbs_root = tmp_path / "thumbs"
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(images_root))
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_THUMBNAILS_DIR", str(thumbs_root))
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp.name)
    init_db.create_db()

    photo_dir = images_root / "unit_001" / "2026-05-08"
    photo_dir.mkdir(parents=True)
    photo_paths = []
    for i in range(3):
        rel = f"unit_001/2026-05-08/13000{i}.jpg"
        body = _make_jpeg(color=(50 + i * 60, 100, 200 - i * 40))
        (images_root / rel).write_bytes(body)
        photo_paths.append((rel, body))

    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (1, 'hw-1', 'Tom 1', ?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    photo_ids = []
    for i, (rel, body) in enumerate(photo_paths):
        cur = conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (1, ?, ?, 1280, 720, ?)",
            (datetime(2026, 5, 8, 13, 0, i), rel, len(body)),
        )
        photo_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()

    # api_grow_units's clear-buffer push path expects state.grow_ws_loop
    # to exist. clear_photos doesn't actually push a WS message, but
    # importing api_grow_units pulls the registry in unconditionally.
    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    from mlss_monitor.routes.api_grow_units import api_grow_units_bp
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(api_grow_photos_bp)
    app.register_blueprint(api_grow_units_bp)
    tc = app.test_client()
    _set_session(tc, role="admin")
    return {
        "client": tc,
        "db_path": tmp.name,
        "images_root": images_root,
        "thumbs_root": thumbs_root,
        "photo_paths": photo_paths,
        "photo_ids": photo_ids,
    }


# ---------------------------------------------------------------------------
# /photo/latest?size=thumb
# ---------------------------------------------------------------------------


def test_thumbnail_generated_on_first_request(setup):
    """First ?size=thumb request populates the cache and serves a JPEG
    that's ~320px wide. Verifies the resize actually happened."""
    r = setup["client"].get("/api/grow/units/1/photo/latest?size=thumb")
    assert r.status_code == 200, r.data
    assert r.mimetype == "image/jpeg"
    # Decode the response to confirm the resize actually happened.
    img = Image.open(io.BytesIO(r.data))
    assert img.width == 320, (
        f"thumbnail should be 320px wide, got {img.width}px"
    )
    # Aspect ratio preserved: 1280x720 → 320x180
    assert img.height == 180, (
        f"thumbnail height should preserve 16:9 aspect, got {img.height}px"
    )


def test_thumbnail_response_smaller_than_original(setup):
    """Sanity check: thumbnail body is materially smaller than the
    source. The original is 1280x720 JPEG (~tens of KB even for solid
    color); the thumbnail at 320x180 should be a fraction of that."""
    orig = setup["client"].get("/api/grow/units/1/photo/latest")
    thumb = setup["client"].get("/api/grow/units/1/photo/latest?size=thumb")
    assert orig.status_code == 200
    assert thumb.status_code == 200
    assert len(thumb.data) < len(orig.data), (
        f"thumbnail ({len(thumb.data)}B) must be smaller than "
        f"original ({len(orig.data)}B)"
    )


def test_thumbnail_cached_on_disk(setup):
    """First request creates the cache file; second request reads it
    from disk (verified via file mtime not changing on the second hit)."""
    c = setup["client"]
    thumbs_root = setup["thumbs_root"]

    r1 = c.get("/api/grow/units/1/photo/latest?size=thumb")
    assert r1.status_code == 200

    cache_path = (
        thumbs_root / "unit_001" / "2026-05-08" / "120000_w320.jpg"
    )
    assert cache_path.exists(), (
        f"thumbnail should be cached at {cache_path}"
    )
    mtime_after_first = cache_path.stat().st_mtime

    # Second request should not regenerate (mtime unchanged).
    r2 = c.get("/api/grow/units/1/photo/latest?size=thumb")
    assert r2.status_code == 200
    assert r2.data == r1.data
    mtime_after_second = cache_path.stat().st_mtime
    assert mtime_after_second == mtime_after_first, (
        "second request must not regenerate the cached thumbnail"
    )


def test_thumbnail_unknown_size_400(setup):
    """?size=large isn't in the allowlist → 400, not silent fallback."""
    r = setup["client"].get("/api/grow/units/1/photo/latest?size=large")
    assert r.status_code == 400


def test_thumbnail_no_size_param_serves_original(setup):
    """Without ?size, the original is served (preserves pre-Phase-4 behavior)."""
    c = setup["client"]
    orig = c.get("/api/grow/units/1/photo/latest")
    thumb = c.get("/api/grow/units/1/photo/latest?size=thumb")
    assert orig.status_code == 200
    assert thumb.status_code == 200
    # Original should be the seeded JPEG bytes; thumbnail is re-encoded.
    assert orig.data == setup["jpeg_body"], (
        "original endpoint should still serve untouched source bytes"
    )
    assert thumb.data != setup["jpeg_body"], (
        "thumbnail endpoint should serve re-encoded bytes"
    )


def test_thumbnail_latest_keeps_short_cache_header(setup):
    """The 5s ``/photo/latest`` Cache-Control window applies to the
    thumbnail too — content varies (latest row changes when a new photo
    lands), so it can't be marked immutable."""
    r = setup["client"].get("/api/grow/units/1/photo/latest?size=thumb")
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert "max-age=5" in cc, (
        f"expected max-age=5 on thumbnail of /photo/latest, got {cc!r}"
    )
    assert "immutable" not in cc, (
        "/photo/latest thumbnail must not be marked immutable"
    )


# ---------------------------------------------------------------------------
# /photos/<id>?size=thumb
# ---------------------------------------------------------------------------


def test_thumbnail_by_id_serves_resized_jpeg(photos_client):
    """GET /photos/<id>?size=thumb returns a 320px-wide JPEG."""
    pid = photos_client["photo_ids"][0]
    r = photos_client["client"].get(f"/api/grow/units/1/photos/{pid}?size=thumb")
    assert r.status_code == 200, r.data
    img = Image.open(io.BytesIO(r.data))
    assert img.width == 320


def test_thumbnail_by_id_uses_immutable_long_cache(photos_client):
    """The per-id immutable cache directive applies to thumbnails too —
    same source ID always resizes to the same bytes (we never overwrite
    a source JPEG and the resize is deterministic)."""
    pid = photos_client["photo_ids"][0]
    r = photos_client["client"].get(f"/api/grow/units/1/photos/{pid}?size=thumb")
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert "max-age=31536000" in cc, cc
    assert "immutable" in cc, cc


def test_thumbnail_by_id_unknown_size_400(photos_client):
    pid = photos_client["photo_ids"][0]
    r = photos_client["client"].get(f"/api/grow/units/1/photos/{pid}?size=xxl")
    assert r.status_code == 400


def test_thumbnail_by_id_404_for_unknown_photo(photos_client):
    """Unknown photo ID 404s before the resize is attempted."""
    r = photos_client["client"].get("/api/grow/units/1/photos/99999?size=thumb")
    assert r.status_code == 404


def test_thumbnail_by_id_404_for_cross_unit_photo(photos_client):
    """Cross-unit security must hold for the thumbnail variant too —
    unit 1's URL with unit 2's photo id must 404, not leak a thumbnail."""
    # Seed a unit-2 photo
    db_path = photos_client["db_path"]
    images_root = photos_client["images_root"]
    unit2_dir = images_root / "unit_002" / "2026-05-08"
    unit2_dir.mkdir(parents=True)
    unit2_rel = "unit_002/2026-05-08/secret.jpg"
    (images_root / unit2_rel).write_bytes(_make_jpeg(color=(0, 200, 0)))

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active) "
        "VALUES (2, 'hw-2', 'Tom 2', ?, 'h', ?, 1)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    cur = conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
        "width_px, height_px, size_bytes) VALUES (2, ?, ?, 1280, 720, 1)",
        (datetime(2026, 5, 8, 14, 0, 0), unit2_rel),
    )
    unit2_pid = cur.lastrowid
    conn.commit()
    conn.close()

    r = photos_client["client"].get(
        f"/api/grow/units/1/photos/{unit2_pid}?size=thumb"
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cache invalidation: DELETE /photos wipes the thumbnail dir too
# ---------------------------------------------------------------------------


def test_thumbnail_cache_cleared_on_clear_photos(photos_client):
    """Populate thumbnail cache, call DELETE /photos, confirm the
    thumbnail dir for the unit is gone (or at least empty).

    On Windows, Flask's test client holds a file handle on a streamed
    send_from_directory response until ``.close()`` is called explicitly
    (Linux's reference-count GC doesn't have this hang). We close the
    populating response before issuing the delete so the rmtree's unlink
    of the cached file can actually proceed.
    """
    c = photos_client["client"]
    thumbs_root = photos_client["thumbs_root"]
    pid = photos_client["photo_ids"][0]

    # Populate cache by hitting the thumbnail endpoint.
    r1 = c.get(f"/api/grow/units/1/photos/{pid}?size=thumb")
    assert r1.status_code == 200
    r1.close()  # release the file handle on Windows

    unit_thumbs_dir = thumbs_root / "unit_001"
    assert unit_thumbs_dir.exists(), "fixture sanity: thumbnail cache should exist"
    assert any(unit_thumbs_dir.rglob("*.jpg")), (
        "fixture sanity: thumbnail cache should have JPEGs"
    )

    # Wipe via DELETE /photos.
    r2 = c.delete("/api/grow/units/1/photos")
    assert r2.status_code == 200, r2.data

    # Cache dir should be gone or empty.
    assert (
        not unit_thumbs_dir.exists()
        or not any(unit_thumbs_dir.rglob("*.jpg"))
    ), (
        "thumbnail cache for unit must be wiped when clear-photos runs"
    )


def test_clear_photos_does_not_explode_when_no_thumbnail_cache(photos_client):
    """The clear-photos path should be idempotent vs. the cache —
    if ?size=thumb was never called, the thumbnail dir doesn't exist,
    and rmtree must not fail the whole wipe."""
    c = photos_client["client"]
    # Skip populating the cache; go straight to wipe.
    r = c.delete("/api/grow/units/1/photos")
    assert r.status_code == 200
    assert r.get_json()["deleted_count"] == 3

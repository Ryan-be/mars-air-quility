"""GET /api/grow/units/<id>/photo/latest serves the latest photo file.

Also covers the History tab endpoints:
  GET /api/grow/units/<id>/photos                    — list photos in a range
  GET /api/grow/units/<id>/photos/<photo_id>         — fetch a single JPEG by id
"""
import sqlite3
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    # Both endpoints now resolve via _resolve_images_dir (photo_storage).
    # Patch the storage module's GROW_IMAGES_DIR fallback so when the
    # app_settings override is empty, both fall through to the tmp dir.
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp.name)
    init_db.create_db()
    img_dir = tmp_path / "imgs" / "unit_001" / "2026-05-03"
    img_dir.mkdir(parents=True)
    (img_dir / "120000.jpg").write_bytes(b"\xff\xd8FAKEJPEG")

    conn = sqlite3.connect(tmp.name)
    # Empty value → _resolve_images_dir falls through to env/built-in.
    # The default test posture; individual tests can override.
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
        "('grow_images_dir', '')"
    )
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h', 'X', ?, 'h', ?)",
        (datetime.utcnow(), datetime.utcnow()),
    )
    conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
        "height_px, size_bytes) VALUES (1, ?, ?, 100, 100, 9)",
        (datetime(2026, 5, 3, 12, 0, 0), "unit_001/2026-05-03/120000.jpg"),
    )
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_photos_bp)
    return {"client": app.test_client(), "db_path": tmp.name, "tmp_path": tmp_path}


def test_latest_serves_jpeg(setup):
    r = setup["client"].get("/api/grow/units/1/photo/latest")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == b"\xff\xd8FAKEJPEG"


def test_latest_404_for_unit_with_no_photos(setup):
    r = setup["client"].get("/api/grow/units/9999/photo/latest")
    assert r.status_code == 404


def test_latest_photo_honours_app_settings_grow_images_dir_override(setup):
    """Regression: latest_photo previously used a module-level env-only
    constant ignoring app_settings.grow_images_dir. Now it resolves via
    _resolve_images_dir, so an admin-set override takes effect.

    Set up two dirs:
      - dir_a (the env-default in the fixture, contains FAKEJPEG)
      - dir_b (the override, contains a different body at the same relpath)
    Set app_settings.grow_images_dir = dir_b → response body must be dir_b's.
    """
    tmp_path = setup["tmp_path"]
    db_path = setup["db_path"]

    # dir_a is already populated by the fixture under tmp_path/imgs.
    # Build dir_b at a separate location with a different body.
    dir_b = tmp_path / "imgs_override"
    img_dir_b = dir_b / "unit_001" / "2026-05-03"
    img_dir_b.mkdir(parents=True)
    override_body = b"\xff\xd8OVERRIDEJPEG"
    (img_dir_b / "120000.jpg").write_bytes(override_body)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
        "('grow_images_dir', ?)", (str(dir_b),),
    )
    conn.commit()
    conn.close()

    r = setup["client"].get("/api/grow/units/1/photo/latest")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    # The dir_b override won — proves the route consults app_settings,
    # not just the module-level constant from the env.
    assert r.data == override_body


# ---------------------------------------------------------------------------
# /photos list endpoint + /photos/<id> fetch endpoint (History tab — Task 2)
# ---------------------------------------------------------------------------


@pytest.fixture
def list_setup(tmp_path, monkeypatch):
    """Fixture for the list/by-id endpoint tests.

    Seeds two units (unit 1 and unit 2 — the second one exists so the
    cross-unit security test has a real photo on disk to attempt to leak)
    plus a controlled set of grow_photos rows at staggered timestamps.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    import database.init_db as init_db
    init_db.DB_FILE = tmp.name
    monkeypatch.setattr("mlss_monitor.routes.api_grow_photos.DB_FILE", tmp.name)
    # Both endpoints resolve via _resolve_images_dir; we only need to
    # patch the storage-module fallback (no longer a routes-module copy).
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", str(tmp_path / "imgs"))
    monkeypatch.setattr("mlss_monitor.grow.photo_storage.DB_FILE", tmp.name)
    init_db.create_db()

    images_root = tmp_path / "imgs"
    now = datetime.utcnow()

    conn = sqlite3.connect(tmp.name)
    # Two units so the cross-unit security test has a meaningful target
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (1, 'h1', 'A', ?, 'h', ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at) VALUES (2, 'h2', 'B', ?, 'h', ?)",
        (now, now),
    )
    # A telemetry row to give the photos a real telemetry_id to denormalise
    conn.execute(
        "INSERT INTO grow_telemetry (id, unit_id, timestamp_utc, "
        "soil_moisture_raw, light_state, pump_state) "
        "VALUES (500, 1, ?, 612, 1, 0)", (now,),
    )

    # Photo timestamps for unit 1 — five photos staggered:
    #   t-2h, t-12h, t-23h (all within 24h)
    #   t-2d, t-10d (outside 24h, inside 30d/90d/all)
    photo_offsets_h = [2, 12, 23, 48, 240]
    photos = []  # (relpath, taken_at, telemetry_id)
    for i, off_h in enumerate(photo_offsets_h):
        ts = now - timedelta(hours=off_h)
        date_dir = ts.strftime("%Y-%m-%d")
        rel_path = f"unit_001/{date_dir}/seed_{i}.jpg"
        abs_dir = images_root / "unit_001" / date_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        # Distinct fake JPEG body per file so the by-id test can verify bytes match
        body = b"\xff\xd8\xff\xe0SEED" + bytes([i]) + b"\x00" * 64
        (images_root / rel_path).write_bytes(body)
        photos.append((rel_path, ts, 500 if i == 0 else None, body))

    photo_ids = []
    for rel_path, ts, tel_id, _body in photos:
        cur = conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
            "height_px, size_bytes, telemetry_id) "
            "VALUES (1, ?, ?, 100, 100, ?, ?)",
            (ts, rel_path, len(_body), tel_id),
        )
        photo_ids.append(cur.lastrowid)

    # One photo for unit 2 — the cross-unit security test attempts to read
    # this photo via the unit-1 URL; it MUST 404, not leak.
    unit2_ts = now - timedelta(hours=1)
    unit2_dir = images_root / "unit_002" / unit2_ts.strftime("%Y-%m-%d")
    unit2_dir.mkdir(parents=True, exist_ok=True)
    unit2_rel = f"unit_002/{unit2_ts.strftime('%Y-%m-%d')}/secret.jpg"
    unit2_body = b"\xff\xd8UNIT2SECRET" + b"\x00" * 32
    (images_root / unit2_rel).write_bytes(unit2_body)
    cur2 = conn.execute(
        "INSERT INTO grow_photos (unit_id, taken_at, file_path, width_px, "
        "height_px, size_bytes) VALUES (2, ?, ?, 100, 100, ?)",
        (unit2_ts, unit2_rel, len(unit2_body)),
    )
    unit2_photo_id = cur2.lastrowid
    conn.commit()
    conn.close()

    from flask import Flask
    from mlss_monitor.routes.api_grow_photos import api_grow_photos_bp
    app = Flask(__name__)
    app.register_blueprint(api_grow_photos_bp)
    return {
        "client": app.test_client(),
        "photo_ids": photo_ids,           # unit-1 photo IDs (5 total, oldest→newest order matches insert order)
        "photo_bodies": [p[3] for p in photos],
        "photo_offsets_h": photo_offsets_h,
        "unit2_photo_id": unit2_photo_id,
        "unit2_body": unit2_body,
    }


def test_photos_list_returns_all_photos_in_range(list_setup):
    """Seed 5 photos at staggered timestamps; ?range=24h returns only those
    within 24h (3 of 5), sorted by taken_at ASC."""
    r = list_setup["client"].get("/api/grow/units/1/photos?range=24h")
    assert r.status_code == 200
    data = r.get_json()
    # The three photos at -23h, -12h, -2h are inside 24h; -2d and -10d are outside.
    assert len(data) == 3
    # Sorted ASC by taken_at
    timestamps = [entry["taken_at"] for entry in data]
    assert timestamps == sorted(timestamps)


def test_photos_list_returns_id_and_taken_at_only(list_setup):
    """Response shape: [{id, taken_at, telemetry_id}, …].
    No file_path, no image bytes."""
    r = list_setup["client"].get("/api/grow/units/1/photos?range=24h")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) > 0
    for entry in data:
        assert set(entry.keys()) == {"id", "taken_at", "telemetry_id"}
        assert "file_path" not in entry


def test_photos_list_supports_range_all(list_setup):
    """?range=all returns every photo for the unit (no cutoff)."""
    r = list_setup["client"].get("/api/grow/units/1/photos?range=all")
    assert r.status_code == 200
    data = r.get_json()
    # All 5 unit-1 photos
    assert len(data) == 5


def test_photos_list_supports_range_90d(list_setup):
    """Sanity-check: 90d range covers all seeded photos (max offset 10d)."""
    r = list_setup["client"].get("/api/grow/units/1/photos?range=90d")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) == 5


def test_photos_list_invalid_range_400(list_setup):
    r = list_setup["client"].get("/api/grow/units/1/photos?range=bogus")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_photos_list_returns_empty_array_for_unit_with_no_photos(list_setup):
    """A unit with no photos returns [], not 404."""
    r = list_setup["client"].get("/api/grow/units/9999/photos?range=24h")
    assert r.status_code == 200
    assert r.get_json() == []


def test_photo_by_id_serves_jpeg(list_setup):
    """GET /api/grow/units/<id>/photos/<photo_id> → 200, content-type
    image/jpeg, body matches the file bytes."""
    photo_ids = list_setup["photo_ids"]
    bodies = list_setup["photo_bodies"]
    # First seeded photo
    pid = photo_ids[0]
    r = list_setup["client"].get(f"/api/grow/units/1/photos/{pid}")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == bodies[0]


def test_photo_by_id_404_for_unknown_id(list_setup):
    r = list_setup["client"].get("/api/grow/units/1/photos/99999")
    assert r.status_code == 404


def test_photo_by_id_404_when_photo_belongs_to_different_unit(list_setup):
    """Security: unit 1's URL must not be able to fetch unit 2's photo by
    quoting unit 2's photo_id. The query MUST cross-check unit_id."""
    other_unit_pid = list_setup["unit2_photo_id"]
    # Confirm the photo really exists under its own unit (sanity check)
    r_legit = list_setup["client"].get(f"/api/grow/units/2/photos/{other_unit_pid}")
    assert r_legit.status_code == 200, "fixture sanity: unit 2's URL should fetch unit 2's photo"
    # The actual security assertion — unit 1 URL with unit 2 photo id
    r = list_setup["client"].get(f"/api/grow/units/1/photos/{other_unit_pid}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cache headers — fix for "timelapse re-fetches every photo every navigation"
# ---------------------------------------------------------------------------
#
# /photo/latest serves time-varying content (the latest row changes when a
# new snap-photo lands), so it gets a tiny 5s cache window. /photos/<id>
# is per-id immutable (we never overwrite committed JPEGs), so it gets the
# conventional 1-year + immutable directive — letting the browser skip
# revalidation entirely on timelapse re-scrub. Default Flask
# send_from_directory ships no Cache-Control without max_age=, so the
# browser's heuristic-freshness kicks in and re-validates constantly,
# which is the bug we're closing.


def test_latest_photo_sets_short_cache(setup):
    """latest changes when a new photo lands → short max-age, no immutable."""
    r = setup["client"].get("/api/grow/units/1/photo/latest")
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert "max-age=5" in cc, f"expected max-age=5 on /photo/latest, got {cc!r}"
    assert "immutable" not in cc, \
        "/photo/latest must NOT be marked immutable — content varies"


def test_photo_by_id_sets_immutable_long_cache(list_setup):
    """photo_by_id is content-stable per id → long max-age + immutable so
    timelapse re-scrub doesn't re-fetch every photo on every navigation."""
    pid = list_setup["photo_ids"][0]
    r = list_setup["client"].get(f"/api/grow/units/1/photos/{pid}")
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    # 1 year = 31536000 seconds
    assert "max-age=31536000" in cc, \
        f"expected max-age=31536000 on /photos/<id>, got {cc!r}"
    assert "immutable" in cc, \
        f"expected `immutable` directive on /photos/<id>, got {cc!r}"

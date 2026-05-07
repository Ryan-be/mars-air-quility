"""End-to-end stack test for the History-tab flow (History Task 6).

Boots the *real* Flask app (with ``state.github_oauth`` mocked truthy so
the global ``check_auth`` middleware is engaged — same posture as a
production deployment), seeds a unit with telemetry rows + photo files
on disk, and exercises the four GET endpoints behind the History tab
through the full stack:

    admin browser -> Flask route (with check_auth) -> DB read
                  -> (for /photos/<id>) send_from_directory off the
                  fixture's tmp images dir -> JPEG bytes back

This is the cross-task integration coverage that the per-endpoint unit
tests in ``test_grow_history.py`` and ``test_grow_photos_api.py`` can't
capture: those tests stand up bare ``Flask()`` apps with a single
blueprint registered and bypass the auth middleware. Here we route
through the production blueprint registration, the OAuth-on auth gate,
and a real session — so a regression in any of those layers fails this
file.

Unlike ``test_configure_e2e.py`` there is no WS listener: History is a
pure HTTP read feature with no server-to-firmware push, so the fixture
is correspondingly simpler (no ``_FakeFirmware``, no listener handle,
no ``state.grow_ws_registry``).

Test order independence: each test gets its own fixture instance with a
fresh tmp DB and fresh tmp images dir. No test reads or writes shared
state across runs.
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixture builders. The base seeder is parameterised by ``telemetry_count``
# so the downsample test can swap 100 -> 1000 without duplicating the rest
# of the setup.
# ---------------------------------------------------------------------------


# Distinct fake JPEG body per photo so the by-id test can verify bytes match.
def _fake_jpeg(seed: int) -> bytes:
    return b"\xff\xd8\xff\xe0FAKEJPG" + bytes([seed]) + b"\x00" * 100


def _build_history_stack(monkeypatch, tmp_path, telemetry_count: int):
    """Assemble the real Flask app + tmp DB + seeded data.

    Returns a dict shaped like the ``configured_stack`` bundle in
    ``test_configure_e2e.py`` minus the WS bits.
    """
    # ── 1. Tmp DB + DB_FILE patches across every grow module ──
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)  # pylint: disable=R1732
    tmp.close()
    images_dir = str(tmp_path / "imgs")
    os.makedirs(images_dir, exist_ok=True)

    import database.init_db as init_db
    import database.db_logger as dbl
    import database.user_db as udb
    init_db.DB_FILE = tmp.name
    dbl.DB_FILE = tmp.name
    udb.DB_FILE = tmp.name
    for mod in [
        "mlss_monitor.grow.auth",
        "mlss_monitor.grow.handlers",
        "mlss_monitor.grow.photo_storage",
        "mlss_monitor.routes.api_grow_enroll",
        "mlss_monitor.routes.api_grow_units",
        "mlss_monitor.routes.api_grow_dist",
        "mlss_monitor.routes.api_grow_history",
        "mlss_monitor.routes.api_grow_photos",
        "mlss_monitor.routes.api_grow_ws",
        "mlss_monitor.routes.api_grow_config",
    ]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", tmp.name)
        except AttributeError:
            pass
    # latest_photo + photo_by_id both resolve via _resolve_images_dir()
    # which falls back to photo_storage.GROW_IMAGES_DIR. Patch the storage
    # module's copy so app_settings-less tests still resolve to the tmp dir.
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", images_dir
    )
    init_db.create_db()

    # ── 2. Seed unit, telemetry rows, photo rows + files ──
    now = datetime.utcnow()
    base_ts = now - timedelta(hours=23)  # spread the data within last 24h

    photo_ids = []
    photo_bodies = []
    with sqlite3.connect(tmp.name) as conn:
        conn.execute(
            "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
            "bearer_token_hash, phase_set_at) "
            "VALUES (1, 'hw-history-e2e', 'E2E History', ?, 'h', ?)",
            (now, now),
        )

        # Telemetry: ``telemetry_count`` rows evenly spread across the last 23h
        # so the 24h range covers them all. Spacing varies depending on count.
        if telemetry_count > 0:
            spread_seconds = 23 * 3600
            interval_s = spread_seconds / telemetry_count
            for i in range(telemetry_count):
                ts = base_ts + timedelta(seconds=i * interval_s)
                pct = 50 + (i % 30) - 15  # oscillates 35-65
                raw = 600 + (i % 200)
                conn.execute(
                    "INSERT INTO grow_telemetry "
                    "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
                    " light_state, pump_state) VALUES (?, ?, ?, ?, 1, 0)",
                    (1, ts, raw, pct),
                )

        # 10 photos staggered across the last ~20 hours (within 24h range).
        for i in range(10):
            taken_at = base_ts + timedelta(hours=i * 2)
            date_dir = taken_at.strftime("%Y-%m-%d")
            # millisecond-precision filename matches the production layout in
            # mlss_monitor/grow/photo_storage.py
            rel_path = (
                f"unit_001/{date_dir}/"
                f"{taken_at.strftime('%H%M%S_%f')[:-3]}.jpg"
            )
            abs_dir = os.path.join(images_dir, "unit_001", date_dir)
            os.makedirs(abs_dir, exist_ok=True)
            body = _fake_jpeg(i)
            with open(os.path.join(images_dir, rel_path), "wb") as f:
                f.write(body)
            cur = conn.execute(
                "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
                "width_px, height_px, size_bytes) VALUES (?, ?, ?, ?, ?, ?)",
                (1, taken_at, rel_path, 1920, 1080, len(body)),
            )
            photo_ids.append(cur.lastrowid)
            photo_bodies.append(body)
        conn.commit()

    # ── 3. Boot the real Flask app with OAuth-on posture ──
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "github_oauth", MagicMock())  # auth ON
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-history-e2e"

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-admin"
        sess["user_role"] = "admin"

    return {
        "app":          app_module.app,
        "client":       client,
        "unit_id":      1,
        "db_path":      tmp.name,
        "images_dir":   images_dir,
        "photo_ids":    photo_ids,    # sorted by taken_at ASC (insertion order)
        "photo_bodies": photo_bodies,
    }


@pytest.fixture
def history_stack(tmp_path, monkeypatch):
    """Default stack: 100 telemetry rows + 10 photos for unit 1."""
    return _build_history_stack(monkeypatch, tmp_path, telemetry_count=100)


@pytest.fixture
def history_stack_1000(tmp_path, monkeypatch):
    """Stack with 1000 telemetry rows so the downsample threshold (600) trips."""
    return _build_history_stack(monkeypatch, tmp_path, telemetry_count=1000)


@pytest.fixture
def history_stack_with_unit2_photo(tmp_path, monkeypatch):
    """Default stack + a single photo seeded under unit 2.

    For the cross-unit security test: ``photo_by_id`` must 404 when
    asked for unit 2's photo via the unit 1 URL. Without a real photo
    on disk we'd be testing the missing-file path instead of the
    cross-unit query guard.
    """
    bundle = _build_history_stack(monkeypatch, tmp_path, telemetry_count=100)
    # Seed unit 2 + one photo
    now = datetime.utcnow()
    with sqlite3.connect(bundle["db_path"]) as conn:
        conn.execute(
            "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
            "bearer_token_hash, phase_set_at) "
            "VALUES (2, 'hw-unit2', 'E2E Other', ?, 'h', ?)",
            (now, now),
        )
        taken_at = now - timedelta(hours=1)
        date_dir = taken_at.strftime("%Y-%m-%d")
        rel_path = (
            f"unit_002/{date_dir}/"
            f"{taken_at.strftime('%H%M%S_%f')[:-3]}.jpg"
        )
        abs_dir = os.path.join(bundle["images_dir"], "unit_002", date_dir)
        os.makedirs(abs_dir, exist_ok=True)
        body = b"\xff\xd8UNIT2_SECRET" + b"\x00" * 32
        with open(os.path.join(bundle["images_dir"], rel_path), "wb") as f:
            f.write(body)
        cur = conn.execute(
            "INSERT INTO grow_photos (unit_id, taken_at, file_path, "
            "width_px, height_px, size_bytes) VALUES (?, ?, ?, ?, ?, ?)",
            (2, taken_at, rel_path, 1920, 1080, len(body)),
        )
        bundle["unit2_photo_id"] = cur.lastrowid
        bundle["unit2_body"] = body
        conn.commit()
    return bundle


# ---------------------------------------------------------------------------
# Test 1: 24h range with 100 rows -> raw shape (no downsample).
# ---------------------------------------------------------------------------


def test_history_24h_returns_raw_shape(history_stack):
    """The fixture's 100 telemetry rows are well under the 600 downsample
    threshold, so the response keeps the legacy ``{ts, pct, raw}`` shape.
    Frontend chart code depends on this for short ranges."""
    bundle = history_stack
    r = bundle["client"].get(f"/api/grow/units/{bundle['unit_id']}/history?range=24h")
    assert r.status_code == 200, r.data
    body = r.get_json()
    # 100 rows, all within the 24h window
    assert len(body["moisture"]) == 100
    sample = body["moisture"][0]
    # Raw shape only — no downsample keys
    assert set(sample.keys()) == {"ts", "pct", "raw"}
    # Reserved key always present so the frontend doesn't have to defensively
    # check for it.
    assert body["phase_changes"] == []


# ---------------------------------------------------------------------------
# Test 2: 30d range with 1000 rows -> downsampled shape.
# ---------------------------------------------------------------------------


def test_history_30d_with_1000_points_downsamples(history_stack_1000):
    """1000 rows trip the 600 downsample threshold. The response array
    is at most 600 entries and each entry carries the bucketed
    ``{ts, pct_min, pct_avg, pct_max, raw_avg}`` shape — proving the
    downsample logic round-trips through the real Flask layer."""
    bundle = history_stack_1000
    r = bundle["client"].get(f"/api/grow/units/{bundle['unit_id']}/history?range=30d")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert len(body["moisture"]) <= 600
    # Sanity: must actually be downsampled (i.e. < 1000), not just clipped
    assert len(body["moisture"]) < 1000
    sample = body["moisture"][0]
    assert "pct_avg" in sample, (
        f"downsampled response should carry pct_avg key, got {sample!r}"
    )
    # Full downsampled key set
    assert set(sample.keys()) == {"ts", "pct_min", "pct_avg", "pct_max", "raw_avg"}


# ---------------------------------------------------------------------------
# Test 3: range=all returns 200 (no cutoff).
# ---------------------------------------------------------------------------


def test_history_all_range_works(history_stack):
    """``range=all`` omits the cutoff filter — sanity check that the
    real route accepts it through the auth middleware and returns the
    expected envelope."""
    bundle = history_stack
    r = bundle["client"].get(f"/api/grow/units/{bundle['unit_id']}/history?range=all")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert "moisture" in body
    assert "watering_events" in body
    assert "phase_changes" in body
    # 100 seeded rows, no cutoff -> all returned (well under downsample threshold)
    assert len(body["moisture"]) == 100


# ---------------------------------------------------------------------------
# Test 4: /photos list returns the seeded photos sorted by taken_at ASC.
# ---------------------------------------------------------------------------


def test_photos_list_returns_seeded_photos_in_order(history_stack):
    """All 10 seeded photos fall within 24h. The list endpoint returns
    each as ``{id, taken_at, telemetry_id}`` sorted ASC by taken_at."""
    bundle = history_stack
    r = bundle["client"].get(f"/api/grow/units/{bundle['unit_id']}/photos?range=24h")
    assert r.status_code == 200, r.data
    data = r.get_json()
    assert len(data) == 10
    timestamps = [entry["taken_at"] for entry in data]
    assert timestamps == sorted(timestamps), "photos must be ASC by taken_at"
    # Shape per entry — only the timeline-cheap metadata, no file paths
    for entry in data:
        assert set(entry.keys()) == {"id", "taken_at", "telemetry_id"}
    # IDs match the fixture's insertion order (seeded ASC by taken_at)
    assert [entry["id"] for entry in data] == bundle["photo_ids"]


# ---------------------------------------------------------------------------
# Test 5: /photos/<id> serves the JPEG file bytes off disk.
# ---------------------------------------------------------------------------


def test_photo_by_id_serves_jpeg_bytes(history_stack):
    """The by-id route resolves the file via _resolve_images_dir() and
    streams JPEG bytes back. The body must match the fixture file
    bytes exactly — proves the path resolution chain works end-to-end."""
    bundle = history_stack
    first_id = bundle["photo_ids"][0]
    expected_body = bundle["photo_bodies"][0]
    r = bundle["client"].get(
        f"/api/grow/units/{bundle['unit_id']}/photos/{first_id}"
    )
    assert r.status_code == 200, r.data
    assert r.mimetype == "image/jpeg"
    assert r.data == expected_body


# ---------------------------------------------------------------------------
# Test 6: cross-unit security — unit 1 URL cannot fetch unit 2's photo by id.
# ---------------------------------------------------------------------------


def test_photo_by_id_404_for_other_units_photo(history_stack_with_unit2_photo):
    """Security cross-check: even though unit 2's photo exists on disk
    and in the DB, asking for it via the unit 1 URL must 404 (the SQL
    cross-checks ``unit_id``). Confirms the production blueprint does
    NOT regress to a leaky single-key lookup."""
    bundle = history_stack_with_unit2_photo
    other_pid = bundle["unit2_photo_id"]
    # Sanity: the photo really exists when asked for under its own unit URL.
    r_legit = bundle["client"].get(f"/api/grow/units/2/photos/{other_pid}")
    assert r_legit.status_code == 200, (
        f"fixture sanity: unit 2's URL should fetch unit 2's photo, got "
        f"{r_legit.status_code} {r_legit.data!r}"
    )
    # Real assertion — wrong unit + valid photo id -> 404.
    r = bundle["client"].get(f"/api/grow/units/1/photos/{other_pid}")
    assert r.status_code == 404, (
        f"unit 1 URL must not fetch unit 2's photo {other_pid}; got "
        f"{r.status_code} {r.data!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: viewer role can read history (read-only data, no @require_role).
#
# Posture: the history + photos endpoints have no ``@require_role`` decorator.
# They're protected only by the global ``check_auth`` middleware, which lets
# any logged-in user through (admin OR viewer). This test pins that the
# read posture is "any logged-in user" — if a future change adds
# ``@require_role("admin")`` to the history endpoint, this test will flag
# it explicitly so the change is intentional.
# ---------------------------------------------------------------------------


def test_history_viewer_role_can_read(history_stack):
    """Viewer session can GET /history just like an admin. History is
    read-only data — the viewer role exists precisely so non-admin users
    can look at their plants. If this test fails with 403, someone has
    added an admin-only RBAC guard; either intentional (update this
    test) or a regression (revert)."""
    bundle = history_stack
    # Build a fresh test client with a viewer session — don't reuse the
    # admin client because session_transaction context-manager updates are
    # additive and we want a clean role discriminator.
    viewer = bundle["app"].test_client()
    with viewer.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-viewer"
        sess["user_role"] = "viewer"
    r = viewer.get(f"/api/grow/units/{bundle['unit_id']}/history?range=24h")
    assert r.status_code == 200, (
        f"viewer should be able to read history (no @require_role on the "
        f"endpoint); got {r.status_code} {r.data!r}"
    )
    body = r.get_json()
    assert "moisture" in body
    assert "phase_changes" in body

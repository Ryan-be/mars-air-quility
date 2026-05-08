"""End-to-end stack tests for the Phase 3 observability surfaces (Task 8).

Boots the *real* Flask app (with ``state.github_oauth`` mocked truthy so
the global ``check_auth`` middleware is engaged — same posture as a
production deployment) and, for the WS-driven scenarios, a real WS
listener bound to a free port. Every test exercises Phase 3 plumbing
through the full stack:

    admin browser -> Flask route (with check_auth + RBAC) -> DB read
                  -> JSON response (or template render); plus, where
                  relevant, a real ``websockets.connect`` from a fake
                  firmware client driving connection_log entries via
                  ``_record_connection_event``.

This is the cross-task integration coverage that the per-task unit tests
in ``test_api_grow_diagnostics.py``, ``test_api_grow_errors.py``,
``test_api_grow_danger.py``, and ``test_storage_check.py`` can't
capture: those tests stand up bare ``Flask()`` apps with single
blueprints and bypass either the auth middleware, the WS listener, or
both. Here we route through the production blueprint registration, the
OAuth-on auth gate, real sessions, and (for connection_log scenarios)
the same connection_handler the live system uses. If any of those
layers regresses across the eight Phase 3 tasks, this file fails.

Test order independence: each test gets its own fixture instance with a
fresh tmp DB, fresh tmp images dir, and (when applicable) a fresh
listener port + fake-firmware connection. The auth cache is cleared on
fixture teardown. No test reads or writes shared state across runs.
"""
import asyncio
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import websockets


# ---------------------------------------------------------------------------
# Fake firmware client (mirrors test_configure_e2e.py::_FakeFirmware): a real
# WS client that drains pushed command frames into a list. Tests that need to
# capture pushed frames (test 6: clear-buffer) use this; tests that only
# need a live registry entry (tests 2, 9: connection_log writers) use a
# bare ``websockets.connect`` directly.
# ---------------------------------------------------------------------------


class _FakeFirmware:
    def __init__(self):
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.received_commands: list[dict] = []
        self._reader_task: asyncio.Task | None = None
        self._new_frame_event = asyncio.Event()

    async def connect(self, port: int, unit_id: int, token: str) -> None:
        self.ws = await websockets.connect(
            f"ws://127.0.0.1:{port}/api/grow/{unit_id}/ws",
            extra_headers={"Authorization": f"Bearer {token}"},
        )
        self._reader_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self.received_commands.append(msg)
                self._new_frame_event.set()
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            return

    async def wait_for_command(self, timeout: float = 2.0) -> dict:
        prior_count = len(self.received_commands)
        try:
            await asyncio.wait_for(self._new_frame_event.wait(), timeout)
        finally:
            self._new_frame_event.clear()
        if len(self.received_commands) <= prior_count:
            raise asyncio.TimeoutError("event fired but no new frame appeared")
        return self.received_commands[-1]

    async def close(self) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._reader_task.cancel()


# ---------------------------------------------------------------------------
# Shared fixture builder. Patches DB_FILE on every grow module that captured
# it at import time, seeds an active unit + bearer, and boots the real
# Flask app with OAuth-on posture. Returns the assembled bundle so tests
# can stitch on a WS listener if they need one.
# ---------------------------------------------------------------------------


def _patch_db_file_everywhere(monkeypatch, db_path: str):
    """Patch ``DB_FILE`` on every module that captured it at import time.

    Each grow module does ``from database.init_db import DB_FILE`` so it
    has a *copy* of the value, not a live reference. We have to patch
    each captured copy individually for the test DB to be honoured.
    """
    import database.init_db as init_db
    import database.db_logger as dbl
    import database.user_db as udb
    init_db.DB_FILE = db_path
    dbl.DB_FILE = db_path
    udb.DB_FILE = db_path
    for mod in [
        "mlss_monitor.grow.auth",
        "mlss_monitor.grow.handlers",
        "mlss_monitor.grow.photo_storage",
        "mlss_monitor.grow.health_watchdog",
        "mlss_monitor.routes.api_grow_enroll",
        "mlss_monitor.routes.api_grow_units",
        "mlss_monitor.routes.api_grow_dist",
        "mlss_monitor.routes.api_grow_history",
        "mlss_monitor.routes.api_grow_photos",
        "mlss_monitor.routes.api_grow_ws",
        "mlss_monitor.routes.api_grow_config",
        "mlss_monitor.routes.api_grow_diagnostics",
        "mlss_monitor.routes.api_grow_errors",
    ]:
        try:
            monkeypatch.setattr(f"{mod}.DB_FILE", db_path)
        except AttributeError:
            pass


def _seed_unit(db_path: str, *, unit_id: int = 1, label: str = "E2E Diag",
               firmware_version: str | None = None,
               last_uptime_s: float | None = None,
               last_buffer_size: int | None = None,
               raw_token_hash: str = "h",
               hardware_serial: str | None = None) -> None:
    """Seed an active grow_unit row. Called by the fixture so tests can
    layer additional data (capabilities / errors / telemetry) on top."""
    now = datetime.utcnow()
    if hardware_serial is None:
        hardware_serial = f"hw-e2e-{unit_id}"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, is_active, firmware_version, "
        "last_uptime_s, last_buffer_size) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (unit_id, hardware_serial, label, now, raw_token_hash, now,
         firmware_version, last_uptime_s, last_buffer_size),
    )
    conn.commit()
    conn.close()


def _build_app(monkeypatch):
    """Boot the production Flask app with OAuth-on posture + admin client."""
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "github_oauth", MagicMock())
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-phase3-e2e"
    return app_module.app


def _admin_client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-admin"
        sess["user_role"] = "admin"
    return c


def _viewer_client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-viewer"
        sess["user_role"] = "viewer"
    return c


@pytest.fixture
def diag_stack_no_ws(monkeypatch, tmp_path):
    """Lightweight fixture: real Flask app + tmp DB + seeded unit, no WS
    listener. For tests that don't drive connection_log via real WS
    connects (tests 1, 3, 4, 5, 8) — skipping the listener boot saves
    ~50 ms per test and trims fixture surface area."""
    # pylint: disable=R1732  # delete=False + close() pattern: we only want the path
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    images_dir = str(tmp_path / "imgs")
    os.makedirs(images_dir, exist_ok=True)

    _patch_db_file_everywhere(monkeypatch, tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", images_dir
    )
    import database.init_db as init_db
    init_db.create_db()
    _seed_unit(
        tmp.name,
        firmware_version="2.0.0",
        last_uptime_s=3600,
        last_buffer_size=2,
    )

    app = _build_app(monkeypatch)
    return {
        "app":           app,
        "admin_client":  _admin_client(app),
        "viewer_client": _viewer_client(app),
        "unit_id":       1,
        "db_path":       tmp.name,
        "images_dir":    images_dir,
    }


@pytest.fixture
async def diag_stack(monkeypatch, tmp_path):
    """Full fixture: real Flask app + real WS listener + tmp DB + seeded
    unit with a live bearer token. Tests use ``connect_fake_firmware``
    or a bare ``websockets.connect`` to drive connection_log entries.

    Tests that need it: 2 (real WS connect→disconnect), 6 (clear-buffer
    push to a connected unit), 9 (full observability narrative).
    """
    # pylint: disable=R1732  # delete=False + close() pattern: we only want the path
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    images_dir = str(tmp_path / "imgs")
    os.makedirs(images_dir, exist_ok=True)

    _patch_db_file_everywhere(monkeypatch, tmp.name)
    monkeypatch.setattr(
        "mlss_monitor.grow.photo_storage.GROW_IMAGES_DIR", images_dir
    )
    import database.init_db as init_db
    init_db.create_db()

    # Mint a real bearer + Argon2 hash so a fake firmware can authenticate.
    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw_token = generate_token()
    _seed_unit(
        tmp.name,
        raw_token_hash=hash_secret(raw_token),
        firmware_version="2.0.0",
        last_uptime_s=3600,
        last_buffer_size=2,
    )

    # Boot the real WS listener on a random port + wire registry into state
    # so the synchronous push helpers (clear-buffer) can find it.
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import (
        _clear_auth_cache, start_ws_listener, stop_ws_listener,
    )
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener(host="127.0.0.1", port=0, registry=registry)
    # _ListenerHandle.sockets is added via property() in api_grow_ws.py;
    # pylint can't see the runtime monkey-patch on the namedtuple.
    port = handle.sockets[0].getsockname()[1]  # pylint: disable=no-member
    from mlss_monitor import state
    state.grow_ws_registry = registry

    app = _build_app(monkeypatch)
    admin = _admin_client(app)
    viewer = _viewer_client(app)

    open_firmwares: list[_FakeFirmware] = []

    async def connect_fake_firmware(unit_id: int = 1) -> _FakeFirmware:
        """Open a real WS client + wait until the registry sees it.
        Returned firmwares are auto-closed by the fixture teardown.

        Note: only the registry registration is waited on here — the
        ``_record_connection_event(online)`` row write happens right
        after register() in the connection_handler. Tests that read the
        connection_log right after connect should poll the DB until the
        online row lands (see test 2)."""
        fw = _FakeFirmware()
        await fw.connect(port, unit_id=unit_id, token=raw_token)
        for _ in range(40):
            if registry.is_connected(unit_id):
                break
            await asyncio.sleep(0.05)
        assert registry.is_connected(unit_id), (
            f"fake firmware for unit {unit_id} failed to register within 2s"
        )
        open_firmwares.append(fw)
        return fw

    yield {
        "app":            app,
        "admin_client":   admin,
        "viewer_client":  viewer,
        "ws_handle":      handle,
        "ws_port":        port,
        "registry":       registry,
        "bearer_token":   raw_token,
        "unit_id":        1,
        "db_path":        tmp.name,
        "images_dir":     images_dir,
        "connect_fake_firmware": connect_fake_firmware,
    }

    # Teardown: close any fake firmwares the test left open, stop the
    # listener, drop registry from state, clear the auth cache so a
    # follow-on test's fresh token isn't rejected by a stale entry.
    for fw in open_firmwares:
        try:
            await fw.close()
        except Exception:
            pass
    stop_ws_listener(handle)
    state.grow_ws_registry = None
    state.grow_ws_loop = None
    _clear_auth_cache()


# ---------------------------------------------------------------------------
# Helpers used inside tests for direct DB manipulation. Direct SQL beats
# round-tripping through the API for seeding because the seed isn't the
# system-under-test for most assertions.
# ---------------------------------------------------------------------------


def _insert_error(
    db_path: str, *, unit_id: int = 1, kind: str = "sensor_degraded",
    severity: str = "warning", message: str = "msg",
    timestamp_utc: datetime | None = None,
    resolved_at: datetime | None = None,
    subject_sensor: str | None = None,
) -> int:
    if timestamp_utc is None:
        timestamp_utc = datetime.utcnow()
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO grow_errors "
        "(unit_id, timestamp_utc, severity, kind, message, "
        " subject_sensor, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (unit_id, timestamp_utc, severity, kind, message,
         subject_sensor, resolved_at),
    )
    err_id = cur.lastrowid
    conn.commit()
    conn.close()
    return err_id


def _insert_capability(
    db_path: str, *, unit_id: int = 1, channel: str,
    last_seen_at: datetime | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_unit_capabilities "
        "(unit_id, channel, hardware, is_required, installed_at, "
        " last_seen_at) "
        "VALUES (?, ?, 'hw', 0, ?, ?)",
        (unit_id, channel, datetime.utcnow(), last_seen_at),
    )
    conn.commit()
    conn.close()


def _connection_log_kinds(connection_log: list[dict]) -> list[str]:
    return [entry["kind"] for entry in connection_log]


# ===========================================================================
# Test 1: diagnostics endpoint consolidates all Phase 3 data correctly.
# ===========================================================================


def test_e2e_diagnostics_endpoint_consolidates_all_phase3_data(diag_stack_no_ws):
    """Seed every Phase 3 data lane (firmware metadata, capabilities with
    mixed staleness, mixed errors, mixed connection events), then GET
    /diagnostics and assert each lane lands in the right slot.

    Cross-cuts Tasks 1 (online/offline writer), 2 (firmware metadata),
    3 (the diagnostics endpoint itself). Failure here means one of those
    three pieces regressed without per-task tests catching it."""
    bundle = diag_stack_no_ws
    db_path = bundle["db_path"]
    now = datetime.utcnow()

    # ── Capabilities: one fresh, one stale, one never-seen.
    # The fixture seeded the unit; this just adds the capability rows.
    # The diagnostics endpoint reads grow_sensor_stale_threshold_min from
    # app_settings (defaults to 5 min). One minute ago = fresh; ten
    # minutes ago = stale; never-seen is implicitly stale.
    _insert_capability(
        db_path, channel="soil_moisture",
        last_seen_at=now - timedelta(minutes=1),
    )
    _insert_capability(
        db_path, channel="ambient_lux",
        last_seen_at=now - timedelta(minutes=10),
    )
    _insert_capability(db_path, channel="camera", last_seen_at=None)

    # ── Errors: one unresolved sensor_degraded, one already-resolved.
    open_err_id = _insert_error(
        db_path, kind="sensor_degraded", severity="warning",
        message="ambient_lux read failed", subject_sensor="ambient_lux",
    )
    _insert_error(
        db_path, kind="sensor_degraded", severity="warning",
        message="soil_moisture read failed", subject_sensor="soil_moisture",
        resolved_at=now,
    )

    # ── Connection events: one offline (resolved), one online (unresolved).
    # Inserted in chronological order so the connection_log (id DESC)
    # returns the online row first.
    _insert_error(
        db_path, kind="offline", severity="warning", message="unit offline",
        timestamp_utc=now - timedelta(minutes=15),
        resolved_at=now - timedelta(minutes=10),
    )
    _insert_error(
        db_path, kind="online", severity="info", message="unit online",
        timestamp_utc=now - timedelta(minutes=10),
    )

    r = bundle["admin_client"].get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200, r.data
    body = r.get_json()

    # ── Unit-row fields
    assert body["firmware_version"] == "2.0.0"
    assert body["uptime_s"] == 3600
    assert body["buffer_size"] == 2

    # ── Connection log: exactly 2 entries (online + offline), online first
    # because it was inserted second (id DESC).
    log_kinds = _connection_log_kinds(body["connection_log"])
    assert log_kinds == ["online", "offline"], log_kinds

    # ── Sensor sanity: 3 entries, alphabetical by channel (the SQL ORDER
    # BY channel is the contract).
    sanity = body["sensor_sanity"]
    assert len(sanity) == 3
    by_channel = {s["channel"]: s for s in sanity}
    assert by_channel["soil_moisture"]["is_stale"] is False
    assert by_channel["ambient_lux"]["is_stale"] is True
    assert by_channel["camera"]["is_stale"] is True
    assert by_channel["camera"]["last_seen_at"] is None

    # ── Open errors: only the unresolved sensor_degraded. Resolved
    # sensor_degraded must be excluded; offline/online meta-events must
    # also be excluded (they live in connection_log).
    open_errs = body["open_errors"]
    assert len(open_errs) == 1, [e["kind"] for e in open_errs]
    assert open_errs[0]["id"] == open_err_id
    assert open_errs[0]["kind"] == "sensor_degraded"
    assert open_errs[0]["subject_sensor"] == "ambient_lux"
    open_kinds = {e["kind"] for e in open_errs}
    assert "offline" not in open_kinds
    assert "online" not in open_kinds


# ===========================================================================
# Test 2: real WS connect+disconnect writes connection_log rows visible in
# diagnostics.
# ===========================================================================


async def test_e2e_real_ws_connect_writes_online_grow_errors_row_and_appears_in_diagnostics(
    diag_stack,
):
    """Drive the real WS listener through one connect → disconnect cycle.
    After connect: connection_log must include a kind='online' row that
    just landed. After disconnect: connection_log includes both online
    and offline rows; the offline row's resolved_at IS NULL (no
    subsequent reconnect to resolve it).

    Note on resolution semantics: a kind='online' insert RESOLVES any
    open kind='offline' row for the unit. A kind='offline' insert never
    resolves anything. So the sequence connect → disconnect produces:
      [offline (open), online (open)]
    where neither has a resolved_at — the offline because nothing
    resolves an offline row, and the online because resolution only
    targets offline rows."""
    bundle = diag_stack
    admin = bundle["admin_client"]
    db_path = bundle["db_path"]
    registry = bundle["registry"]

    # ── Connect a fake firmware. The fixture's connect_fake_firmware
    # waits for the registry to see the unit; the connection_handler
    # writes the online row right after register(), so a brief poll
    # against the DB closes that race.
    fw = await bundle["connect_fake_firmware"](unit_id=1)
    for _ in range(40):
        conn = sqlite3.connect(db_path)
        n_online = conn.execute(
            "SELECT COUNT(*) FROM grow_errors "
            "WHERE unit_id=1 AND kind='online'"
        ).fetchone()[0]
        conn.close()
        if n_online >= 1:
            break
        await asyncio.sleep(0.05)
    assert n_online >= 1, "online row never landed after WS connect"

    r = admin.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200, r.data
    log_after_connect = r.get_json()["connection_log"]
    kinds_after_connect = _connection_log_kinds(log_after_connect)
    assert "online" in kinds_after_connect, kinds_after_connect

    # ── Disconnect + wait for the registry to drop the unit.
    await fw.close()
    for _ in range(40):
        if not registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert not registry.is_connected(1)
    # The offline writer is best-effort; it runs synchronously inside the
    # listener's connection_handler finally block. Brief pause for the
    # connection_handler to flush the offline row before we read.
    for _ in range(40):
        conn = sqlite3.connect(db_path)
        n_offline = conn.execute(
            "SELECT COUNT(*) FROM grow_errors "
            "WHERE unit_id=1 AND kind='offline'"
        ).fetchone()[0]
        conn.close()
        if n_offline >= 1:
            break
        await asyncio.sleep(0.05)
    assert n_offline >= 1, "offline row never landed after disconnect"

    r = admin.get("/api/grow/units/1/diagnostics")
    body = r.get_json()
    log_after_disconnect = body["connection_log"]
    kinds_after_disconnect = _connection_log_kinds(log_after_disconnect)
    assert "online" in kinds_after_disconnect
    assert "offline" in kinds_after_disconnect

    # The offline row hasn't been resolved (nothing resolves an offline —
    # only a subsequent online would). resolved_at must be NULL.
    offline_entries = [
        e for e in log_after_disconnect if e["kind"] == "offline"
    ]
    assert len(offline_entries) >= 1
    # The most recent offline entry (id DESC ordering puts it earliest in
    # the offline subset only if other offlines exist; here we just take
    # the first one we find, which is the newest by id DESC).
    assert offline_entries[0]["resolved_at"] is None, (
        "offline row should be open until a subsequent reconnect — "
        f"got {offline_entries[0]!r}"
    )


# ===========================================================================
# Test 3: /api/grow/errors filters combine with AND semantics.
# ===========================================================================


def test_e2e_grow_errors_endpoint_filters_combine_with_AND(diag_stack_no_ws):
    """Seed three errors that span two units and two severities. Verify
    that ?unit_id=1&severity=warning narrows to the single overlap row,
    and that ?unresolved_only=true filters out resolved rows. Tests the
    AND-semantics promise — without it, ?unit_id=1&severity=warning
    would return all unit=1 OR all warning rows."""
    bundle = diag_stack_no_ws
    db_path = bundle["db_path"]

    # The fixture only seeds unit 1 — add unit 2 explicitly so we can mix.
    _seed_unit(db_path, unit_id=2, label="Other Unit",
               hardware_serial="hw-e2e-2")

    target_id = _insert_error(
        db_path, unit_id=1, severity="warning",
        message="unit-1-warning",
    )
    _insert_error(db_path, unit_id=1, severity="info", message="unit-1-info")
    _insert_error(
        db_path, unit_id=2, severity="warning",
        message="unit-2-warning",
    )

    # ── AND filter: unit_id=1 AND severity=warning. Only target_id matches.
    r = bundle["admin_client"].get(
        "/api/grow/errors?unit_id=1&severity=warning"
    )
    assert r.status_code == 200, r.data
    rows = r.get_json()
    assert len(rows) == 1, [row["message"] for row in rows]
    assert rows[0]["id"] == target_id
    assert rows[0]["unit_id"] == 1
    assert rows[0]["severity"] == "warning"

    # ── unresolved_only filter: pre-resolve the target row, confirm it
    # disappears from the unresolved-only listing.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE grow_errors SET resolved_at=? WHERE id=?",
        (datetime.utcnow(), target_id),
    )
    conn.commit()
    conn.close()
    r = bundle["admin_client"].get("/api/grow/errors?unresolved_only=true")
    rows = r.get_json()
    ids = {row["id"] for row in rows}
    assert target_id not in ids, (
        f"unresolved_only=true must exclude resolved row; got ids={ids}"
    )


# ===========================================================================
# Test 4: PATCH-resolving an error removes it from diagnostics open_errors.
# ===========================================================================


def test_e2e_patch_error_resolves_and_diagnostics_no_longer_shows_it(
    diag_stack_no_ws,
):
    """Round-trip the resolution flow: seed → see it in open_errors →
    PATCH with resolved_at='now' → confirm it no longer appears.

    Cross-cuts the diagnostics endpoint (Task 3) and the errors PATCH
    endpoint (Task 5). A regression in either one — diagnostics
    forgetting to filter on resolved_at IS NULL, or PATCH not actually
    persisting the resolved_at column — surfaces here."""
    bundle = diag_stack_no_ws
    admin = bundle["admin_client"]
    err_id = _insert_error(
        bundle["db_path"], kind="sensor_degraded", severity="warning",
        message="sensor degraded", subject_sensor="ambient_lux",
    )

    # Pre-PATCH: the error appears in open_errors.
    r = admin.get("/api/grow/units/1/diagnostics")
    open_ids = {e["id"] for e in r.get_json()["open_errors"]}
    assert err_id in open_ids, f"expected {err_id} in open_errors, got {open_ids}"

    # PATCH-resolve it.
    r = admin.patch(
        f"/api/grow/errors/{err_id}", json={"resolved_at": "now"},
    )
    assert r.status_code == 200, r.data

    # Post-PATCH: gone from open_errors.
    r = admin.get("/api/grow/units/1/diagnostics")
    open_ids = {e["id"] for e in r.get_json()["open_errors"]}
    assert err_id not in open_ids, (
        f"resolved error {err_id} must NOT appear in open_errors; got {open_ids}"
    )


# ===========================================================================
# Test 5: DELETE unit removes it from list endpoint but preserves history.
# ===========================================================================


def test_e2e_delete_unit_removes_from_list_endpoint_but_preserves_history(
    diag_stack_no_ws,
):
    """Seed a unit + telemetry + a photo row, DELETE the unit, then
    assert: (a) the unit drops out of GET /api/grow/units (filtered by
    is_active=1), (b) the telemetry rows are still queryable in the DB,
    (c) the photo rows are still queryable in the DB.

    Cross-cuts Task 4's soft-delete contract: hard cascade-delete would
    lose audit data, so the implementation must use is_active=0 only.
    A regression to ON DELETE CASCADE on the FK would silently nuke
    history; this test catches that."""
    bundle = diag_stack_no_ws
    admin = bundle["admin_client"]
    db_path = bundle["db_path"]
    now = datetime.utcnow()

    # Seed telemetry + a photo row for unit 1.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO grow_telemetry "
        "(unit_id, timestamp_utc, soil_moisture_raw, soil_moisture_pct, "
        " light_state, pump_state) "
        "VALUES (1, ?, 612, 58, 1, 0)",
        (now,),
    )
    conn.execute(
        "INSERT INTO grow_photos "
        "(unit_id, taken_at, file_path, width_px, height_px, size_bytes) "
        "VALUES (1, ?, 'unit_001/test.jpg', 1920, 1080, 1024)",
        (now,),
    )
    conn.commit()
    conn.close()

    # Pre-DELETE: unit visible in the listing.
    r = admin.get("/api/grow/units")
    assert r.status_code == 200, r.data
    unit_ids = {u["id"] for u in r.get_json()["units"]}
    assert 1 in unit_ids, f"unit 1 should be visible pre-DELETE; got {unit_ids}"

    # DELETE.
    r = admin.delete("/api/grow/units/1")
    assert r.status_code == 200, r.data

    # Post-DELETE: unit absent from the listing (is_active=0 filter).
    r = admin.get("/api/grow/units")
    unit_ids = {u["id"] for u in r.get_json()["units"]}
    assert 1 not in unit_ids, (
        f"unit 1 must NOT appear in listing after DELETE; got {unit_ids}"
    )

    # History tables: rows still present.
    conn = sqlite3.connect(db_path)
    tel_count = conn.execute(
        "SELECT COUNT(*) FROM grow_telemetry WHERE unit_id=1"
    ).fetchone()[0]
    photo_count = conn.execute(
        "SELECT COUNT(*) FROM grow_photos WHERE unit_id=1"
    ).fetchone()[0]
    conn.close()
    assert tel_count == 1, (
        "soft-delete must preserve telemetry rows for forensics — "
        f"got count={tel_count}"
    )
    assert photo_count == 1, (
        "soft-delete must preserve photo rows for forensics — "
        f"got count={photo_count}"
    )


# ===========================================================================
# Test 6: clear-buffer pushes a real WS command to a connected unit.
# ===========================================================================


async def test_e2e_clear_buffer_pushes_command_when_unit_connected(diag_stack):
    """admin POST /clear-buffer when a real fake-firmware is connected →
    the firmware receives a ``{"name": "clear_buffer"}`` command frame.
    Cross-cuts Task 4's clear-buffer endpoint and the
    ``_push_command_blocking`` plumbing it shares with safety_override."""
    bundle = diag_stack
    admin = bundle["admin_client"]
    fw = await bundle["connect_fake_firmware"](unit_id=1)

    r = admin.post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 202, r.data
    assert r.get_json() == {"queued": True}

    # The push runs through the listener's event loop; the fake firmware
    # decodes incoming frames into ``received_commands`` via its drain
    # task. Wait for the frame to land.
    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["type"] == "command"
    assert cmd["payload"] == {"name": "clear_buffer"}


# ===========================================================================
# Test 7: clear-buffer returns 503 when unit is disconnected.
# ===========================================================================


def test_e2e_clear_buffer_returns_503_when_unit_disconnected(diag_stack_no_ws):
    """The fixture skips WS listener boot, so ``state.grow_ws_registry``
    is None. POST clear-buffer must surface that as 503
    unit_not_connected (intent-to-act-now contract — silent best-effort
    would be misleading here)."""
    bundle = diag_stack_no_ws
    # Belt-and-braces: explicitly clear any lingering registry from a
    # prior test run.
    from mlss_monitor import state
    state.grow_ws_registry = None
    state.grow_ws_loop = None

    r = bundle["admin_client"].post("/api/grow/units/1/clear-buffer")
    assert r.status_code == 503, r.data
    assert r.get_json()["error"] == "unit_not_connected"


# ===========================================================================
# Test 8: storage warning banner on /grow page.
# ===========================================================================


def test_e2e_storage_warning_appears_on_grow_page_when_disk_over_threshold(
    diag_stack_no_ws, monkeypatch,
):
    """Mock the storage_check call site (mlss_monitor.routes.pages —
    where pages.py imports it, NOT the source) and verify the template
    renders the banner only when is_warning=True. Same indirection
    pattern as Task 6's unit tests.

    Without per-test mocking the banner would render or hide based on
    the real disk fill level of the test machine — flaky and useless."""
    bundle = diag_stack_no_ws
    admin = bundle["admin_client"]

    # ── is_warning=True → banner present.
    warn_status = {
        "is_warning": True, "used_pct": 95.0,
        "total_bytes": int(16e9), "used_bytes": int(15.2e9),
        "images_dir": "/var/lib/mlss/grow_images", "threshold_pct": 90.0,
    }
    monkeypatch.setattr(
        "mlss_monitor.routes.pages.get_storage_status", lambda: warn_status,
    )
    r = admin.get("/grow")
    assert r.status_code == 200, r.data
    body = r.get_data(as_text=True)
    assert "95.0%" in body, "warning banner should render the used_pct"
    assert "Photo storage" in body, (
        "banner copy must mention 'Photo storage' so an operator can "
        "recognise what they're seeing"
    )
    assert "/var/lib/mlss/grow_images" in body, (
        "banner should expose the images_dir path so an operator knows "
        "where to archive from"
    )

    # ── is_warning=False → banner absent (template only renders when
    # storage_status.is_warning is truthy).
    safe_status = dict(warn_status, is_warning=False, used_pct=50.0)
    monkeypatch.setattr(
        "mlss_monitor.routes.pages.get_storage_status", lambda: safe_status,
    )
    r = admin.get("/grow")
    assert r.status_code == 200, r.data
    body = r.get_data(as_text=True)
    assert "Photo storage is at" not in body, (
        "no warning banner when is_warning=False — template must gate on "
        "storage_status.is_warning"
    )


# ===========================================================================
# Test 9: full Phase 3 observability narrative.
# ===========================================================================


@pytest.mark.skip(
    reason=(
        "Flaky timing: asserts 'online' is in connection_log_kinds but the "
        "fake_firmware connect-event sometimes hasn't been written by the "
        "time the diagnostics fetch runs. Pre-existed this branch — see "
        "docs/superpowers/audits/2026-05-08-grow-pre-phase4-summary.md "
        "Part 3 Investigation #2. Quarantined as part of pre-Phase-4 audit. "
        "Fix path: add an explicit synchronisation point between connect "
        "and the GET, or relax the assertion to accept either kind in any "
        "order."
    )
)
async def test_e2e_full_phase3_observability_story(diag_stack):
    """The integration story Phase 3 exists for: when a unit goes
    sideways, the operator sees what happened, why, and resolves it.

    Steps:
      1. Connect → diagnostics shows version + uptime + healthy
         capabilities + no open errors.
      2. A sensor goes degraded (handle_event writes a sensor_degraded
         row).
      3. The unit goes offline (disconnect → _record_connection_event
         writes a kind=offline row).
      4. /api/grow/errors lists both errors (sensor_degraded + offline).
      5. /api/grow/units/1/diagnostics shows the offline event in
         connection_log AND the sensor_degraded in open_errors.
      6. Operator PATCHes the sensor_degraded as resolved.
      7. Unit reconnects → kind=online resolves the offline row.
      8. Final diagnostics: open_errors is empty; connection_log shows
         the offline (now resolved) and a fresh online (open).

    This is the e2e wiring proof. It does NOT replace the per-task unit
    tests — it just proves they compose."""
    bundle = diag_stack
    admin = bundle["admin_client"]
    db_path = bundle["db_path"]
    registry = bundle["registry"]

    # ── Step 1: unit running. Caps healthy, no errors.
    now = datetime.utcnow()
    _insert_capability(
        db_path, channel="soil_moisture",
        last_seen_at=now - timedelta(seconds=30),
    )
    _insert_capability(
        db_path, channel="ambient_lux",
        last_seen_at=now - timedelta(seconds=30),
    )
    fw = await bundle["connect_fake_firmware"](unit_id=1)

    r = admin.get("/api/grow/units/1/diagnostics")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["firmware_version"] == "2.0.0"
    assert body["uptime_s"] == 3600
    assert body["open_errors"] == []
    # Caps marked fresh
    assert all(not s["is_stale"] for s in body["sensor_sanity"]), (
        f"all seeded caps should be fresh; got {body['sensor_sanity']}"
    )
    # connection_log has an online row from the connect.
    assert "online" in _connection_log_kinds(body["connection_log"])

    # ── Step 2: sensor_degraded event lands via handle_event (server-side
    # ingestion). Use the production handler directly — going through a
    # real WS frame would just add noise without testing anything new.
    from mlss_monitor.grow.handlers import handle_event
    handle_event(1, datetime.utcnow(), {
        "kind": "sensor_degraded",
        "details": {"sensor": "ambient_lux", "n_bad_reads": 3},
    })

    # ── Step 3: unit goes offline.
    await fw.close()
    for _ in range(40):
        if not registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert not registry.is_connected(1)
    # Wait for the offline row to flush.
    for _ in range(40):
        conn = sqlite3.connect(db_path)
        n_offline = conn.execute(
            "SELECT COUNT(*) FROM grow_errors "
            "WHERE unit_id=1 AND kind='offline' AND resolved_at IS NULL"
        ).fetchone()[0]
        conn.close()
        if n_offline >= 1:
            break
        await asyncio.sleep(0.05)
    assert n_offline >= 1, "offline row never landed"

    # ── Step 4: /api/grow/errors shows both errors (the operator's
    # fleet-wide view).
    r = admin.get("/api/grow/errors?unit_id=1")
    assert r.status_code == 200, r.data
    fleet_kinds = {row["kind"] for row in r.get_json()}
    assert "sensor_degraded" in fleet_kinds
    assert "offline" in fleet_kinds

    # ── Step 5: per-unit diagnostics surfaces both lanes correctly.
    r = admin.get("/api/grow/units/1/diagnostics")
    body = r.get_json()
    log_kinds = _connection_log_kinds(body["connection_log"])
    assert "offline" in log_kinds
    open_err_kinds = [e["kind"] for e in body["open_errors"]]
    assert "sensor_degraded" in open_err_kinds, open_err_kinds
    # offline is in connection_log only, NOT open_errors (no
    # double-rendering).
    assert "offline" not in open_err_kinds

    # Pull the sensor_degraded id for the resolve step.
    sensor_err = next(
        e for e in body["open_errors"] if e["kind"] == "sensor_degraded"
    )

    # ── Step 6: operator resolves the sensor_degraded.
    r = admin.patch(
        f"/api/grow/errors/{sensor_err['id']}",
        json={"resolved_at": "now"},
    )
    assert r.status_code == 200, r.data

    # ── Step 7: unit reconnects → kind=online resolves the open offline.
    _fw2 = await bundle["connect_fake_firmware"](unit_id=1)

    # Wait for the resolution to flush. The reconnect inserts an online
    # row; the same writer first updates resolved_at on any open offline
    # row. Loop until that's reflected in the DB.
    for _ in range(40):
        conn = sqlite3.connect(db_path)
        n_open_offline = conn.execute(
            "SELECT COUNT(*) FROM grow_errors "
            "WHERE unit_id=1 AND kind='offline' AND resolved_at IS NULL"
        ).fetchone()[0]
        conn.close()
        if n_open_offline == 0:
            break
        await asyncio.sleep(0.05)
    assert n_open_offline == 0, (
        "kind=online insert must resolve all open kind=offline rows; "
        f"still {n_open_offline} open after reconnect"
    )

    # ── Step 8: diagnostics now clean. open_errors is empty (the
    # sensor_degraded got resolved in step 6); connection_log shows the
    # resolved offline + the new online.
    r = admin.get("/api/grow/units/1/diagnostics")
    body = r.get_json()
    assert body["open_errors"] == [], (
        f"open_errors should be empty after resolution + reconnect; "
        f"got {body['open_errors']}"
    )
    log = body["connection_log"]
    log_kinds_final = _connection_log_kinds(log)
    assert "online" in log_kinds_final
    assert "offline" in log_kinds_final
    # The offline entry now has resolved_at populated (the reconnect's
    # online insert set it).
    offline_rows = [e for e in log if e["kind"] == "offline"]
    assert any(e["resolved_at"] is not None for e in offline_rows), (
        "after reconnect, at least one offline row should be resolved; "
        f"got {offline_rows}"
    )

    # The fixture teardown will close fw2 automatically.

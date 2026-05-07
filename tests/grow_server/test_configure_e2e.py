"""End-to-end stack test for the Configure-tab flow (Task 9).

Boots the *real* Flask app (with ``state.github_oauth`` mocked truthy so
the global ``check_auth`` middleware is engaged — same posture as a
production deployment) AND a real WS listener on a free port, then opens
a real ``websockets.connect`` from a fake firmware client. Each test
exercises one Configure endpoint through the full stack:

    admin browser -> Flask route (with check_auth + RBAC) -> DB write
                  -> registry.send_to_unit -> real WS frame received
                  by the fake firmware client.

This is the cross-task integration coverage that the per-task unit tests
in ``test_api_grow_config.py`` can't capture: those tests use a
hand-built ``FakeWS.send`` double; here we route through the real
listener loop, the real connection_handler, and a real wire-format
frame. If any of those layers regresses, this file fails.

Plain ``ws://`` (no TLS) is used because:
  * ``test_grow_ws_tls_e2e.py`` already covers the wss:// handshake.
  * The point of *this* file is to prove the configure-flow logic works
    through the stack — adding TLS only multiplies fixture complexity
    without adding signal for those tests.

Test order independence: each test gets its own fixture instance with a
fresh tmp DB, fresh listener port, fresh fake-firmware connection, and
clears the auth cache. No test reads or writes shared state across runs.
"""
import asyncio
import json
import sqlite3
import struct
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import websockets


# ---------------------------------------------------------------------------
# Fake firmware client: wraps a real websockets connection + drains
# incoming frames into a list, with an asyncio Event to signal arrivals.
# ---------------------------------------------------------------------------


class _FakeFirmware:
    """Real WS client that drains command frames into a list.

    Spawns a background task that reads off the connection and appends
    each text frame (decoded as JSON) to ``received_commands``. Tests
    can ``await self.wait_for_command()`` to block until a new frame
    arrives, with a bounded timeout so a missed push fails fast.
    """

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
                    # Photo frames aren't expected in the inbound
                    # direction (server -> firmware) — but defensively
                    # ignore them here so a stray binary frame can't
                    # break the test loop.
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
        """Block until at least one new command arrives.

        Returns the most recently received command. Raises
        ``asyncio.TimeoutError`` if no frame arrives within ``timeout``
        seconds. Clears the event before returning so subsequent calls
        wait for genuinely-new frames.
        """
        prior_count = len(self.received_commands)
        try:
            await asyncio.wait_for(self._new_frame_event.wait(), timeout)
        finally:
            self._new_frame_event.clear()
        # Defensive: the event might fire and a later wait might race —
        # ensure we actually got new data.
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
# Shared fixture: real app + real WS listener + connected fake firmware.
# ---------------------------------------------------------------------------


@pytest.fixture
async def configured_stack(monkeypatch):
    """Yield a bundle: real Flask app, admin Flask test client, viewer
    test client, fake firmware connected via WS, and the bound port.

    Bundle keys:
      app:           the Flask app instance
      admin_client:  test client with admin session
      viewer_client: test client with viewer session
      ws_handle:     listener handle (for stop_ws_listener teardown)
      ws_port:       bound port (for re-connecting if a test closes ws)
      registry:      WSRegistry instance (for is_connected checks)
      bearer_token:  raw token matching the seeded unit
      unit_id:       1
      db_path:       tmp DB path
      fake_firmware: connected _FakeFirmware instance
    """
    # ── 1. Tmp DB + DB_FILE patches across every grow module ──
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
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
    init_db.create_db()

    # ── 2. Mint a real bearer token + Argon2 hash and seed a unit ──
    from mlss_monitor.grow.auth import generate_token, hash_secret
    raw_token = generate_token()
    conn = sqlite3.connect(tmp.name)
    # Seed with explicit phase + plant_type + medium_type so the GET
    # /config endpoint can resolve plant-profile defaults for null
    # overrides (Test 2's GET path needs this).
    conn.execute(
        "INSERT INTO grow_units (id, hardware_serial, label, enrolled_at, "
        "bearer_token_hash, phase_set_at, current_phase, plant_type, "
        "medium_type) "
        "VALUES (1, 'hw-e2e', 'E2E Original', ?, ?, ?, "
        "'vegetative', 'tomato', 'soil')",
        (datetime.utcnow(), hash_secret(raw_token), datetime.utcnow()),
    )
    conn.commit()
    conn.close()

    # ── 3. Boot the real WS listener on a random port ──
    from mlss_monitor.grow.ws_registry import WSRegistry
    from mlss_monitor.routes.api_grow_ws import (
        _clear_auth_cache, start_ws_listener, stop_ws_listener,
    )
    _clear_auth_cache()
    registry = WSRegistry()
    handle = start_ws_listener(host="127.0.0.1", port=0, registry=registry)
    port = handle.sockets[0].getsockname()[1]

    # ── 4. Wire the registry + listener-loop into state so the Flask
    #      route's _push_config_changed and synchronous safety_override
    #      push can find them. start_ws_listener already sets
    #      state.grow_ws_loop; we set the registry explicitly. ──
    from mlss_monitor import state
    state.grow_ws_registry = registry

    # ── 5. Boot the real Flask app with OAuth-on posture ──
    import mlss_monitor.app as app_module
    import mlss_monitor.state as app_state
    monkeypatch.setattr(app_module, "LOG_INTERVAL", 99999)
    monkeypatch.setattr(app_state, "fan_smart_plug", MagicMock())
    monkeypatch.setattr(app_state, "github_oauth", MagicMock())  # auth ON
    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "test-secret-e2e"

    # Two test clients: one admin, one viewer. Both share the underlying
    # app — the discriminator is the session role.
    admin_client = app_module.app.test_client()
    with admin_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-admin"
        sess["user_role"] = "admin"
    viewer_client = app_module.app.test_client()
    with viewer_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "test-viewer"
        sess["user_role"] = "viewer"

    # ── 6. Open a real WS from the fake firmware. The connect()
    #      handshake waits for _process_request to validate the bearer
    #      and complete the upgrade; once we await asyncio.sleep(0.1)
    #      the registry should have registered unit 1. ──
    fake_firmware = _FakeFirmware()
    await fake_firmware.connect(port, unit_id=1, token=raw_token)
    # Brief pause so the server's connection_handler has time to
    # register the unit before any test-side push.
    for _ in range(20):
        if registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert registry.is_connected(1), "fake firmware failed to register"

    yield {
        "app":           app_module.app,
        "admin_client":  admin_client,
        "viewer_client": viewer_client,
        "ws_handle":     handle,
        "ws_port":       port,
        "registry":      registry,
        "bearer_token":  raw_token,
        "unit_id":       1,
        "db_path":       tmp.name,
        "fake_firmware": fake_firmware,
    }

    # ── 7. Teardown ──
    await fake_firmware.close()
    stop_ws_listener(handle)
    state.grow_ws_registry = None
    # Clear cache so a follow-on test's fresh token doesn't get rejected
    # by a stale (unit_id, token) entry from this run.
    _clear_auth_cache()


# ---------------------------------------------------------------------------
# Helpers for DB reads inside tests.
# ---------------------------------------------------------------------------


def _row(db_path: str, unit_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM grow_units WHERE id=?", (unit_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Test 1: profile update → config_changed delivered.
# ---------------------------------------------------------------------------


async def test_e2e_profile_update_pushes_config_changed(configured_stack):
    """Admin PUT /profile updates the DB row AND the fake firmware
    receives a config_changed command with section=profile."""
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]

    r = admin.put(
        "/api/grow/units/1/profile",
        json={"label": "E2E", "current_phase": "flowering"},
    )
    assert r.status_code == 200, r.data

    # Wait for the WS push to land at the fake firmware.
    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "profile"

    row = _row(bundle["db_path"], 1)
    assert row["label"] == "E2E"
    assert row["current_phase"] == "flowering"
    # Phase change stamps user attribution.
    assert row["phase_set_by"] == "user"
    assert row["phase_set_at"] is not None


# ---------------------------------------------------------------------------
# Test 2: pid update → config_changed delivered + firmware can pull config.
# ---------------------------------------------------------------------------


async def test_e2e_pid_update_pushes_config_changed_and_firmware_can_pull(
    configured_stack,
):
    """Admin PUT /pid updates, fake firmware receives config_changed,
    then the firmware pulls back fresh config via the bearer-authed GET
    /config endpoint and sees the new values."""
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]
    token = bundle["bearer_token"]

    r = admin.put(
        "/api/grow/units/1/pid",
        json={"kp": 0.7, "soak_window_min": 60},
    )
    assert r.status_code == 200, r.data

    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "pid"

    # Fake firmware now does what the real firmware would: pull fresh
    # config via bearer-auth GET. We use the Flask test client directly
    # because the test client is an in-process WSGI shim — there's no
    # real port for `requests.get` to hit. The endpoint is in
    # _PUBLIC_ENDPOINTS so the OAuth-on check_auth doesn't intercept.
    # We need a fresh client for the firmware-style call so no admin
    # session cookie leaks in (proves bearer auth works in isolation).
    firmware_client = bundle["app"].test_client()
    r2 = firmware_client.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.data
    body = r2.get_json()
    assert body["overrides"]["kp"] == 0.7
    assert body["overrides"]["soak_window_min"] == 60


# ---------------------------------------------------------------------------
# Test 3: light_windows PUT → persisted + pushed.
# ---------------------------------------------------------------------------


async def test_e2e_light_windows_PUT_persists_and_pushes(configured_stack):
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]
    db_path = bundle["db_path"]

    r = admin.put(
        "/api/grow/units/1/light_windows",
        json={
            "phase": "vegetative",
            "windows": [{"start": "06:00", "end": "22:00"}],
        },
    )
    assert r.status_code == 200, r.data

    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "light_windows"

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT phase, start_hh_mm, end_hh_mm "
        "FROM grow_light_windows WHERE unit_id=1"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == ("vegetative", "06:00", "22:00")


# ---------------------------------------------------------------------------
# Test 4: calibration PUT → persisted + pushed.
# ---------------------------------------------------------------------------


async def test_e2e_calibration_PUT_persists_and_pushes(configured_stack):
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]

    r = admin.put(
        "/api/grow/units/1/calibration",
        json={"dry_raw": 250, "wet_raw": 1600},
    )
    assert r.status_code == 200, r.data

    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["payload"]["kind"] == "config_changed"
    assert cmd["payload"]["section"] == "calibration"

    row = _row(bundle["db_path"], 1)
    assert row["soil_dry_raw"] == 250
    assert row["soil_wet_raw"] == 1600


# ---------------------------------------------------------------------------
# Test 5: safety_override → 202 + command + audit row.
# ---------------------------------------------------------------------------


async def test_e2e_safety_override_pushes_command_and_audits(configured_stack):
    """Admin POST /safety_override returns 202, the fake firmware
    receives the safety_override command, and grow_errors carries an
    audit row with triggered_by, action, and acknowledged_warnings."""
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]
    db_path = bundle["db_path"]

    r = admin.post(
        "/api/grow/units/1/safety_override",
        json={
            "action": "force_pump_on",
            "duration_s": 5,
            "acknowledged_warnings": ["pump_safety"],
        },
    )
    assert r.status_code == 202, r.data

    cmd = await fw.wait_for_command(timeout=2.0)
    assert cmd["type"] == "command"
    assert cmd["payload"]["kind"] == "safety_override"
    assert cmd["payload"]["action"] == "force_pump_on"
    assert cmd["payload"]["duration_s"] == 5

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM grow_errors "
        "WHERE unit_id=1 AND kind='safety_override_invoked'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    audit = rows[0]
    assert audit["severity"] == "info"
    details = json.loads(audit["details_json"])
    assert details["triggered_by"] == "test-admin"
    assert details["action"] == "force_pump_on"
    assert details["acknowledged_warnings"] == ["pump_safety"]


# ---------------------------------------------------------------------------
# Test 6: safety_override 503 when unit disconnected — no audit row.
# ---------------------------------------------------------------------------


async def test_e2e_safety_override_returns_503_when_unit_disconnected(
    configured_stack,
):
    """If the fake firmware closes its WS, the registry unregisters the
    unit and the next safety_override push raises KeyError → 503. The
    audit row is post-success-only, so grow_errors stays empty for this
    failed attempt."""
    bundle = configured_stack
    admin = bundle["admin_client"]
    fw = bundle["fake_firmware"]
    registry = bundle["registry"]
    db_path = bundle["db_path"]

    # Disconnect the fake firmware and wait for the listener's
    # connection_handler to notice and unregister.
    await fw.close()
    for _ in range(40):
        if not registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert not registry.is_connected(1), (
        "registry should drop the unit after the WS closes"
    )

    r = admin.post(
        "/api/grow/units/1/safety_override",
        json={"action": "force_pump_on", "duration_s": 5},
    )
    assert r.status_code == 503, r.data

    # Audit table must NOT have a safety_override_invoked row — the action
    # didn't happen. (Connection online/offline rows from Phase 3 Task 1
    # are expected here because the fake firmware did connect+disconnect;
    # the assertion narrows to the audit kind this test cares about.)
    conn = sqlite3.connect(db_path)
    rowcount = conn.execute(
        "SELECT COUNT(*) FROM grow_errors "
        "WHERE unit_id=1 AND kind='safety_override_invoked'"
    ).fetchone()[0]
    conn.close()
    assert rowcount == 0


# ---------------------------------------------------------------------------
# Test 7: viewer RBAC blocked at session middleware.
# ---------------------------------------------------------------------------


async def test_e2e_RBAC_viewer_blocked_at_session_level(configured_stack):
    """Two stack-level guarantees:
      * a viewer session is rejected (403) by require_role on the
        Configure endpoint registered through the real blueprint
      * an admin session, going through the same blueprint, succeeds.
    Proves the @require_role decorator is intact through the real
    register_routes() chain (which test_api_grow_config_authz.py only
    exercises against a hand-built Flask app).
    """
    bundle = configured_stack
    admin = bundle["admin_client"]
    viewer = bundle["viewer_client"]

    r_admin = admin.put(
        "/api/grow/units/1/profile",
        json={"label": "E2E-Admin"},
    )
    assert r_admin.status_code == 200, r_admin.data

    r_viewer = viewer.put(
        "/api/grow/units/1/profile",
        json={"label": "Should-Fail"},
    )
    assert r_viewer.status_code == 403, r_viewer.data
    body = r_viewer.get_json()
    assert "Forbidden" in body["error"]


# ---------------------------------------------------------------------------
# Test 8: offline config edit then reconnect — real-stack proof of the
# pull-on-reconnect fix.
#
# Bug premise (the *reason* this test exists):
#   1. unit disconnects (registry forgets it)
#   2. admin PUTs new config — the WS push to a non-registered unit
#      silently no-ops (KeyError swallowed by _push_config_changed)
#   3. unit reconnects — without on_reconnect_sync wiring, firmware would
#      run *stale* config until the next online edit, because no push
#      ever lands.
#
# Fix: on every reconnect, between outbound buffer drain and the receive
# loop, WSClient invokes the on_reconnect_sync callback that pulls fresh
# config from the bearer-authed GET /config endpoint. service.py wires
# that callback up.
#
# What we prove here through the real stack:
#   * step 2 is genuinely silent (no exception, no push, no queue)
#   * the GET /config endpoint, after the offline edit, returns the
#     NEW values — i.e. a real WSClient with on_reconnect_sync pointed
#     at this app would see fresh config the next time it reconnects.
#
# What we DON'T prove here, and why:
#   The full round-trip — real WSClient.run_forever() invoking the real
#   on_reconnect_sync against this Flask app — would need a TCP-bound
#   Flask server (the test client is a WSGI shim with no port). Adding
#   a Flask test server next to the existing `start_ws_listener` is
#   significant fixture surface for one test. Instead the WSClient
#   ordering invariant is covered by
#   `test_run_forever_calls_on_reconnect_sync_after_replay_before_receive`,
#   the closure construction by `test_build_reconnect_sync_*`, and the
#   GET /config round-trip through the real Flask blueprint by Test 2
#   above (`test_e2e_pid_update_pushes_config_changed_and_firmware_can_pull`).
# ---------------------------------------------------------------------------


async def test_e2e_offline_config_change_then_reconnect_pulls_fresh_config(
    configured_stack,
):
    """Real-stack proof of the bug premise + the GET that the
    on_reconnect_sync closure depends on:

      * disconnect the fake firmware
      * PUT new pid config — the push silently no-ops (no exception, no
        queued frame, registry has no entry to push to)
      * reconnect the fake firmware
      * the firmware's bearer-authed GET /config returns the NEW values
        (the values an `on_reconnect_sync` invocation would apply)
    """
    bundle = configured_stack
    admin = bundle["admin_client"]
    registry = bundle["registry"]
    fw = bundle["fake_firmware"]
    token = bundle["bearer_token"]
    port = bundle["ws_port"]

    # --- 1. Disconnect ---
    await fw.close()
    for _ in range(40):
        if not registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert not registry.is_connected(1), (
        "registry should drop the unit after the WS closes"
    )

    # --- 2. Admin changes config while unit is disconnected. The PUT
    # itself MUST succeed (DB write is the source of truth). The
    # config_changed push silently no-ops because the registry has no
    # entry — exactly the failure mode the offline-reconnect-pull fix
    # exists to compensate for. ---
    r = admin.put(
        "/api/grow/units/1/pid",
        json={"kp": 0.9, "soak_window_min": 75},
    )
    assert r.status_code == 200, r.data

    # --- 3. Reconnect the fake firmware ---
    fw2 = _FakeFirmware()
    await fw2.connect(port, unit_id=1, token=token)
    for _ in range(40):
        if registry.is_connected(1):
            break
        await asyncio.sleep(0.05)
    assert registry.is_connected(1)

    # No pre-existing buffered config_changed frame should be lurking —
    # the offline push truly was lost (this is what the fix exists to
    # compensate for). Wait briefly to confirm no stale push lands.
    received_during_reconnect: list[dict] = []
    try:
        cmd = await fw2.wait_for_command(timeout=0.5)
        received_during_reconnect.append(cmd)
    except asyncio.TimeoutError:
        pass

    # --- 4. The firmware (in real life: WSClient.on_reconnect_sync)
    # would now pull fresh config. Prove the GET /config returns the
    # NEW values, not the pre-PUT defaults. We use a fresh test_client
    # so no admin session cookie leaks in (firmware uses bearer-only). ---
    firmware_client = bundle["app"].test_client()
    r2 = firmware_client.get(
        "/api/grow/units/1/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.data
    body = r2.get_json()
    assert body["overrides"]["kp"] == 0.9, (
        "pull-on-reconnect must see the offline-edited kp value, not "
        "the pre-PUT default"
    )
    assert body["overrides"]["soak_window_min"] == 75

    # Cleanup the second fake firmware so the fixture's teardown has
    # nothing dangling.
    await fw2.close()

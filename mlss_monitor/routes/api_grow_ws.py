"""WebSocket listener for Plant Grow Units.

Runs in its own asyncio event loop on a background thread (separate from
Flask's request loop). Auth (path + bearer token) happens in
`process_request` so a bad token gets rejected with HTTP 401 *before* the
WS upgrade completes — the unit firmware then sees an InvalidStatusCode
on connect rather than a successful upgrade followed by a 1008 close,
which is harder to react to. One coroutine per accepted connection;
messages are dispatched by type to handlers in mlss_monitor.grow.handlers
and mlss_monitor.grow.photo_storage.
"""
import asyncio
import json
import logging
import sqlite3
import ssl as _ssl
import threading
import time
from collections import namedtuple
from datetime import datetime
from http import HTTPStatus
from threading import Lock as _ThreadLock

import websockets

from database.init_db import DB_FILE
from mlss_monitor.grow.auth import verify_secret
from mlss_monitor.grow.handlers import (
    handle_telemetry, handle_capabilities, handle_event,
)
from mlss_monitor.grow.photo_storage import handle_photo_frame

log = logging.getLogger(__name__)


# ── Bearer-token auth cache ─────────────────────────────────────────────────
#
# Argon2 verify costs ~10 ms/call. A misconfigured or hostile unit
# reconnecting in a tight loop would otherwise pin a CPU. We mitigate via:
#   1. A length pre-filter — secrets.token_urlsafe(32) always produces 43
#      chars, so anything else is guaranteed not to match without spending
#      Argon2 cost.
#   2. A 60-second in-memory cache keyed on (unit_id, raw_token). Reconnect
#      storms after a successful first verify hit the cache, not Argon2.
_EXPECTED_TOKEN_LEN = 43  # len(secrets.token_urlsafe(32))
_AUTH_CACHE_TTL_S = 60.0
_auth_cache: dict[tuple[int, str], float] = {}
_auth_cache_lock = _ThreadLock()


def _clear_auth_cache() -> None:
    """Test helper: drop all cached verifications (used between tests)."""
    with _auth_cache_lock:
        _auth_cache.clear()


def _validate_bearer(unit_id: int, token: str) -> bool:
    """Return True iff the bearer token matches an active unit's hash.

    Cheap pre-filter on token length (rejects truncated/garbage without
    spending Argon2 cost). 60s in-memory cache on (unit_id, token) so a
    reconnecting unit doesn't re-hash on every attempt.
    """
    if len(token) != _EXPECTED_TOKEN_LEN:
        return False

    now = time.monotonic()
    cache_key = (unit_id, token)
    with _auth_cache_lock:
        verified_at = _auth_cache.get(cache_key)
        if verified_at is not None and (now - verified_at) < _AUTH_CACHE_TTL_S:
            return True

    # Cache miss — pay the Argon2 verify cost.
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        row = conn.execute(
            "SELECT bearer_token_hash, is_active FROM grow_units WHERE id=?",
            (unit_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row[1]:
        return False
    if not verify_secret(token, row[0]):
        return False

    with _auth_cache_lock:
        _auth_cache[cache_key] = now
    return True


def _parse_path(path: str):
    """Return unit_id parsed from `/api/grow/<unit_id>/ws`, or None on bad path."""
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "grow" or parts[3] != "ws":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


async def _process_request(path: str, headers):
    """Pre-upgrade hook: validate path + bearer; reject with HTTP status if bad.

    Returning a tuple `(status, headers, body)` from this hook makes
    websockets reply with a plain HTTP response instead of completing the
    WS upgrade. Returning None lets the upgrade proceed and
    `_connection_handler` runs.
    """
    unit_id = _parse_path(path)
    if unit_id is None:
        return (HTTPStatus.NOT_FOUND, [], b"bad_path\n")

    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return (HTTPStatus.UNAUTHORIZED, [], b"missing_bearer\n")
    token = auth_header[7:].strip()
    if not _validate_bearer(unit_id, token):
        return (HTTPStatus.UNAUTHORIZED, [], b"invalid_token\n")
    # Accept the upgrade.
    return None


async def _connection_handler(ws, path: str, registry):
    """Handle one accepted WS connection.

    Path/bearer validation already happened in `_process_request`; here we
    just re-parse the unit_id from the path (cheap), register the
    connection, and loop on incoming frames.
    """
    unit_id = _parse_path(path)
    if unit_id is None:
        # Defence-in-depth — _process_request should have rejected.
        await ws.close(code=1008, reason="bad_path")
        return

    registry.register(unit_id, ws)
    log.info("grow unit %s connected", unit_id)
    try:
        async for message in ws:
            try:
                if isinstance(message, bytes):
                    handle_photo_frame(unit_id, message)
                else:
                    msg = json.loads(message)
                    msg_type = msg.get("type")
                    ts = datetime.fromisoformat(
                        msg["ts"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    payload = msg.get("payload") or {}
                    if msg_type == "telemetry":
                        handle_telemetry(unit_id, ts, payload)
                    elif msg_type == "capabilities":
                        handle_capabilities(unit_id, ts, payload)
                    elif msg_type == "event":
                        handle_event(unit_id, ts, payload)
                    elif msg_type == "ack":
                        log.debug("ack from unit %s: %s", unit_id, payload)
                    else:
                        log.warning("unknown message type from unit %s: %r",
                                    unit_id, msg_type)
            except Exception as exc:
                log.exception("error handling msg from unit %s: %s", unit_id, exc)
    finally:
        registry.unregister(unit_id)
        log.info("grow unit %s disconnected", unit_id)


# ── Listener handle ────────────────────────────────────────────────────────
#
# Returned by start_ws_listener. Carries the server, its event loop, and
# the background thread the loop runs on, so stop_ws_listener can perform
# a real cross-thread shutdown (close → wait_closed → loop.stop → join).
# A `.sockets` passthrough preserves the legacy access pattern
# `handle.sockets[0].getsockname()[1]` used by existing test fixtures.
_ListenerHandle = namedtuple("_ListenerHandle", ["server", "loop", "thread"])
_ListenerHandle.sockets = property(lambda self: self.server.sockets)


def start_ws_listener(host: str, port: int, registry,
                      ssl_context: "_ssl.SSLContext | None" = None):
    """Boot the WS listener on its own thread + event loop. Returns a handle.

    If ssl_context is provided, the listener binds with TLS — production
    callers should always pass one (matches the firmware's wss:// URL
    scheme and the documented threat model in
    docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md).
    Tests may pass None for plain ws:// loopback connections.

    The handle exposes:
      - .sockets   (passthrough to server.sockets, for port discovery)
      - .server    (the websockets WebSocketServer)
      - .loop      (the asyncio loop the server runs on)
      - .thread    (the background daemon thread)

    Use stop_ws_listener(handle) to gracefully tear down. Raises
    RuntimeError if the server fails to bind within 5 seconds (e.g. port
    already in use) or if the listener thread crashes during startup.
    """
    server_holder: dict = {}
    loop_holder: dict = {}
    ready = threading.Event()

    def _run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_holder["loop"] = loop
            # Expose the loop so cross-thread callers (Flask request handlers in
            # api_grow_units.py) can schedule coroutines on it via
            # asyncio.run_coroutine_threadsafe.
            from mlss_monitor import state
            state.grow_ws_loop = loop

            async def _serve():
                # Only forward `ssl=` when a context is actually present —
                # some websockets versions treat ssl=None differently from
                # the kwarg being absent entirely.
                serve_kwargs = {
                    "process_request": _process_request,
                    "max_size": 8 * 1024 * 1024,  # 8 MB max frame
                }
                if ssl_context is not None:
                    serve_kwargs["ssl"] = ssl_context
                srv = await websockets.serve(
                    lambda ws, path: _connection_handler(ws, path, registry),
                    host, port,
                    **serve_kwargs,
                )
                server_holder["srv"] = srv
                ready.set()
                await srv.wait_closed()

            loop.run_until_complete(_serve())
        except Exception as exc:
            # Anything raised during bind/serve dies in this thread otherwise —
            # log it so the parent thread's RuntimeError isn't the only signal.
            log.exception("grow WS listener thread crashed: %s", exc)
            ready.set()  # wake the parent so it surfaces a RuntimeError

    thread = threading.Thread(target=_run, daemon=True, name="grow-ws-listener")
    thread.start()
    if not ready.wait(timeout=5):
        raise RuntimeError(
            f"grow WS listener failed to bind {host}:{port} within 5s"
        )
    srv = server_holder.get("srv")
    if srv is None:
        # Thread set `ready` from the except branch; the real cause was logged.
        raise RuntimeError(
            f"grow WS listener thread crashed during startup (see log for cause); "
            f"could not bind {host}:{port}"
        )
    return _ListenerHandle(server=srv, loop=loop_holder["loop"], thread=thread)


def stop_ws_listener(handle, join_timeout: float = 2.0) -> None:
    """Gracefully stop a listener returned by start_ws_listener.

    Schedules `srv.close()` on the listener's own loop, waits for it to
    drain, then stops the loop and joins the thread. Idempotent — safe to
    call multiple times even after the loop has already been stopped.
    """
    if handle is None:
        return

    async def _close():
        handle.server.close()
        await handle.server.wait_closed()

    try:
        # Schedule close on the listener's loop and wait for drain. If the
        # loop is already stopped, run_coroutine_threadsafe raises
        # RuntimeError; if the future itself errors (loop torn down mid-call)
        # we silently move on — we still want to join the thread either way.
        future = asyncio.run_coroutine_threadsafe(_close(), handle.loop)
        try:
            future.result(timeout=join_timeout)
        except Exception:
            pass
        handle.loop.call_soon_threadsafe(handle.loop.stop)
    except RuntimeError:
        # Loop wasn't running — nothing to schedule.
        pass
    handle.thread.join(timeout=join_timeout)

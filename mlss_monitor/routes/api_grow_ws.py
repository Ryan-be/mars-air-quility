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
import threading
from datetime import datetime
from http import HTTPStatus

import websockets

from database.init_db import DB_FILE
from mlss_monitor.grow.auth import verify_secret
from mlss_monitor.grow.handlers import (
    handle_telemetry, handle_capabilities, handle_event,
)
from mlss_monitor.grow.photo_storage import handle_photo_frame

log = logging.getLogger(__name__)


def _validate_bearer(unit_id: int, token: str) -> bool:
    """Return True iff the bearer token matches an active unit's hash."""
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
    return verify_secret(token, row[0])


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


def start_ws_listener(host: str, port: int, registry):
    """Boot the WS listener on its own thread + event loop. Returns the server obj.

    Raises RuntimeError if the server fails to bind within 5 seconds (e.g. port
    already in use). Otherwise returns the websockets.WebSocketServer so callers
    can inspect the bound port (`server.sockets[0].getsockname()[1]`) when the
    caller passed `port=0` to let the OS assign one.
    """
    server_holder = {}
    ready = threading.Event()

    def _run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                srv = await websockets.serve(
                    lambda ws, path: _connection_handler(ws, path, registry),
                    host, port,
                    process_request=_process_request,
                    max_size=8 * 1024 * 1024,  # 8 MB max frame
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

    threading.Thread(target=_run, daemon=True, name="grow-ws-listener").start()
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
    return srv

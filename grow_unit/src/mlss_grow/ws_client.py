"""WebSocket client for the grow unit.

Single connection to MLSS. When connected: forwards messages from the
safety loop and dispatches incoming commands. When disconnected: buffers
to local SQLite. On reconnect: drains the buffer in timestamp order
before resuming live stream.
"""
import asyncio
import json
import logging
import os
import random
import ssl
from datetime import datetime
from typing import Callable, Optional

from mlss_grow.buffer import LocalBuffer
from mlss_grow.photo_buffer import PhotoBuffer
from mlss_grow.ws_protocol import encode_text_message, encode_photo_frame

log = logging.getLogger(__name__)


def _build_ssl_context(cert_path: "str | None") -> ssl.SSLContext:
    """Build the client SSLContext. If a cert is pinned, load it as a CA;
    otherwise drop verification + warn (C2 fix).

    The MLSS server presents a self-signed cert on the LAN. Without a
    pinned cert the default ssl context refuses to handshake
    (CERTIFICATE_VERIFY_FAILED), so the firmware can't connect at all.
    The fix:
      - install.sh fetches the cert via openssl s_client (TOFU at install
        time, documented LAN-trust model) and writes /etc/mlss/server.crt
      - this function loads that cert as a CA and keeps full verification
        on (CERT_REQUIRED + check_hostname=True)
      - if the cert is missing (dev/test, pre-install), fall back to
        CERT_NONE and log a prominent WARNING
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if cert_path and os.path.isfile(cert_path):
        ctx.load_verify_locations(cafile=cert_path)
        # Defaults for PROTOCOL_TLS_CLIENT are already CERT_REQUIRED +
        # check_hostname=True; set explicitly so the posture is visible.
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
    else:
        log.warning(
            "MLSS server cert not found at %s — falling back to "
            "verify_mode=CERT_NONE for the WS handshake. This is INSECURE: "
            "an attacker on the LAN can MITM the WSS connection. Run "
            "install.sh on a Pi to pin the cert, or set server_cert_path "
            "in /boot/mlss-grow.yaml.",
            cert_path,
        )
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _default_connect(url, token, cert_path):
    import websockets
    ctx = _build_ssl_context(cert_path)
    return await websockets.connect(
        url,
        ssl=ctx,
        extra_headers={"Authorization": f"Bearer {token}"},
    )


class WSClient:
    """Connect, send, receive, buffer, replay."""

    def __init__(self, url: str, token: str, buffer_db_path: str,
                 on_command: Callable[[dict], None],
                 connect_fn=_default_connect,
                 backoff_base: float = 1.0, backoff_max: float = 60.0,
                 server_cert_path: "str | None" = None,
                 on_reconnect_sync: Optional[Callable[[], None]] = None,
                 buffer_retention_days_provider: Optional[
                     Callable[[], "int | None"]
                 ] = None,
                 photo_buffer: Optional[PhotoBuffer] = None) -> None:
        self._url = url
        self._token = token
        # Wire eviction events from the buffer to a grow_errors-shaped
        # event over the WS. When the WS is down (the likely case when
        # the buffer is filling) the eviction event itself goes into the
        # buffer — acceptable, since once the WS comes back the eviction
        # events flush along with everything else.
        self._buffer = LocalBuffer(
            buffer_db_path,
            on_eviction=self._handle_buffer_eviction,
        )
        self._on_command = on_command
        self._connect_fn = connect_fn
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._server_cert_path = server_cert_path
        # Optional callback invoked once per successful reconnect, between
        # outbound buffer drain and the receive loop. Used by service.py
        # to re-pull + apply unit config so changes made while offline
        # take effect on reconnect, not on the next online config push.
        # Failures are logged and swallowed — they must NOT tear down the WS.
        self._on_reconnect_sync = on_reconnect_sync
        # Optional provider returning the latest buffer_retention_days
        # value pulled from the server (or None to skip pruning). Wired
        # by service.py from the same closure that captures pull_unit_config
        # results, so prune always uses the freshest value. Default to a
        # no-op lambda so existing call sites (no provider passed) keep
        # working unchanged.
        self._buffer_retention_days_provider = (
            buffer_retention_days_provider
            if buffer_retention_days_provider is not None
            else (lambda: None)
        )
        # Optional disk-backed buffer for photos taken while the WS is
        # down. Default None for backward compat with tests + callers
        # that don't care about photo persistence; service.py passes one
        # in production. Reverses the C2 deferral that dropped photos
        # outright (see photo_buffer.py docstring + spec §8 for why we
        # ship it now).
        self._photo_buffer = photo_buffer
        self._ws = None

    def _handle_buffer_eviction(self, *, reason: str, evicted_count: int) -> None:
        """Buffer eviction → grow_errors event. Best-effort; never raises.

        Called from inside LocalBuffer.append's commit flow when one of
        the size caps fires. We push a grow_errors-shaped event onto the
        same buffer (or onto the WS if up). The event's own enqueue
        could itself trigger another eviction, but only at the very edge
        of the cap — the next 100 appends won't re-check the byte cap,
        and the row cap only fires once per excess row, so we don't
        recurse.
        """
        body = encode_text_message(
            "event",
            datetime.utcnow(),
            {
                "kind": "buffer_eviction",
                "details": {
                    "reason": reason,
                    "evicted_count": evicted_count,
                },
            },
        )
        # Always go through the buffer — if the WS is up the next
        # _replay_buffer (or send_text path) will pick it up; if not it
        # rides out alongside the rest.
        self._buffer.append("event", body, datetime.utcnow())

    async def _connect_once(self) -> bool:
        try:
            self._ws = await self._connect_fn(
                self._url, self._token, self._server_cert_path,
            )
            return True
        except Exception as exc:
            log.warning("WS connect failed: %s", exc)
            self._ws = None
            return False

    def is_connected(self) -> bool:
        return self._ws is not None

    async def send_text(self, msg_type: str, ts: datetime, payload: dict) -> None:
        body = encode_text_message(msg_type, ts, payload)
        if self._ws is None:
            self._buffer.append(msg_type, body, ts)
            return
        try:
            await self._ws.send(body)
        except Exception as exc:
            log.warning("WS send failed (%s); buffering", exc)
            self._buffer.append(msg_type, body, ts)
            self._ws = None

    async def send_photo(self, metadata: dict, jpeg_bytes: bytes) -> None:
        """Send a photo frame, buffering to disk when the WS is down.

        Reverses the C2 "drop on outage" behaviour. When the WS is
        unreachable (or send fails mid-flight) the photo is written to
        the disk-backed PhotoBuffer if one is wired, then uploaded
        oldest-first by _replay_photos on the next reconnect. If no
        buffer is wired (test paths), behaviour falls back to the prior
        drop-on-failure semantics.
        """
        if self._ws is None:
            if self._photo_buffer is not None:
                self._photo_buffer.append(metadata, jpeg_bytes)
                log.info(
                    "WS down; buffered photo to disk (size=%d)",
                    self._photo_buffer.size(),
                )
            else:
                log.info("WS down; dropping photo (no buffer wired)")
            return
        try:
            frame = encode_photo_frame(metadata, jpeg_bytes)
            await self._ws.send(frame)
        except Exception as exc:
            log.warning("WS photo send failed: %s; buffering", exc)
            if self._photo_buffer is not None:
                self._photo_buffer.append(metadata, jpeg_bytes)
            self._ws = None

    async def _replay_buffer(self) -> None:
        """Drain the local buffer to the WS, deleting each row only after
        a successful send.

        If a send fails mid-replay (socket drop, server hiccup) the
        un-sent rows stay in the buffer and the next reconnect picks
        them up. The previous flow `pop_all()` deleted the entire batch
        up front, which silently lost rows when the connection died
        partway through.
        """
        rows = self._buffer.peek_all()
        if not rows:
            return
        log.info("replaying %d buffered messages", len(rows))
        # Notify server we're replaying (ack-target identification). If the
        # marker itself fails to send, bail out before deleting anything —
        # the whole batch will be re-attempted on next reconnect.
        start_event = encode_text_message(
            "event", datetime.utcnow(),
            {"kind": "buffer_replay_started", "details": {"count": len(rows)}},
        )
        try:
            await self._ws.send(start_event)
        except Exception as exc:
            log.warning(
                "buffer replay start failed: %s; %d rows preserved",
                exc, len(rows),
            )
            self._ws = None
            return

        sent = 0
        for row in rows:
            try:
                await self._ws.send(row.body)
            except Exception as exc:
                remaining = len(rows) - sent
                log.warning(
                    "buffer replay interrupted after %d/%d rows: %s; "
                    "%d rows preserved for next reconnect",
                    sent, len(rows), exc, remaining,
                )
                self._ws = None
                return
            # Delete only after the send succeeded — this is the whole
            # point of the peek-then-delete protocol.
            self._buffer.delete(row.id)
            sent += 1

        # All rows acknowledged; emit the completion marker. Failure of
        # the marker itself is non-fatal — the data has already been
        # delivered.
        done_event = encode_text_message(
            "event", datetime.utcnow(),
            {"kind": "buffer_replay_complete", "details": {}},
        )
        try:
            await self._ws.send(done_event)
        except Exception as exc:
            log.warning("buffer replay completion marker failed: %s", exc)
            self._ws = None

    async def _replay_photos(self) -> None:
        """Upload all buffered photos in oldest-first order.

        Per-photo delete only after a successful send (mirrors the
        text-buffer protocol in _replay_buffer). A mid-replay
        disconnect leaves the un-sent tail on disk for the next
        reconnect. No-op if no photo buffer is wired or the buffer is
        empty.
        """
        if self._photo_buffer is None:
            return
        photos = self._photo_buffer.peek_all()
        if not photos:
            return
        log.info("uploading %d buffered photos", len(photos))
        for index, photo in enumerate(photos):
            if self._ws is None:
                log.info(
                    "WS dropped during photo replay; %d photos remain",
                    len(photos) - index,
                )
                return
            try:
                with open(photo.jpeg_path, "rb") as f:
                    jpeg_bytes = f.read()
                frame = encode_photo_frame(photo.metadata, jpeg_bytes)
                await self._ws.send(frame)
            except Exception as exc:
                log.warning(
                    "photo upload failed for %s: %s; will retry next reconnect",
                    photo.jpeg_path, exc,
                )
                self._ws = None
                return
            # Delete only after the send succeeded — the per-photo
            # durability guarantee. Same as the text-buffer protocol.
            self._photo_buffer.delete(photo)

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        # asyncio.CancelledError inherits from BaseException (Python 3.8+),
        # not Exception — so the broad `except Exception` below cleanly skips
        # it and cancellation propagates without an explicit re-raise.
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "command":
                            self._on_command(parsed["payload"])
                    except Exception as exc:
                        log.warning("bad incoming message: %s", exc)
        except Exception as exc:
            log.warning("receive loop ended: %s", exc)
            self._ws = None

    async def run_forever(self) -> None:
        """Top-level connection lifecycle: connect, replay, receive, reconnect."""
        attempt = 0
        while True:
            ok = await self._connect_once()
            if not ok:
                delay = min(self._backoff_max, self._backoff_base * (2 ** attempt))
                delay *= 1.0 + random.uniform(-0.2, 0.2)  # jitter
                attempt += 1
                await asyncio.sleep(delay)
                continue
            attempt = 0
            try:
                await self._replay_buffer()
                # Re-sync config from the server. Config may have changed
                # while we were offline, and the server's config_changed
                # WS push silently no-ops while the unit is disconnected
                # (the registry has no entry for the unit). Without this
                # pull, the firmware would run stale config until the next
                # *online* config edit. Failures here must NOT tear down
                # the WS — log and proceed; we'll fall back to whatever
                # loop_cfg already holds, and the next config_changed push
                # while online will re-sync.
                if self._on_reconnect_sync is not None:
                    try:
                        self._on_reconnect_sync()
                    except Exception as exc:
                        log.warning("on_reconnect_sync failed: %s", exc)
                # Buffer housekeeping: prune old rows by retention policy
                # AFTER on_reconnect_sync runs so we use the freshest
                # buffer_retention_days value pulled from the server. A
                # provider returning None (= no override / unconfigured)
                # skips pruning; the hard size caps in LocalBuffer.append
                # are still in force as defence-in-depth. Failures here
                # MUST NOT tear down the WS — log and continue.
                try:
                    retention_days = self._buffer_retention_days_provider()
                    if retention_days is not None and retention_days > 0:
                        self._buffer.prune(retention_days)
                except Exception as exc:
                    log.warning("buffer prune failed: %s", exc)
                # Drain buffered photos after the text buffer + config
                # sync. Order matters: text messages first so server-side
                # time-series gaps are filled before binary frames start
                # competing for the same socket. _replay_photos is no-op
                # if the photo buffer isn't wired (tests) or empty.
                # Failure inside _replay_photos clears self._ws and
                # returns; we still want to fall through to the receive
                # loop to handle that — but receive loop will exit
                # immediately if _ws is None and run_forever loops back
                # to reconnect. That's the right cycle.
                await self._replay_photos()
                await self._receive_loop()
            finally:
                self._ws = None
            await asyncio.sleep(self._backoff_base)

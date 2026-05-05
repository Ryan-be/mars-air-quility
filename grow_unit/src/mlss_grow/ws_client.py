"""WebSocket client for the grow unit.

Single connection to MLSS. When connected: forwards messages from the
safety loop and dispatches incoming commands. When disconnected: buffers
to local SQLite. On reconnect: drains the buffer in timestamp order
before resuming live stream.
"""
import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Callable

from mlss_grow.buffer import LocalBuffer
from mlss_grow.ws_protocol import encode_text_message, encode_photo_frame

log = logging.getLogger(__name__)


async def _default_connect(url, token):
    import websockets
    return await websockets.connect(url, extra_headers={"Authorization": f"Bearer {token}"})


class WSClient:
    """Connect, send, receive, buffer, replay."""

    def __init__(self, url: str, token: str, buffer_db_path: str,
                 on_command: Callable[[dict], None],
                 connect_fn=_default_connect,
                 backoff_base: float = 1.0, backoff_max: float = 60.0) -> None:
        self._url = url
        self._token = token
        self._buffer = LocalBuffer(buffer_db_path)
        self._on_command = on_command
        self._connect_fn = connect_fn
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._ws = None

    async def _connect_once(self) -> bool:
        try:
            self._ws = await self._connect_fn(self._url, self._token)
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
        if self._ws is None:
            log.info("WS down; dropping photo (not buffered to save SD wear)")
            return
        try:
            frame = encode_photo_frame(metadata, jpeg_bytes)
            await self._ws.send(frame)
        except Exception as exc:
            log.warning("WS photo send failed: %s", exc)
            self._ws = None

    async def _replay_buffer(self) -> None:
        rows = self._buffer.pop_all()
        if not rows:
            return
        log.info("replaying %d buffered messages", len(rows))
        # Notify server we're replaying (ack-target identification)
        start_event = encode_text_message(
            "event", datetime.utcnow(),
            {"kind": "buffer_replay_started", "details": {"count": len(rows)}},
        )
        try:
            await self._ws.send(start_event)
            for row in rows:
                await self._ws.send(row.body)
            done_event = encode_text_message(
                "event", datetime.utcnow(),
                {"kind": "buffer_replay_complete", "details": {}},
            )
            await self._ws.send(done_event)
        except Exception as exc:
            log.warning("buffer replay failed: %s; rows already removed from buffer", exc)
            self._ws = None

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    try:
                        parsed = json.loads(msg)
                        if parsed.get("type") == "command":
                            self._on_command(parsed["payload"])
                    except Exception as exc:
                        log.warning("bad incoming message: %s", exc)
        except asyncio.CancelledError:
            raise
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
                await self._receive_loop()
            finally:
                self._ws = None
            await asyncio.sleep(self._backoff_base)

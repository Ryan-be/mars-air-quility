"""WSClient: connect, send, buffer-on-failure, replay-on-reconnect, command dispatch."""
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from mlss_grow.ws_client import WSClient


class FakeWSConnection:
    """Stand-in for a real websocket connection. Captures sent frames + queues incoming."""
    def __init__(self):
        self.sent = []
        self._inbox = asyncio.Queue()
        self.closed = False

    async def send(self, msg):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(msg)

    async def __aiter__(self):
        while not self.closed:
            try:
                yield await asyncio.wait_for(self._inbox.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

    async def push_incoming(self, msg):
        await self._inbox.put(msg)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_send_when_connected_goes_through(tmp_path):
    fake_ws = FakeWSConnection()
    cmd_received = []

    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: cmd_received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    await client._connect_once()
    await client.send_text("telemetry", datetime(2026, 1, 1),
                            {"soil_moisture_raw": 612, "light_state": True, "pump_state": False})
    assert len(fake_ws.sent) == 1
    parsed = json.loads(fake_ws.sent[0])
    assert parsed["type"] == "telemetry"


@pytest.mark.asyncio
async def test_send_when_disconnected_buffers(tmp_path):
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(side_effect=ConnectionError("never connects")),
    )
    # Don't bother connecting; send should buffer
    await client.send_text("telemetry", datetime(2026, 5, 3),
                            {"soil_moisture_raw": 612, "light_state": True, "pump_state": False})
    assert client._buffer.size() == 1


@pytest.mark.asyncio
async def test_replay_drains_buffer_on_reconnect(tmp_path):
    fake_ws = FakeWSConnection()
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    # Pre-populate buffer
    client._buffer.append("telemetry", '{"x":1}', ts=datetime(2026, 1, 1))
    client._buffer.append("event", '{"y":2}', ts=datetime(2026, 1, 2))
    assert client._buffer.size() == 2

    await client._connect_once()
    await client._replay_buffer()

    # Buffer drained; replay events sent
    assert client._buffer.size() == 0
    sent_kinds = [json.loads(m).get("type") for m in fake_ws.sent if isinstance(m, str)]
    # First two are buffer_replay_started + actual messages + buffer_replay_complete
    assert "event" in sent_kinds  # buffer_replay_started/complete are events too


@pytest.mark.asyncio
async def test_incoming_command_dispatched_to_handler(tmp_path):
    fake_ws = FakeWSConnection()
    received = []
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    await client._connect_once()

    cmd_msg = json.dumps({
        "type": "command", "ts": "2026-05-03T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 10}},
    })
    await fake_ws.push_incoming(cmd_msg)

    # Run the receive loop briefly
    recv_task = asyncio.create_task(client._receive_loop())
    await asyncio.sleep(0.2)
    recv_task.cancel()

    assert len(received) == 1
    assert received[0]["name"] == "identify"
    assert received[0]["args"]["duration_s"] == 10

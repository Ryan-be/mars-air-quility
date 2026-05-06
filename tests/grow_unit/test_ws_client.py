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
async def test_replay_buffer_keeps_unsent_rows_on_send_failure(tmp_path):
    """Mid-replay disconnect must NOT drop the rows that hadn't been sent
    yet. Old code did `pop_all()` up front (delete all) then sent — a
    socket close after the third send lost rows 4 and 5 forever.
    New protocol: peek-then-delete-each-success."""

    # Fake WS that succeeds on the first 3 sends then raises.
    class PartialFailWS:
        def __init__(self):
            self.sent = []
            self._fail_after = 3

        async def send(self, msg):
            if len(self.sent) >= self._fail_after:
                raise ConnectionError("simulated drop mid-replay")
            self.sent.append(msg)

    fake_ws = PartialFailWS()
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    # Pre-populate with 5 rows
    for i in range(5):
        client._buffer.append(
            "telemetry",
            json.dumps({"i": i,
                        "soil_moisture_raw": 600 + i,
                        "light_state": True, "pump_state": False}),
            ts=datetime(2026, 1, 1, 0, i),
        )
    assert client._buffer.size() == 5
    starting_rows = client._buffer.peek_all()
    starting_bodies = [r.body for r in starting_rows]

    await client._connect_once()
    await client._replay_buffer()

    # The fake_ws accepts at most _fail_after + the start_event marker.
    # Either way, *some* rows must remain — specifically, the unsent ones.
    remaining = client._buffer.peek_all()
    assert len(remaining) > 0, (
        "buffer must NOT be fully drained after a mid-replay failure — "
        "the un-sent rows should be preserved for the next reconnect"
    )

    # Every remaining body must come from the original buffer (i.e. we
    # didn't accidentally re-write or duplicate anything).
    remaining_bodies = {r.body for r in remaining}
    assert remaining_bodies.issubset(set(starting_bodies))

    # And: nothing that we successfully sent (the actual telemetry frames,
    # not the start_event marker) should still be in the buffer — those are
    # acknowledged and gone.
    sent_bodies = set(fake_ws.sent)
    assert sent_bodies.isdisjoint(remaining_bodies), (
        "rows we successfully delivered must have been deleted from the "
        "local buffer; otherwise reconnect would re-send duplicates"
    )


@pytest.mark.asyncio
async def test_replay_buffer_full_drain_on_clean_run(tmp_path):
    """Pinning happy-path behaviour after the refactor: a replay where
    every send succeeds drains the buffer down to zero, the same as the
    old pop_all() flow did."""
    fake_ws = FakeWSConnection()
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
    )
    for i in range(3):
        client._buffer.append(
            "telemetry",
            json.dumps({"i": i, "soil_moisture_raw": 600 + i,
                        "light_state": True, "pump_state": False}),
            ts=datetime(2026, 1, 1, 0, i),
        )
    assert client._buffer.size() == 3

    await client._connect_once()
    await client._replay_buffer()

    assert client._buffer.size() == 0


@pytest.mark.asyncio
async def test_replay_buffer_resends_only_remaining_after_partial_fail(tmp_path):
    """Two-phase test: simulate a partial replay, reconnect, replay
    again with a healthy WS, assert *only* the previously-unsent rows
    end up on the second WS."""

    class FailAfterN:
        def __init__(self, n):
            self.sent = []
            self._fail_after = n

        async def send(self, msg):
            if len(self.sent) >= self._fail_after:
                raise ConnectionError("simulated drop")
            self.sent.append(msg)

    bad_ws = FailAfterN(n=3)  # accept 3 frames then bomb
    client = WSClient(
        url="ws://test", token="t", buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=bad_ws),
    )
    # 5 rows
    for i in range(5):
        client._buffer.append(
            "telemetry",
            json.dumps({"i": i, "soil_moisture_raw": 600 + i,
                        "light_state": True, "pump_state": False}),
            ts=datetime(2026, 1, 1, 0, i),
        )
    bad_sent_count_before = client._buffer.size()
    await client._connect_once()
    await client._replay_buffer()

    remaining_after_fail = client._buffer.peek_all()
    assert remaining_after_fail, "expected unsent rows to remain"

    # Reconnect to a healthy WS and verify only the leftovers are sent.
    good_ws = FakeWSConnection()
    client._connect_fn = AsyncMock(return_value=good_ws)
    await client._connect_once()
    await client._replay_buffer()

    assert client._buffer.size() == 0
    # Every body still in `remaining_after_fail` must appear in good_ws.sent.
    leftover_bodies = {r.body for r in remaining_after_fail}
    sent_on_good = set(good_ws.sent)
    missing = leftover_bodies - sent_on_good
    assert not missing, (
        f"rows the partial replay left behind must be re-sent on the next "
        f"successful replay; missing: {missing}"
    )


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

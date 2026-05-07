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


# ----------------------------------------------------------------------------
# on_reconnect_sync — pull-on-reconnect bug fix.
#
# The server's `config_changed` WS push silently no-ops while a unit is
# disconnected (the registry has no entry for the unit). Without a pull
# on reconnect, a config edit made while the unit is offline would leave
# the firmware running stale config until the *next* online edit. The
# fix wires WSClient to call an optional sync callback between the
# outbound buffer drain and the receive loop, on every reconnect.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_calls_on_reconnect_sync_after_replay_before_receive(
    tmp_path,
):
    """Reconnect order: connect -> replay outbound -> on_reconnect_sync ->
    receive. Without this, configure-while-offline-then-reconnect leaves
    the unit running stale config until the next online config change."""
    fake_ws = FakeWSConnection()
    call_log: list[str] = []

    def record_sync():
        call_log.append("on_reconnect_sync")

    received = []

    def record_command(cmd):
        call_log.append("on_command")
        received.append(cmd)

    # Pre-populate buffer so _replay_buffer has rows to send. Each send
    # appends to fake_ws.sent — we'll read that list below to confirm
    # ordering: every replay-related send happened before on_reconnect_sync.
    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=record_command,
        connect_fn=AsyncMock(return_value=fake_ws),
        on_reconnect_sync=record_sync,
        backoff_base=0.0,  # don't waste time sleeping in run_forever
    )
    client._buffer.append(
        "telemetry",
        json.dumps({"i": 0, "soil_moisture_raw": 600,
                    "light_state": True, "pump_state": False}),
        ts=datetime(2026, 1, 1),
    )

    # Drive run_forever for one connect cycle, then push an incoming
    # command frame so the receive loop has something to dispatch, then
    # cancel.
    runner = asyncio.create_task(client.run_forever())
    # Wait until the buffer has been drained AND on_reconnect_sync has
    # been called (i.e. the runner has reached the receive loop).
    for _ in range(50):
        if "on_reconnect_sync" in call_log and client._buffer.size() == 0:
            break
        await asyncio.sleep(0.02)

    # Now push a command frame so we can confirm receive happens AFTER sync.
    await fake_ws.push_incoming(json.dumps({
        "type": "command", "ts": "2026-05-06T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 5}},
    }))
    for _ in range(50):
        if "on_command" in call_log:
            break
        await asyncio.sleep(0.02)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    # 1. Sync was called exactly once for the single reconnect cycle.
    assert call_log.count("on_reconnect_sync") == 1, call_log
    # 2. The incoming command was dispatched.
    assert call_log.count("on_command") == 1, call_log
    # 3. Critical ordering: sync runs BEFORE the first command dispatch.
    #    If the firmware received and dispatched a command before the
    #    fresh config landed, that command might run against stale
    #    config (e.g. an old PID gain).
    assert call_log.index("on_reconnect_sync") < call_log.index("on_command"), (
        f"on_reconnect_sync must run before any command is dispatched; got {call_log}"
    )
    # 4. The buffered telemetry made it onto the wire, proving sync ran
    #    AFTER replay (otherwise an exception in sync would have
    #    short-circuited; see next test for that case).
    assert client._buffer.size() == 0


@pytest.mark.asyncio
async def test_run_forever_continues_when_on_reconnect_sync_raises(tmp_path):
    """A failed config pull must log and proceed — do NOT tear down the
    WS or skip the receive loop. Firmware runs stale config until the
    next config_changed push, but stays online and keeps dispatching
    incoming commands."""
    fake_ws = FakeWSConnection()
    received: list[dict] = []

    def boom():
        raise RuntimeError("simulated config pull failure")

    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
        on_reconnect_sync=boom,
        backoff_base=0.0,
    )

    runner = asyncio.create_task(client.run_forever())
    # Give the runner time to connect, replay, hit the failing sync, and
    # then proceed into the receive loop.
    await asyncio.sleep(0.1)

    # Receive loop must still be running — push a command and confirm it
    # reaches the handler.
    await fake_ws.push_incoming(json.dumps({
        "type": "command", "ts": "2026-05-06T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 5}},
    }))
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.02)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert len(received) == 1, (
        "WS receive loop must continue after on_reconnect_sync raises — "
        "a failed config pull is best-effort, not a connection-killer"
    )
    assert received[0]["name"] == "identify"


@pytest.mark.asyncio
async def test_on_reconnect_sync_optional_default_none_keeps_old_behavior(
    tmp_path,
):
    """Existing call sites that don't pass on_reconnect_sync must keep
    working unchanged — the parameter is optional with default None."""
    fake_ws = FakeWSConnection()
    received: list[dict] = []
    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
        backoff_base=0.0,
        # NB: no on_reconnect_sync — should default to None and the
        # run_forever loop must skip the call without erroring.
    )
    assert client._on_reconnect_sync is None

    runner = asyncio.create_task(client.run_forever())
    await asyncio.sleep(0.1)
    await fake_ws.push_incoming(json.dumps({
        "type": "command", "ts": "2026-05-06T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 5}},
    }))
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.02)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert len(received) == 1


# ----------------------------------------------------------------------------
# C2: buffer_retention_days_provider — prune the local SQLite buffer on every
# successful reconnect using the freshest value pulled from the server. The
# provider closure is built by service.py and shares state with on_reconnect_sync;
# from WSClient's POV it's just a callable returning Optional[int].
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_calls_prune_after_replay_with_retention_from_provider(
    tmp_path,
):
    """Reconnect cycle: connect -> replay -> on_reconnect_sync -> prune ->
    receive. The provider returns the latest pulled retention value;
    WSClient passes it straight to LocalBuffer.prune.
    """
    fake_ws = FakeWSConnection()
    prune_calls: list[int] = []

    def provider() -> int:
        return 7  # 7 days, the firmware default mirroring app_settings

    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
        buffer_retention_days_provider=provider,
        backoff_base=0.0,
    )
    # Patch the buffer's prune method so we can observe the call without
    # depending on the actual delete-by-cutoff behaviour (covered in
    # test_buffer.py).
    real_prune = client._buffer.prune

    def spy_prune(retention_days, now=None):
        prune_calls.append(retention_days)
        return real_prune(retention_days, now)
    client._buffer.prune = spy_prune

    runner = asyncio.create_task(client.run_forever())
    # Drive long enough for one connect → replay → prune cycle.
    for _ in range(50):
        if prune_calls:
            break
        await asyncio.sleep(0.02)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert prune_calls == [7], (
        f"buffer.prune should be called exactly once with retention=7 "
        f"per reconnect cycle; got {prune_calls}"
    )


@pytest.mark.asyncio
async def test_run_forever_skips_prune_when_provider_returns_none(tmp_path):
    """Provider returns None → "no override / unconfigured" → prune is
    skipped. Hard size caps in LocalBuffer.append still defend.
    """
    fake_ws = FakeWSConnection()
    prune_calls: list[int] = []

    def provider() -> None:
        return None

    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
        buffer_retention_days_provider=provider,
        backoff_base=0.0,
    )

    def spy_prune(retention_days, now=None):
        prune_calls.append(retention_days)
    client._buffer.prune = spy_prune

    runner = asyncio.create_task(client.run_forever())
    # Give the runner enough time to complete a full reconnect cycle and
    # pass the prune call site.
    await asyncio.sleep(0.15)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert prune_calls == [], (
        f"prune must NOT be called when provider returns None; got {prune_calls}"
    )


@pytest.mark.asyncio
async def test_run_forever_continues_when_prune_raises(tmp_path):
    """Buffer prune is best-effort — a raised exception inside prune
    must NOT tear down the WS or skip the receive loop. Pin the same
    semantics as on_reconnect_sync's error handling.
    """
    fake_ws = FakeWSConnection()
    received: list[dict] = []

    def provider() -> int:
        return 3  # any positive value to enter the prune branch

    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: received.append(cmd),
        connect_fn=AsyncMock(return_value=fake_ws),
        buffer_retention_days_provider=provider,
        backoff_base=0.0,
    )

    def boom(retention_days, now=None):
        raise RuntimeError("simulated prune failure")
    client._buffer.prune = boom

    runner = asyncio.create_task(client.run_forever())
    await asyncio.sleep(0.1)

    # Receive loop must still be running — push a command, confirm it
    # reaches the handler. If prune had torn down the WS, the runner
    # would be stuck in a reconnect loop and the command would never
    # dispatch.
    await fake_ws.push_incoming(json.dumps({
        "type": "command", "ts": "2026-05-06T12:00:00Z",
        "payload": {"name": "identify", "args": {"duration_s": 5}},
    }))
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.02)

    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert len(received) == 1, (
        "WS receive loop must continue after prune raises — buffer "
        "prune is best-effort housekeeping, not a connection-killer"
    )


@pytest.mark.asyncio
async def test_buffer_retention_days_provider_optional_default_no_prune(
    tmp_path,
):
    """Existing call sites that don't pass a provider must keep working —
    the default is a `lambda: None` that skips pruning entirely.
    """
    fake_ws = FakeWSConnection()
    prune_calls: list[int] = []

    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
        # NB: no buffer_retention_days_provider — default lambda: None
        # must skip the prune branch silently.
        backoff_base=0.0,
    )

    def spy_prune(retention_days, now=None):
        prune_calls.append(retention_days)
    client._buffer.prune = spy_prune

    runner = asyncio.create_task(client.run_forever())
    await asyncio.sleep(0.15)
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    assert prune_calls == []


@pytest.mark.asyncio
async def test_buffer_eviction_emits_event_on_buffer(tmp_path):
    """When the buffer's size cap evicts, WSClient's eviction handler
    appends a `buffer_eviction` event back into the same buffer. This
    verifies the wiring: forcing an eviction must result in an event
    row that ends up replayed once the WS is up.
    """
    fake_ws = FakeWSConnection()
    client = WSClient(
        url="ws://test", token="t",
        buffer_db_path=str(tmp_path / "b.sqlite"),
        on_command=lambda cmd: None,
        connect_fn=AsyncMock(return_value=fake_ws),
        backoff_base=0.0,
    )

    # Replace the buffer with one that has a tiny row cap so we can
    # force eviction with just a handful of appends. Reuse the same
    # on_eviction wiring (the WSClient sets it on construction; we
    # rebuild via the same handler).
    from mlss_grow.buffer import LocalBuffer as _LB
    client._buffer = _LB(
        db_path=str(tmp_path / "b2.sqlite"),
        max_rows=3,
        on_eviction=client._handle_buffer_eviction,
    )

    # Append 5 telemetry rows with cap=3 → 2 evictions.
    for i in range(5):
        client._buffer.append(
            "telemetry", f'{{"i":{i}}}',
            ts=datetime(2026, 1, 1),
        )

    # The buffer should now contain: at least one buffer_eviction event
    # row (added by the handler) plus the most-recent telemetry rows.
    rows = client._buffer.peek_all()
    bodies = [r.body for r in rows]
    eviction_event_present = any(
        "buffer_eviction" in b for b in bodies
    )
    assert eviction_event_present, (
        f"expected at least one buffer_eviction event row in the buffer "
        f"after eviction; got bodies={bodies}"
    )

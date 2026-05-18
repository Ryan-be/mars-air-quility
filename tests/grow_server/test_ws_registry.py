"""WSRegistry: tracks active per-unit WebSocket connections.

Used by REST endpoints to push commands (e.g. identify, water_now) to a
specific unit, and by status checks to know whether a unit is currently
holding an open connection.
"""
import pytest
from mlss_monitor.grow.ws_registry import WSRegistry


class FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, msg):
        if self.closed:
            raise RuntimeError("closed")
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_register_and_lookup():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    assert reg.is_connected(42) is True
    assert reg.is_connected(999) is False
    assert reg.connection_count() == 1


@pytest.mark.asyncio
async def test_unregister_removes_connection():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    reg.unregister(42)
    assert reg.is_connected(42) is False
    assert reg.connection_count() == 0


@pytest.mark.asyncio
async def test_send_command_to_unit():
    reg = WSRegistry()
    ws = FakeWS()
    reg.register(unit_id=42, ws=ws)
    await reg.send_to_unit(42, '{"type":"command","payload":{"name":"identify"}}')
    assert ws.sent == ['{"type":"command","payload":{"name":"identify"}}']


@pytest.mark.asyncio
async def test_send_to_disconnected_unit_raises():
    reg = WSRegistry()
    with pytest.raises(KeyError):
        await reg.send_to_unit(999, "anything")


@pytest.mark.asyncio
async def test_re_register_replaces_old_connection():
    """If a unit reconnects (new WS) the old reference is dropped."""
    reg = WSRegistry()
    old_ws = FakeWS()
    new_ws = FakeWS()
    reg.register(42, old_ws)
    reg.register(42, new_ws)
    await reg.send_to_unit(42, "msg")
    assert old_ws.sent == []
    assert new_ws.sent == ["msg"]


@pytest.mark.asyncio
async def test_list_connected_unit_ids():
    reg = WSRegistry()
    reg.register(1, FakeWS())
    reg.register(2, FakeWS())
    reg.register(5, FakeWS())
    assert sorted(reg.connected_unit_ids()) == [1, 2, 5]

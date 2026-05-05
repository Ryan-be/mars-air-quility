"""Per-unit WebSocket connection registry.

The MLSS WS listener registers each accepted connection here keyed by
unit_id. REST endpoints (manual identify/water/light-override) reach in
to send commands. Status checks can query is_connected() to render
'online' state without round-tripping the unit.
"""
from threading import Lock


class WSRegistry:
    def __init__(self):
        self._connections: dict[int, object] = {}  # unit_id -> ws
        self._lock = Lock()

    def register(self, unit_id: int, ws):
        """Register a new WS connection. Replaces any prior connection for that unit."""
        with self._lock:
            self._connections[unit_id] = ws

    def unregister(self, unit_id: int):
        """Remove a unit's connection; no-op if not registered."""
        with self._lock:
            self._connections.pop(unit_id, None)

    def is_connected(self, unit_id: int) -> bool:
        with self._lock:
            return unit_id in self._connections

    def connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def connected_unit_ids(self) -> list[int]:
        with self._lock:
            return list(self._connections.keys())

    async def send_to_unit(self, unit_id: int, message: str):
        """Send a text message to a connected unit. Raises KeyError if not connected."""
        with self._lock:
            ws = self._connections.get(unit_id)
        if ws is None:
            raise KeyError(f"unit {unit_id} not connected")
        await ws.send(message)

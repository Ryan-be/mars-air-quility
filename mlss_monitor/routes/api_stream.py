"""SSE streaming endpoint — pushes real-time events to the browser."""

import json
import queue
import time as _time

from flask import Blueprint, Response, current_app, jsonify, request

from mlss_monitor import state

api_stream_bp = Blueprint("api_stream", __name__)

_HEARTBEAT_SECONDS = 10
_MAX_LIFETIME_SECONDS = 600  # 10 min; EventSource auto-reconnects


def _sse_format(msg: dict) -> str:
    """Encode a single event bus message as an SSE text frame."""
    lines = [
        f"id: {msg['id']}",
        f"event: {msg['event']}",
        f"data: {json.dumps(msg['data'])}",
    ]
    return "\n".join(lines) + "\n\n"


@api_stream_bp.route("/api/stream")
def stream():
    """Long-lived SSE connection.  The client receives events in real time
    and can reconnect transparently via the ``EventSource`` API."""
    bus = state.event_bus
    if bus is None:
        return Response("Event bus not initialised", status=503)

    testing = current_app.config.get("TESTING", False)
    sub_queue = bus.subscribe(replay=True)

    def generate():
        start_time = _time.monotonic()
        try:
            while True:
                if _time.monotonic() - start_time > _MAX_LIFETIME_SECONDS:
                    yield ": reconnect\n\n"
                    return
                try:
                    timeout = 0.1 if testing else _HEARTBEAT_SECONDS
                    msg = sub_queue.get(timeout=timeout)
                    yield _sse_format(msg)
                except queue.Empty:
                    if testing:
                        return
                    yield ": heartbeat\n\n"
        finally:
            bus.unsubscribe(sub_queue)

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_stream_bp.route("/api/stream/history")
def stream_history():
    """Return recent event history as JSON (useful for late-joining
    clients or debugging)."""
    bus = state.event_bus
    if bus is None:
        return jsonify([])
    event_type = request.args.get("event")
    return jsonify(bus.get_history(event_type=event_type))

"""WS message encoding: matches mlss_contracts envelope shape."""
import json
import struct
from datetime import datetime


def encode_text_message(msg_type: str, ts: datetime, payload: dict) -> str:
    """JSON envelope expected by the server WS listener."""
    return json.dumps({
        "type": msg_type,
        "ts": ts.isoformat() + "Z",
        "payload": payload,
    })


def encode_photo_frame(metadata: dict, jpeg_bytes: bytes) -> bytes:
    """Binary frame: [4 bytes BE header_len][JSON header][JPEG bytes]."""
    header_bytes = json.dumps(metadata).encode("utf-8")
    return struct.pack(">I", len(header_bytes)) + header_bytes + jpeg_bytes

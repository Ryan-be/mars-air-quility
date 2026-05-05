"""Outgoing WS message serialisation: text envelope + binary photo frame."""
import json
import struct
from datetime import datetime
from mlss_grow.ws_protocol import encode_text_message, encode_photo_frame


def test_encode_text_message_envelope():
    out = encode_text_message(
        msg_type="telemetry",
        ts=datetime(2026, 5, 3, 12, 34, 18),
        payload={"soil_moisture_raw": 612, "light_state": True, "pump_state": False},
    )
    parsed = json.loads(out)
    assert parsed["type"] == "telemetry"
    assert parsed["ts"].startswith("2026-05-03T12:34:18")
    assert parsed["payload"]["soil_moisture_raw"] == 612


def test_encode_photo_frame_layout():
    jpeg = b"\xff\xd8\xff\xe0FAKE" + b"\x00" * 100
    meta = {"taken_at": "2026-05-03T12:34:18Z", "width": 1920, "height": 1080,
            "jpeg_quality": 85, "shutter_us": 16667, "iso": 100}
    frame = encode_photo_frame(meta, jpeg)
    # First 4 bytes = header length BE
    h_len = struct.unpack(">I", frame[:4])[0]
    assert h_len > 0
    parsed_header = json.loads(frame[4:4 + h_len].decode("utf-8"))
    assert parsed_header == meta
    assert frame[4 + h_len:] == jpeg


def test_text_message_uses_z_suffix_for_utc():
    out = encode_text_message("event", datetime(2026, 1, 1), {})
    parsed = json.loads(out)
    assert parsed["ts"].endswith("Z")

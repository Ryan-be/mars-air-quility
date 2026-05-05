"""Camera wrapper: detect, capture (returns JPEG bytes + metadata)."""
from unittest.mock import MagicMock
from mlss_grow.camera import Camera, CameraNotAvailable
import pytest


def test_detect_returns_none_when_picamera2_missing(monkeypatch):
    monkeypatch.setattr("mlss_grow.camera._picamera2_module", None)
    assert Camera.detect() is None


def test_detect_returns_camera_instance_when_lib_present(monkeypatch):
    fake_pc2 = MagicMock()
    fake_pc2.Picamera2 = MagicMock(return_value=MagicMock())
    monkeypatch.setattr("mlss_grow.camera._picamera2_module", fake_pc2)
    cam = Camera.detect()
    assert isinstance(cam, Camera)


def test_capture_returns_bytes_and_metadata(monkeypatch):
    fake_drv = MagicMock()
    fake_drv.capture_array = MagicMock(return_value=MagicMock())
    fake_drv.camera_properties = {"PixelArraySize": (1920, 1080)}
    fake_drv.metadata = MagicMock(return_value={"ExposureTime": 16667, "AnalogueGain": 1.0})

    cam = Camera(driver=fake_drv)

    # Mock the JPEG encoding step (we don't want to actually run picamera2 here)
    monkeypatch.setattr("mlss_grow.camera._encode_jpeg",
                        lambda arr, quality: b"\xff\xd8FAKEJPEG")

    jpeg_bytes, meta = cam.capture()
    assert jpeg_bytes == b"\xff\xd8FAKEJPEG"
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["jpeg_quality"] == 85
    assert meta["shutter_us"] == 16667


def test_capture_raises_camera_not_available_when_no_driver():
    cam = Camera(driver=None)
    with pytest.raises(CameraNotAvailable):
        cam.capture()

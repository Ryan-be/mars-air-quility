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
    """Mocks the picamera2 driver via its REAL API surface
    (capture_metadata, not metadata — the latter doesn't exist on
    Picamera2 and was the cause of an outage on first deployment)."""
    fake_drv = MagicMock(spec=["capture_array", "capture_metadata",
                                "camera_properties"])
    fake_drv.capture_array = MagicMock(return_value=MagicMock())
    fake_drv.camera_properties = {"PixelArraySize": (1920, 1080)}
    fake_drv.capture_metadata = MagicMock(
        return_value={"ExposureTime": 16667, "AnalogueGain": 1.0})

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


def test_capture_uses_capture_metadata_not_metadata(monkeypatch):
    """Pin the API: Camera.capture() calls capture_metadata() (the real
    picamera2 method), NEVER metadata() (which doesn't exist on the
    Picamera2 class). A previous version called .metadata() and crashed
    every snap-photo with AttributeError on real hardware. spec=
    on the MagicMock makes any access to the wrong name raise.
    """
    fake_drv = MagicMock(spec=["capture_array", "capture_metadata",
                                "camera_properties"])
    fake_drv.capture_array = MagicMock(return_value=MagicMock())
    fake_drv.camera_properties = {"PixelArraySize": (1920, 1080)}
    fake_drv.capture_metadata = MagicMock(return_value={})

    cam = Camera(driver=fake_drv)
    monkeypatch.setattr("mlss_grow.camera._encode_jpeg",
                        lambda arr, quality: b"\xff\xd8")
    cam.capture()
    # If capture() ever uses .metadata() instead of .capture_metadata(),
    # spec= would have raised AttributeError — getting here proves
    # the code calls the right method.
    fake_drv.capture_metadata.assert_called_once()


def test_capture_continues_when_capture_metadata_raises(monkeypatch):
    """If capture_metadata fails for ANY reason, the capture should
    succeed with empty metadata rather than crashing the whole
    snap-photo flow. Photo capture is the primary value; meta is
    nice-to-have for ML joins."""
    fake_drv = MagicMock(spec=["capture_array", "capture_metadata",
                                "camera_properties"])
    fake_drv.capture_array = MagicMock(return_value=MagicMock())
    fake_drv.camera_properties = {"PixelArraySize": (1920, 1080)}
    fake_drv.capture_metadata = MagicMock(side_effect=RuntimeError("driver bug"))

    cam = Camera(driver=fake_drv)
    monkeypatch.setattr("mlss_grow.camera._encode_jpeg",
                        lambda arr, quality: b"\xff\xd8")

    jpeg_bytes, meta = cam.capture()
    assert jpeg_bytes == b"\xff\xd8"
    # shutter_us / iso fall through to None / 100 from meta.get defaults
    assert meta["shutter_us"] is None
    assert meta["iso"] == 100


def test_capture_raises_camera_not_available_when_no_driver():
    cam = Camera(driver=None)
    with pytest.raises(CameraNotAvailable):
        cam.capture()

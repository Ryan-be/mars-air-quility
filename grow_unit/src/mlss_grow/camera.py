"""Pi camera capture wrapper using picamera2.

Detects on Camera.detect(), produces (jpeg_bytes, metadata) on .capture().
The metadata dict matches the JSON header expected by the WS photo frame
parser on the server side (see mlss_monitor.grow.photo_storage).
"""
import io
import logging

log = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2 as _Picamera2
    import picamera2 as _picamera2_module
except ImportError:
    _picamera2_module = None
    _Picamera2 = None

try:
    from PIL import Image
except ImportError:
    Image = None


class CameraNotAvailable(Exception):
    pass


def _encode_jpeg(array, quality: int) -> bytes:
    """Encode a numpy array (HxWx3 RGB) to JPEG bytes via Pillow."""
    if Image is None:
        raise RuntimeError("Pillow not installed — cannot encode JPEG")
    im = Image.fromarray(array)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class Camera:
    DEFAULT_QUALITY = 85

    def __init__(self, driver) -> None:
        self._driver = driver

    @classmethod
    def detect(cls) -> "Camera | None":
        if _picamera2_module is None:
            return None
        try:
            drv = _picamera2_module.Picamera2()
            config = drv.create_still_configuration()
            drv.configure(config)
            drv.start()
            return cls(driver=drv)
        except Exception as exc:
            log.warning("picamera2 init failed: %s", exc)
            return None

    def capture(self) -> tuple[bytes, dict]:
        if self._driver is None:
            raise CameraNotAvailable("camera driver not initialised")
        array = self._driver.capture_array()
        # picamera2's actual method is `capture_metadata()` — `metadata()`
        # alone doesn't exist on the Picamera2 class. Returns the most
        # recent frame's metadata; close enough to the array we just
        # captured for shutter/ISO logging purposes (a stricter
        # capture_request() round-trip would be needed to guarantee the
        # metadata is for THIS exact frame).
        try:
            meta = self._driver.capture_metadata()
        except Exception as exc:
            # Camera metadata is nice-to-have for ML training joins, not
            # critical for the basic capture-and-store flow. Log and
            # continue with empty metadata so a buggy picamera2 version
            # doesn't break photo capture entirely.
            log.warning("capture_metadata failed (%s) — continuing with empty meta", exc)
            meta = {}
        jpeg_bytes = _encode_jpeg(array, self.DEFAULT_QUALITY)
        width, height = self._driver.camera_properties.get(
            "PixelArraySize", (0, 0))
        return jpeg_bytes, {
            "width": width,
            "height": height,
            "jpeg_quality": self.DEFAULT_QUALITY,
            "shutter_us": meta.get("ExposureTime"),
            "iso": int(meta.get("AnalogueGain", 1) * 100),
        }

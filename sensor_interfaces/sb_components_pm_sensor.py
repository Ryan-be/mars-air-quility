import logging
import serial

log = logging.getLogger(__name__)

# PMS5003/PMSA003 frame constants
_START1 = 0x42
_START2 = 0x4D
_FRAME_LEN = 32


class AirMonitoringHAT_PM:
    """
    Interface for SB Components Air Monitoring HAT.
    Reads PM1.0, PM2.5, and PM10 values via UART (PMSA003 / PMS5003).

    Uses /dev/serial0 (hardware UART) — no I2C address conflict with other sensors.
    """

    def __init__(self, port="/dev/serial0", baudrate=9600, timeout=2):
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = None

    def _open(self):
        if self._ser is None or not self._ser.is_open:
            self._ser = serial.Serial(
                self._port, self._baudrate, timeout=self._timeout,
            )

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    def _sync_to_frame(self):
        """Scan the byte stream until we find the 0x42 0x4D start marker."""
        for _ in range(64):  # read up to 64 bytes looking for sync
            b = self._ser.read(1)
            if len(b) == 0:
                return False
            if b[0] == _START1:
                b2 = self._ser.read(1)
                if len(b2) == 0:
                    return False
                if b2[0] == _START2:
                    return True
        return False

    def _verify_checksum(self, frame):
        """Verify the PMS frame checksum (last 2 bytes = sum of all preceding bytes)."""
        expected = (frame[-2] << 8) | frame[-1]
        actual = sum(frame[:-2])
        return expected == actual

    def read_pm(self):
        """Read one PM frame. Returns dict with pm1_0, pm2_5, pm10 (ug/m3), or None."""
        try:
            self._open()
            self._ser.reset_input_buffer()

            if not self._sync_to_frame():
                log.warning("PM sensor: could not sync to frame start")
                return None

            # We already consumed the 2 start bytes; read the remaining 30
            remaining = self._ser.read(_FRAME_LEN - 2)
            if len(remaining) < _FRAME_LEN - 2:
                log.warning("PM sensor: incomplete frame (%d bytes)", len(remaining))
                return None

            # Reconstruct full frame for checksum
            frame = bytes([_START1, _START2]) + remaining

            if not self._verify_checksum(frame):
                log.warning("PM sensor: checksum mismatch")
                return None

            # Standard atmosphere values (bytes 10-15 in the frame)
            pm1_0 = (frame[10] << 8) | frame[11]
            pm2_5 = (frame[12] << 8) | frame[13]
            pm10 = (frame[14] << 8) | frame[15]

            return {"pm1_0": pm1_0, "pm2_5": pm2_5, "pm10": pm10}

        except serial.SerialException as e:
            log.error("PM sensor serial error: %s", e)
            self.close()
            return None
        except Exception as e:
            log.error("PM sensor unexpected error: %s", e)
            return None


# Module-level convenience (mirrors aht20.py / sgp30.py pattern)
_sensor = None


def init_pm_sensor(port="/dev/serial0"):
    global _sensor
    try:
        _sensor = AirMonitoringHAT_PM(port=port)
        # Do a test read to confirm sensor is responding
        result = _sensor.read_pm()
        if result is not None:
            log.info("PM sensor initialised: PM2.5=%d ug/m3", result["pm2_5"])
            return _sensor
        log.warning("PM sensor connected but no data yet (may need warm-up)")
        return _sensor
    except Exception as e:
        log.error("Failed to initialise PM sensor: %s", e)
        _sensor = None
        return None


def read_pm():
    if _sensor is None:
        return None
    return _sensor.read_pm()

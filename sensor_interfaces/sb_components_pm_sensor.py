import logging
import time
import serial
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

log = logging.getLogger(__name__)

# PMS5003/PMSA003 frame constants
_START1 = 0x42
_START2 = 0x4D
_FRAME_LEN = 32

# SB Components Air Monitoring HAT uses GPIO27 (BCM) as the SET/sleep pin.
# HIGH = active (sensor fan + laser on, streaming data)
# LOW  = sleep  (sensor powered down, no data)
_DEFAULT_SET_PIN = 27


class AirMonitoringHAT_PM:
    """
    Interface for SB Components Air Monitoring HAT.
    Reads PM1.0, PM2.5, and PM10 values via UART (PMSA003 / PMS5003).

    Uses /dev/serial0 (hardware UART) — no I2C address conflict with other sensors.
    The HAT's SET pin (GPIO27) must be held HIGH for the sensor to stream data.
    """

    def __init__(self, port="/dev/serial0", baudrate=9600, timeout=2,
                 set_pin=_DEFAULT_SET_PIN):
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout  # serial port hard read timeout (seconds)
        self._set_pin = set_pin
        self._ser = None
        self._consecutive_failures = 0
        # Per-instance backoff state (was module-level _skip_until).
        self._skip_until: float = 0.0
        # Per-instance single-worker executor (was module-level _pm_executor).
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pm_sensor")

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    def __del__(self):
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def _open(self):
        if self._ser is None or not self._ser.is_open:
            self._ser = serial.Serial(
                self._port, self._baudrate, timeout=self._timeout,
            )

    def _wake_sensor(self):
        """Pull the SET pin HIGH to wake the PMSA003 from sleep mode."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._set_pin, GPIO.OUT)
            GPIO.output(self._set_pin, GPIO.HIGH)
            log.info("PM sensor: SET pin (GPIO%d) pulled HIGH — waking sensor", self._set_pin)
            time.sleep(5)  # sensor needs ~2-3s to spin up fan and stabilise
        except ImportError:
            log.warning("RPi.GPIO not available — cannot control PM sensor SET pin")
        except Exception as e:
            log.error("PM sensor: failed to set wake pin GPIO%d: %s", self._set_pin, e)

    def _sync_to_frame(self):
        """Scan the byte stream until we find the 0x42 0x4D start marker.

        Uses a 96-byte window (3 full frames worth) to reliably catch the next
        frame boundary without flushing the buffer.
        """
        for _ in range(96):
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

    def _try_read_frame(self, attempt):
        """Single attempt to read one valid PM frame. Returns dict or None."""
        try:
            self._open()
            # Do NOT call reset_input_buffer() — flushing the buffer and then
            # waiting for the next 1 Hz frame is the root cause of intermittent
            # sync failures.  Instead, scan whatever bytes are already buffered.

            if not self._sync_to_frame():
                log.debug("PM sensor: no frame marker found (attempt %d)", attempt + 1)
                return None

            # We already consumed the 2 start bytes; read the remaining 30
            remaining = self._ser.read(_FRAME_LEN - 2)
            if len(remaining) < _FRAME_LEN - 2:
                log.debug("PM sensor: short frame %d bytes (attempt %d)",
                          len(remaining), attempt + 1)
                return None

            frame = bytes([_START1, _START2]) + remaining

            if not self._verify_checksum(frame):
                log.debug("PM sensor: checksum mismatch (attempt %d)", attempt + 1)
                return None

            # Standard atmosphere values (bytes 10-15 in the frame)
            pm1_0 = (frame[10] << 8) | frame[11]
            pm2_5 = (frame[12] << 8) | frame[13]
            pm10  = (frame[14] << 8) | frame[15]

            # PM1.0 <= PM2.5 <= PM10 is a physical requirement
            if not pm1_0 <= pm2_5 <= pm10:
                log.debug(
                    "PM sensor: rejected frame — PM values out of order (pm1=%s pm2.5=%s pm10=%s)",
                    pm1_0, pm2_5, pm10,
                )
                return None

            return {"pm1_0": pm1_0, "pm2_5": pm2_5, "pm10": pm10}

        except serial.SerialException as e:
            log.error("PM sensor serial error: %s", e)
            self.close()
            return None
        except Exception as e:
            log.error("PM sensor unexpected error: %s", e)
            return None

    def _do_read_attempt(self, attempt: int):
        """Run _try_read_frame in the per-instance executor with a hard wall-clock
        timeout so a stalled serial read never blocks the calling thread."""
        future = self._executor.submit(self._try_read_frame, attempt)
        try:
            return future.result(timeout=3.0)
        except FuturesTimeout:
            log.warning("PM sensor: read attempt %d timed out after 3s", attempt + 1)
            # Force-close the serial port so the stuck read unblocks.
            try:
                self.close()
            except Exception:
                pass
            return None

    def _handle_failure(self):
        """Apply backoff/retry logic after all read attempts for a cycle are exhausted."""
        self._consecutive_failures += 1
        log.warning("PM sensor: could not read a valid frame after 3 attempts "
                    "(consecutive failures: %d)", self._consecutive_failures)

        if self._consecutive_failures >= 10:
            self._skip_until = time.monotonic() + 60.0
            log.warning(
                "PM sensor: backing off for 60s after %d consecutive failures",
                self._consecutive_failures,
            )
            self._consecutive_failures = 0
        elif self._consecutive_failures >= 5:
            log.warning("PM sensor: %d consecutive failures — re-waking sensor",
                        self._consecutive_failures)
            self._wake_sensor()
            self._consecutive_failures = 0

    def read_pm(self):
        """Read one PM frame with up to 3 retries.

        Returns dict with pm1_0, pm2_5, pm10 (ug/m3), or None on failure.

        Consecutive-failure backoff:
          - After 5 failures: re-wake the sensor via GPIO.
          - After 10 failures: enter a 60-second cooldown to avoid hammering a
            broken serial port and blocking the sensor loop.
        """
        # Cooldown check — skip reads until the backoff timer expires.
        if time.monotonic() < self._skip_until:
            return None

        for attempt in range(3):
            if attempt == 1 and self._ser is not None and self._ser.is_open:
                # Second attempt: flush stale buffer (non-blocking, safe).
                try:
                    self._ser.reset_input_buffer()
                except Exception:
                    pass
            result = self._do_read_attempt(attempt)
            if result is not None:
                self._consecutive_failures = 0
                return result
            if attempt < 2:
                # Wait for the next 1 Hz frame — but only 1s, not 1.2s, to keep
                # the total worst-case read time under the caller's expectations.
                time.sleep(1.0)

        self._handle_failure()
        return None


# Module-level convenience (mirrors aht20.py / sgp30.py pattern)
_sensor = None


def init_pm_sensor(port="/dev/serial0", set_pin=_DEFAULT_SET_PIN):
    global _sensor
    try:
        _sensor = AirMonitoringHAT_PM(port=port, set_pin=set_pin)
        _sensor._wake_sensor()
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

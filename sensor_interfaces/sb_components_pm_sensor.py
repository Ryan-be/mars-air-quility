import logging
import threading
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
        # Background poller state — the serial read can block for up to ~11s
        # per failed cycle (3 attempts × 3s timeout + 2 × 1s sleep). Running
        # that on the sensor loop / log-data thread would stall the hot tier
        # and the SSE sensor_update broadcast, making the dashboard appear
        # frozen. The poller owns the blocking path; callers get cached
        # results instantly via get_cached_pm().
        self._cache_lock = threading.Lock()
        self._cached_result: dict | None = None
        self._cached_monotonic_ts: float = 0.0
        self._poller_thread: threading.Thread | None = None
        self._poller_stop = threading.Event()

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    def __del__(self):
        try:
            self._poller_stop.set()
        except Exception:
            pass
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass

    def start_poller(self, interval: float = 1.0) -> None:
        """Start the background polling thread if it isn't already running.

        The poller calls the blocking read_pm() every `interval` seconds (or
        as fast as possible if a read takes longer) and stores the latest
        successful frame in a lock-guarded cache that get_cached_pm() reads.
        """
        if self._poller_thread is not None and self._poller_thread.is_alive():
            return
        self._poller_stop.clear()
        self._poller_thread = threading.Thread(
            target=self._poll_loop,
            args=(interval,),
            daemon=True,
            name="pm_sensor_poller",
        )
        self._poller_thread.start()

    def stop_poller(self) -> None:
        """Signal the poller thread to exit (used from tests/shutdown)."""
        self._poller_stop.set()

    def restart_after_fork(self, interval: float = 1.0) -> None:
        """Rebuild thread/executor state after os.fork().

        With gunicorn's preload_app=True, __init__ runs in the master — the
        ThreadPoolExecutor's worker thread and the poller thread both start
        in the master and are left behind at fork(). The Event/Lock objects
        themselves are inherited and can be re-used, but the threads backing
        the executor are gone so any submit() would deadlock.

        Called from gunicorn.conf.py::post_fork. Safe to call multiple times
        (idempotent); also safe in non-forking contexts (tests) — it just
        replaces the executor and bounces the poller.
        """
        # Drop the stale executor (its worker thread is gone) and create a
        # new one in this process.
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pm_sensor"
        )
        # The inherited serial handle, if any, is also tied to the master's
        # blocking read. Close so the next read reopens in this process.
        try:
            self.close()
        except Exception:
            pass
        # Fresh poller lifecycle. The inherited poller_thread object refers
        # to a dead master thread; is_alive() returns False so start_poller()
        # would be idempotent, but null it out for clarity.
        self._poller_thread = None
        self._poller_stop = threading.Event()
        self.start_poller(interval=interval)

    def _poll_loop(self, interval: float) -> None:
        # Small initial delay so the first read doesn't collide with the
        # synchronous probe in init_pm_sensor().
        if self._poller_stop.wait(interval):
            return
        while not self._poller_stop.is_set():
            try:
                result = self.read_pm()
                if result is not None:
                    with self._cache_lock:
                        self._cached_result = result
                        self._cached_monotonic_ts = time.monotonic()
            except Exception as exc:  # pragma: no cover — defensive
                log.error("PM sensor poller: unexpected error: %s", exc)
            # Wait for the next cycle or a stop signal.
            if self._poller_stop.wait(interval):
                return

    def get_cached_pm(self, max_age: float | None = None) -> dict | None:
        """Return the most recent successful PM frame without blocking.

        `max_age` (seconds) — if provided, entries older than this are
        treated as expired and None is returned. None means no age limit.
        """
        with self._cache_lock:
            if self._cached_result is None:
                return None
            if max_age is not None and (
                time.monotonic() - self._cached_monotonic_ts
            ) > max_age:
                return None
            return dict(self._cached_result)

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
        # Do a test read to confirm sensor is responding. This probe is
        # deliberately blocking — it happens once at startup and its result
        # seeds the poller's cache so the first read_pm() call has data.
        result = _sensor.read_pm()
        if result is not None:
            log.info("PM sensor initialised: PM2.5=%d ug/m3", result["pm2_5"])
            # Seed the cache so get_cached_pm() returns data immediately.
            with _sensor._cache_lock:
                _sensor._cached_result = dict(result)
                _sensor._cached_monotonic_ts = time.monotonic()
        else:
            log.warning("PM sensor connected but no data yet (may need warm-up)")
        # Start the background poller so all subsequent callers get cached,
        # non-blocking reads via get_cached_pm().
        _sensor.start_poller(interval=1.0)
        return _sensor
    except Exception as e:
        log.error("Failed to initialise PM sensor: %s", e)
        _sensor = None
        return None


def read_pm():
    """Return the most recent cached PM frame, or None.

    This is non-blocking — the actual serial read happens on a dedicated
    poller thread inside AirMonitoringHAT_PM. Callers on the 1 Hz sensor
    loop and the 10 s log loop are never stalled by a failed serial read.
    """
    if _sensor is None:
        return None
    return _sensor.get_cached_pm()

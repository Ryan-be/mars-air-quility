"""systemd service entrypoint.

Boot sequence:
  1. Load /etc/mlss/grow.token if present → use existing credentials
  2. Otherwise read /boot/mlss-grow.yaml + enrol → save token + delete YAML
  3. Open WS to MLSS, start safety loop
  4. Run forever
"""
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from importlib.metadata import version, PackageNotFoundError

from mlss_grow.config import (
    load_firstboot_config, load_token, save_token,
)
from mlss_grow.enrol import enroll_unit, get_hardware_serial

log = logging.getLogger(__name__)

FIRSTBOOT_PATH = "/boot/mlss-grow.yaml"
TOKEN_PATH = "/etc/mlss/grow.token"

# Module-load monotonic timestamp. We use monotonic (not wall-clock) so
# uptime_s is immune to clock changes (NTP step, manual sysadmin set).
# Captured at import so all subsequent uptime calculations reference
# the same origin even if the service module is re-imported in tests.
_SERVICE_START_TIME = time.monotonic()


def _get_firmware_version() -> str:
    """Return the package version (mlss_grow) or "dev" if not installed.

    The systemd unit installs mlss_grow as a real package via pip so
    importlib.metadata.version returns whatever pyproject.toml advertises.
    Running out of a checkout (poetry shell, pytest) typically yields
    PackageNotFoundError; in that case "dev" is the right placeholder
    so the Diagnostics tab shows something useful rather than crashing.
    """
    try:
        return version("mlss_grow")
    except PackageNotFoundError:
        return "dev"


def _service_uptime_s() -> float:
    """Seconds since the service module was imported (monotonic)."""
    return time.monotonic() - _SERVICE_START_TIME


def _try_init_with_health(driver_factory, channel_name: str):
    """Run a driver factory inside a try/except; classify the outcome.

    Returns a tuple ``(driver_or_none, health_string)`` where:
      - ``health == "untested"`` if init succeeded — the driver is alive
        but hasn't been exercised yet (this is the right baseline for
        actuators: a working pHAT does NOT prove anything until we
        actually pulse the pump or toggle the relay)
      - ``health == "no_hardware"`` if init raised — the HAT/sensor isn't
        present or isn't responding on the I2C bus

    Used for actuators (pump, light) where "init succeeded" is the only
    cheap signal we have at boot. Sensors use ``_read_with_health`` which
    additionally requires a successful first read.
    """
    try:
        return driver_factory(), "untested"
    except Exception as exc:  # noqa: BLE001 — boot path; log + degrade
        log.warning(
            "init failed for %s: %s — capability will report no_hardware",
            channel_name, exc,
        )
        return None, "no_hardware"


def _read_with_health(sensor, channel_name: str):
    """Read a sensor once at boot to confirm it's actually reporting data.

    A sensor that detected on the bus but returns garbage (or nothing)
    isn't useful to the user; flag it as no_hardware so the UI can grey
    out the corresponding tile. Sensors are eligible for "connected"
    immediately because a successful first read IS the observation that
    proves the channel is alive (unlike actuators, where init success
    is necessary but not sufficient).

    Returns ``(reading_or_none, health_string)`` where reading is a dict
    on success, None otherwise.
    """
    try:
        reading = sensor.read()
    except Exception as exc:  # noqa: BLE001 — boot path; log + degrade
        log.warning(
            "first read failed for %s: %s — capability will report no_hardware",
            channel_name, exc,
        )
        return None, "no_hardware"
    if not reading:
        # Sensor.read() returns {} on bad reads (see SeesawSoilSensor).
        # Treat empty as no_hardware: same UX outcome from the user's
        # POV (no usable data) and we're past the point where the driver
        # might recover quickly.
        log.warning(
            "first read for %s returned no data — capability will report no_hardware",
            channel_name,
        )
        return None, "no_hardware"
    return reading, "connected"


# ─── Capability declaration helpers ──────────────────────────────────
# Required channels (per Channel enum + spec): every unit declares these
# four even when hardware is absent (health="no_hardware"), so the UI
# can render grey-out tiles consistently. Optional channels are only
# emitted when their sensor is actually present.
_REQUIRED_CHANNELS = ("soil_moisture", "light", "pump", "camera")

# Display unit per channel — surfaced as `Capability.unit_label` so the
# UI doesn't have to maintain its own lookup. Empty string for booleans
# (light/pump state) and the camera (no scalar value).
_CHANNEL_UNIT_LABELS = {
    "soil_moisture": "raw",
    "soil_temp_c": "°C",
    "ambient_lux": "lux",
    "air_temp_c": "°C",
    "air_humidity_pct": "%",
    "reservoir_level_pct": "%",
    "light": "",
    "pump": "",
    "camera": "",
}


def _build_capabilities(
    *, sensors: list, sensor_healths: dict, pump, pump_health: str,
    light, light_health: str, camera, camera_health: str,
    hardware_serial: str,
) -> list[dict]:
    """Build the per-channel capability list emitted in the boot frame.

    Walks the wired-up hardware + the health states recorded by
    `_try_init_with_health` / `_read_with_health` and produces one entry
    per channel the unit declares. Always emits the four REQUIRED
    channels (soil_moisture, light, pump, camera), even when their
    health is `no_hardware`, so the UI can render greyed tiles for
    missing required hardware. Optional sensor channels (soil_temp_c,
    ambient_lux, etc.) are only emitted when their sensor is present.

    Args:
        sensors: list of Sensor instances that successfully initialised
                 (init may have succeeded but read may have failed —
                 those carry health="no_hardware" via sensor_healths).
        sensor_healths: dict mapping sensor → health string. Sensors
                        that failed init are NOT in `sensors`; their
                        absence means each declared channel falls
                        through to `no_hardware`.
        pump / light / camera: driver instance or None.
        pump_health / light_health / camera_health: health string from
                        the matching `_try_init_with_health` call.
        hardware_serial: Pi's hardware serial (`/proc/cpuinfo`-derived).

    Returns:
        A list of dicts shaped to match `mlss_contracts.Capability`
        (channel / hardware / is_required / unit_label / details / health).
    """
    out: list[dict] = []
    seen_channels: set[str] = set()

    # Sensors — emit one entry per declared channel.
    for sensor in sensors:
        health = sensor_healths.get(id(sensor), "untested")
        for ch in sensor.channels():
            seen_channels.add(ch)
            details = None
            # Surface I2C address for sensors that expose one — handy for
            # ops debugging when a sensor stops responding ("which 0x36
            # device is misbehaving?"). The seesaw is the only one
            # currently emitting, but the pattern generalises.
            i2c_addr = getattr(sensor, "_driver", None)
            if i2c_addr is not None and hasattr(i2c_addr, "address"):
                try:
                    details = {"i2c_address": f"0x{i2c_addr.address:02x}"}
                except Exception:  # noqa: BLE001
                    details = None
            out.append({
                "channel": ch,
                "hardware": type(sensor).__name__,
                "is_required": ch in _REQUIRED_CHANNELS,
                "unit_label": _CHANNEL_UNIT_LABELS.get(ch, ""),
                "details": details,
                "health": health,
            })

    # Actuators — pump, light. Always emit (required channels), with
    # health "no_hardware" when init failed.
    out.append({
        "channel": "pump",
        "hardware": type(pump).__name__ if pump is not None else "AutomationPHATPump",
        "is_required": True,
        "unit_label": _CHANNEL_UNIT_LABELS["pump"],
        "details": None,
        "health": pump_health,
    })
    seen_channels.add("pump")

    out.append({
        "channel": "light",
        "hardware": type(light).__name__ if light is not None else "AutomationPHATLight",
        "is_required": True,
        "unit_label": _CHANNEL_UNIT_LABELS["light"],
        "details": None,
        "health": light_health,
    })
    seen_channels.add("light")

    # Camera — required, but unlike actuators "untested" doesn't quite
    # fit (a successfully-initialised picamera2 is functionally
    # equivalent to "connected" — the only failure mode beyond init is
    # the picamera2 daemon dying mid-capture, which would surface as a
    # photo-frame error not a capability state).
    out.append({
        "channel": "camera",
        "hardware": type(camera).__name__ if camera is not None else "Camera",
        "is_required": True,
        "unit_label": _CHANNEL_UNIT_LABELS["camera"],
        "details": None,
        "health": camera_health,
    })
    seen_channels.add("camera")

    # Required channels with no producer at all — emit a no_hardware
    # placeholder so the UI gets a row to grey out. Currently the only
    # required sensor channel is `soil_moisture`; if it didn't show up
    # in any wired sensor the unit has no soil sensor, which is the
    # camera-only first-deployment posture.
    for ch in _REQUIRED_CHANNELS:
        if ch in seen_channels:
            continue
        out.append({
            "channel": ch,
            "hardware": "unknown",
            "is_required": True,
            "unit_label": _CHANNEL_UNIT_LABELS.get(ch, ""),
            "details": None,
            "health": "no_hardware",
        })

    return out


@dataclass
class BootstrappedState:
    unit_id: int
    token: str
    mlss_host: str
    plant_name: str | None = None
    plant_type: str = "generic"
    medium: str = "soil"
    # Path to the pinned MLSS server cert (managed by install.sh). Carried
    # through here so _run_main_loop can hand it to WSClient for TLS
    # verification — see C2 fix in ws_client._build_ssl_context.
    server_cert_path: str = "/etc/mlss/server.crt"


def bootstrap_unit_state(
    firstboot_path: str = FIRSTBOOT_PATH,
    token_path: str = TOKEN_PATH,
    enroll_fn=enroll_unit,
    get_serial_fn=get_hardware_serial,
) -> BootstrappedState:
    """Decide credentials: existing token wins, else enrol."""
    existing = load_token(token_path)
    fb = load_firstboot_config(firstboot_path)

    if existing:
        unit_id, token = existing
        host = fb.mlss_host if fb else None
        if host is None:
            # Pull from /etc/mlss/grow.host, written at first save (added below)
            host_file = os.path.join(os.path.dirname(token_path), "grow.host")
            if os.path.exists(host_file):
                with open(host_file) as f:
                    host = f.read().strip()
            else:
                raise RuntimeError("token exists but mlss_host unknown")
        # Cert path: prefer the YAML override (if firstboot still around),
        # otherwise the documented default. The cert itself is managed by
        # install.sh; we just need to know where to look.
        cert_path = fb.server_cert_path if fb else "/etc/mlss/server.crt"
        return BootstrappedState(
            unit_id=unit_id, token=token, mlss_host=host,
            server_cert_path=cert_path,
        )

    if fb is None:
        raise RuntimeError("no firstboot config and no existing token — cannot enrol")

    serial = get_serial_fn()
    log.info("enrolling unit (hardware_serial=%s)", serial)
    unit_id, token = enroll_fn(fb, serial)
    save_token(token_path, unit_id, token)
    # Persist mlss_host alongside the token for future boots
    host_file = os.path.join(os.path.dirname(token_path), "grow.host")
    os.makedirs(os.path.dirname(host_file), exist_ok=True)
    with open(host_file, "w") as f:
        f.write(fb.mlss_host)
    # Delete firstboot YAML so the enrollment key doesn't persist on SD card
    try:
        os.remove(firstboot_path)
    except OSError as exc:
        log.warning("failed to remove %s: %s", firstboot_path, exc)
    return BootstrappedState(
        unit_id=unit_id, token=token, mlss_host=fb.mlss_host,
        plant_name=fb.plant_name, plant_type=fb.plant_type, medium=fb.medium,
        server_cert_path=fb.server_cert_path,
    )


def main() -> None:
    """systemd entrypoint. Bootstrap, then run forever."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    try:
        state = bootstrap_unit_state()
    except Exception as exc:
        log.error("bootstrap failed: %s", exc)
        sys.exit(1)

    log.info("bootstrapped unit_id=%s mlss_host=%s", state.unit_id, state.mlss_host)
    asyncio.run(_run_main_loop(state))


# Firmware default for buffer retention. Mirrors the server's
# `grow_default_buffer_retention_days` app_setting (currently "7"). Used
# when the per-unit override (grow_units.buffer_retention_days) is NULL
# in the server response — the buffer_retention_days_provider closure
# falls back to this value so prune always has something to work with.
_DEFAULT_BUFFER_RETENTION_DAYS = 7


def _build_reconnect_sync_and_retention(
    server_url: str, unit_id: int, token: str,
    server_cert_path: "str | None", loop_cfg,
) -> "tuple[Callable[[], None], Callable[[], int]]":
    """Build (reconnect_sync_callback, buffer_retention_days_provider).

    The reconnect-sync callback pulls + applies the latest unit config.
    Used by WSClient on every successful reconnect (after outbound
    buffer drain, before the receive loop) so config edits made while
    the unit was offline take effect on reconnect — not on the next
    online edit.

    The retention-days provider returns the latest buffer_retention_days
    pulled from the server, falling back to _DEFAULT_BUFFER_RETENTION_DAYS
    when the server hasn't been pulled yet (first connect) or returned
    NULL (no per-unit override). WSClient.run_forever calls it after
    on_reconnect_sync, so by the time prune runs the value reflects the
    pull that just landed.

    Both closures are paired here (rather than two independent factories)
    so they share the `latest_retention_days` cell — the sync callback
    is the writer, the provider is the reader. Avoids bloating loop_cfg
    with non-actuator state.

    Failures (network blip, server down, malformed response) in the sync
    callback are logged but do NOT raise — WSClient.run_forever's
    wrapper turns a raise into a tear-down warning, which is also OK,
    but logging-then-returning keeps the WS up so we still receive any
    config_changed push that follows.
    """
    from mlss_grow.config_sync import pull_unit_config, apply_config

    # Mutable cell holding the most recent retention value. List-of-one
    # rather than `nonlocal` because the inner closures are independent
    # functions and can't share a Python variable directly.
    latest_retention_days: list[int] = [_DEFAULT_BUFFER_RETENTION_DAYS]

    def _sync() -> None:
        try:
            unit_cfg = pull_unit_config(
                server_url, unit_id, token,
                server_cert_path=server_cert_path,
            )
            apply_config(unit_cfg, loop_cfg)
            # Update the retention cell BEFORE logging so any failure in
            # the log call below doesn't leave the cell stale.
            if unit_cfg.buffer_retention_days is not None:
                latest_retention_days[0] = unit_cfg.buffer_retention_days
            else:
                # Per-unit override cleared — revert to firmware default.
                latest_retention_days[0] = _DEFAULT_BUFFER_RETENTION_DAYS
            log.info(
                "reconnect: pulled fresh config and applied "
                "(phase=%s, plant_type=%s, buffer_retention_days=%s)",
                unit_cfg.current_phase, unit_cfg.plant_type,
                latest_retention_days[0],
            )
        except Exception as exc:
            log.warning(
                "reconnect-pull failed: %s — running stale config until "
                "next config_changed push",
                exc,
            )

    def _retention_provider() -> int:
        return latest_retention_days[0]

    return _sync, _retention_provider


def _build_reconnect_sync(server_url: str, unit_id: int, token: str,
                          server_cert_path: "str | None", loop_cfg) -> "Callable[[], None]":
    """Backwards-compat wrapper around _build_reconnect_sync_and_retention.

    Returns just the sync callback for callers that don't need the
    retention provider (currently only the legacy test harness — main
    runtime uses _build_reconnect_sync_and_retention directly).
    """
    sync, _ = _build_reconnect_sync_and_retention(
        server_url, unit_id, token, server_cert_path, loop_cfg,
    )
    return sync


async def _run_main_loop(state: BootstrappedState) -> None:
    """Wire up sensors, actuators, camera, WS client, safety loop. Run forever.

    Implementation deferred to integration task — this Phase 1 function is a
    skeleton that the integration test exercises end-to-end.
    """
    from mlss_grow.sensors import auto_detect
    from mlss_grow.actuators.automation_phat import AutomationPHATPump, AutomationPHATLight
    from mlss_grow.camera import Camera
    from mlss_grow.ws_client import WSClient
    from mlss_grow.photo_buffer import PhotoBuffer
    from mlss_grow.safety_loop import SafetyLoop, LoopConfig
    from mlss_grow.pid import PIDConfig
    from mlss_grow.light_schedule import parse_window
    from mlss_grow.dispatch import DispatchContext, dispatch_command
    from mlss_grow.safety_override import SafetyOverrideState
    import board
    import busio
    from datetime import datetime

    # Hardware init — each piece is independently fallible. A missing
    # I2C driver, an unwired pHAT, a disabled camera should NOT crash the
    # whole service; the sense-only-mode design says each capability
    # registers its own health (untested / no_hardware / connected) and
    # the UI greys out controls for hardware that didn't come up. The
    # firmware is happy to run with just a camera, just a soil sensor,
    # just actuators, or any subset.
    #
    # We use _try_init_with_health / _read_with_health here so each
    # piece carries a health string forward into the capabilities frame
    # — a refactor from the old pattern where init swallowed exceptions
    # silently and the UI couldn't tell the difference between "unwired
    # hardware" and "I just haven't tested this yet".
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as exc:
        log.warning("I2C bus init failed (%s) — sensors disabled. "
                    "Run `sudo raspi-config nonint do_i2c 0 && sudo reboot` "
                    "to enable I2C.", exc)
        i2c = None

    sensor_healths: dict = {}  # id(sensor) → "connected" | "no_hardware"
    if i2c is not None:
        try:
            detected = auto_detect(i2c)
        except Exception as exc:
            log.warning("sensor auto-detect failed: %s — no sensors active", exc)
            detected = []
    else:
        detected = []

    # Probe each detected sensor with a single read to confirm it's
    # actually emitting data. Sensors that fail the read are dropped
    # from the active list (so safety_loop doesn't include them in
    # telemetry) but tracked separately so capabilities still
    # surface "no_hardware" for their declared channels.
    sensors = []
    for sensor in detected:
        sensor_name = type(sensor).__name__
        _reading, health = _read_with_health(sensor, sensor_name)
        sensor_healths[id(sensor)] = health
        if health == "connected":
            sensors.append(sensor)
        # Sensors that failed the first read still show up in the
        # capabilities frame (so the UI knows the channel exists but
        # is dead) — keep them in `detected` for the capabilities
        # builder. They're NOT added to `sensors` so the safety_loop
        # never tries to call .read() on them again.
    detected_for_caps = detected  # pass to _build_capabilities

    pump, pump_health = _try_init_with_health(AutomationPHATPump, "pump")
    light, light_health = _try_init_with_health(AutomationPHATLight, "light")
    # Camera.detect returns None on no hardware (instead of raising), so
    # adapt: treat None as the "no_hardware" health, an instance as
    # "connected" (picamera2 init has already succeeded if we got an
    # instance back).
    try:
        camera = Camera.detect()
        camera_health = "connected" if camera is not None else "no_hardware"
    except Exception as exc:  # noqa: BLE001
        log.warning("camera detect failed: %s — photos disabled", exc)
        camera = None
        camera_health = "no_hardware"

    # Default config until MLSS sends an explicit one. The first
    # `config_changed` push (or the first explicit pull) replaces the
    # PID/light/calibration state in place via apply_config.
    loop_cfg = LoopConfig(
        light_windows=[parse_window("06:00", "22:00")],
        pid=PIDConfig(target_pct=55),
    )

    # The HTTPS base URL for config pulls. pull_unit_config appends
    # /api/grow/units/<id>/config itself, so we pass just the scheme +
    # host + port. Note: the WS uses port 5001, the HTTPS API uses 5000
    # — same host, different services. Mirrors DispatchContext.server_url
    # below so the on_reconnect_pull and the config_changed dispatcher
    # both hit the same endpoint.
    https_base_url = f"https://{state.mlss_host}:5000"

    received_commands = asyncio.Queue()
    # Build paired sync + retention closures so the WS prune call always
    # sees the freshest buffer_retention_days pulled from the server. The
    # sync closure writes the cell; the retention provider reads it. See
    # _build_reconnect_sync_and_retention docstring for shared-state notes.
    on_reconnect_sync, retention_provider = _build_reconnect_sync_and_retention(
        server_url=https_base_url,
        unit_id=state.unit_id,
        token=state.token,
        server_cert_path=state.server_cert_path,
        loop_cfg=loop_cfg,
    )
    # Disk-backed photo buffer: keeps photos taken while the WS is down,
    # uploaded oldest-first by ws_client._replay_photos on the next
    # successful reconnect. Reverses the C2 deferral that dropped photos
    # outright. Path overridable via env var so test/dev runs don't
    # collide with the production /var/lib path. Defaults match
    # photo_buffer.py: 1GB byte cap + 7-day age prune.
    photo_buffer = PhotoBuffer(
        root_dir=os.environ.get(
            "MLSS_GROW_PHOTO_BUFFER_DIR",
            "/var/lib/mlss-grow/photos",
        ),
    )
    ws = WSClient(
        url=f"wss://{state.mlss_host}:5001/api/grow/{state.unit_id}/ws",
        token=state.token,
        buffer_db_path="/var/lib/mlss-grow/buffer.sqlite",
        on_command=received_commands.put_nowait,
        server_cert_path=state.server_cert_path,
        on_reconnect_sync=on_reconnect_sync,
        buffer_retention_days_provider=retention_provider,
        photo_buffer=photo_buffer,
    )

    # Build + emit the capabilities frame ONCE at boot. send_text falls
    # through to the local buffer when the WS isn't connected yet, so
    # the frame is held + replayed on first connection. The server's
    # handle_capabilities is idempotent (DELETE+INSERT), so re-emits
    # don't duplicate rows. Without this frame, every downstream UI
    # (Live readings tiles, sensor sanity panel, health pills,
    # firmware_version) renders empty even when telemetry is streaming.
    capabilities_payload = {
        "capabilities": _build_capabilities(
            sensors=detected_for_caps,
            sensor_healths=sensor_healths,
            pump=pump, pump_health=pump_health,
            light=light, light_health=light_health,
            camera=camera, camera_health=camera_health,
            hardware_serial=get_hardware_serial(),
        ),
        "firmware_version": _get_firmware_version(),
        "hardware_serial": get_hardware_serial(),
        "uptime_s": _service_uptime_s(),
    }
    log.info("emitting capabilities frame: %d channels (%s)",
             len(capabilities_payload["capabilities"]),
             ", ".join(c["channel"] for c in capabilities_payload["capabilities"]))
    await ws.send_text("capabilities", datetime.utcnow(), capabilities_payload)

    # Shared state between dispatcher (writer) and safety loop (reader)
    # for safety_override. The safety loop polls
    # override_state.consume_skip_next_soak each tick.
    override_state = SafetyOverrideState()

    async def emit(kind: str, payload: dict):
        if kind == "photo":
            await ws.send_photo(payload["meta"], payload["jpeg_bytes"])
        else:
            await ws.send_text(kind, datetime.utcnow(), payload)

    safety = SafetyLoop(
        sensors=sensors, pump=pump, light=light, camera=camera,
        config=loop_cfg,
        emit=lambda k, p: asyncio.run_coroutine_threadsafe(
            emit(k, p), asyncio.get_event_loop()),
        # Phase 3 diagnostics: every telemetry frame carries uptime +
        # buffer_size. The buffer is owned by the WSClient (which we
        # constructed above) — pass through so safety_loop can call
        # .size() each tick.
        uptime_provider=_service_uptime_s,
        buffer=ws._buffer,
        # Pre-Phase-4 audit fix (Flow 3 #1): wire the shared override
        # state so the safety loop actually consumes the
        # `skip_next_soak` flag the dispatcher sets. Pre-fix this was
        # a dead branch — admin clicks set the flag but no reader.
        override_state=override_state,
    )

    # Bundle of state the dispatcher needs. Reuses https_base_url so
    # config_changed pushes and the on_reconnect_sync pull hit the same
    # endpoint; pull_unit_config appends /api/grow/units/<id>/config
    # itself. Phase 3 Task 4: pass the buffer through so the
    # `clear_buffer` command (Diagnostics tab Danger Zone) can empty it.
    dispatch_ctx = DispatchContext(
        unit_id=state.unit_id,
        server_url=https_base_url,
        token=state.token,
        server_cert_path=state.server_cert_path,
        pump=pump, light=light, camera=camera,
        loop_cfg=loop_cfg, ws=ws,
        override_state=override_state,
        buffer=ws._buffer,
    )

    async def safety_ticker():
        while True:
            try:
                safety.tick()
            except Exception as exc:
                log.exception("safety tick failed: %s", exc)
            await asyncio.sleep(30)

    async def command_handler():
        while True:
            cmd = await received_commands.get()
            await dispatch_command(cmd, dispatch_ctx)

    async def watchdog_pinger():
        """Ping systemd's watchdog so it doesn't SIGABRT us.

        The systemd unit declares WatchdogSec=30, meaning systemd
        expects a `WATCHDOG=1` notification at least every 30s. If we
        miss it (e.g. async loop wedged, hardware deadlock), systemd
        kills us — that's the safety property the watchdog provides.
        We ping every 10s so transient slowness doesn't trigger a
        kill.

        Dependency-free: writes to the AF_UNIX socket path in
        $NOTIFY_SOCKET via stdlib `socket`. If the env var isn't
        set (e.g. running outside systemd, or via `python -m` for
        dev), this is a no-op. Errors are swallowed — the firmware
        should keep running even if notify is broken.
        """
        notify_socket_path = os.environ.get("NOTIFY_SOCKET")
        if not notify_socket_path:
            log.info("NOTIFY_SOCKET not set — watchdog ping disabled "
                     "(running outside systemd?)")
            return
        # Linux abstract sockets start with @ — translate to NUL prefix.
        if notify_socket_path.startswith("@"):
            notify_socket_path = "\0" + notify_socket_path[1:]
        log.info("watchdog ping enabled — notifying systemd every 10s")
        try:
            import socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            # Initial READY=1 lets systemd know boot completed.
            try:
                sock.sendto(b"READY=1", notify_socket_path)
            except OSError as exc:
                log.warning("READY=1 notify failed: %s", exc)
            while True:
                try:
                    sock.sendto(b"WATCHDOG=1", notify_socket_path)
                except OSError as exc:
                    log.warning("WATCHDOG=1 notify failed: %s", exc)
                await asyncio.sleep(10)
        except Exception as exc:
            log.warning("watchdog_pinger setup failed: %s", exc)

    await asyncio.gather(
        ws.run_forever(),
        safety_ticker(),
        command_handler(),
        watchdog_pinger(),
    )


if __name__ == "__main__":
    main()

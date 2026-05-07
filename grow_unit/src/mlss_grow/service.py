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
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
    except Exception as exc:
        log.warning("I2C bus init failed (%s) — sensors disabled. "
                    "Run `sudo raspi-config nonint do_i2c 0 && sudo reboot` "
                    "to enable I2C.", exc)
        i2c = None

    if i2c is not None:
        try:
            sensors = auto_detect(i2c)
        except Exception as exc:
            log.warning("sensor auto-detect failed: %s — no sensors active", exc)
            sensors = []
    else:
        sensors = []

    try:
        pump = AutomationPHATPump()
    except Exception as exc:
        log.warning("pump init failed: %s — pump unavailable", exc)
        pump = None

    try:
        light = AutomationPHATLight()
    except Exception as exc:
        log.warning("light init failed: %s — light unavailable", exc)
        light = None

    try:
        camera = Camera.detect()
    except Exception as exc:
        log.warning("camera init failed: %s — photos disabled", exc)
        camera = None

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

    await asyncio.gather(
        ws.run_forever(),
        safety_ticker(),
        command_handler(),
    )


if __name__ == "__main__":
    main()

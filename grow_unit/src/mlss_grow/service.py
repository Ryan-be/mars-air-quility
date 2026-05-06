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
from dataclasses import dataclass

from mlss_grow.config import (
    load_firstboot_config, load_token, save_token, FirstbootConfig,
)
from mlss_grow.enrol import enroll_unit, get_hardware_serial

log = logging.getLogger(__name__)

FIRSTBOOT_PATH = "/boot/mlss-grow.yaml"
TOKEN_PATH = "/etc/mlss/grow.token"


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


async def _run_main_loop(state: BootstrappedState) -> None:
    """Wire up sensors, actuators, camera, WS client, safety loop. Run forever.

    Implementation deferred to integration task — this Phase 1 function is a
    skeleton that the integration test exercises end-to-end.
    """
    from mlss_grow.sensors import auto_detect
    from mlss_grow.actuators.automation_phat import AutomationPHATPump, AutomationPHATLight
    from mlss_grow.camera import Camera
    from mlss_grow.ws_client import WSClient
    from mlss_grow.safety_loop import SafetyLoop, LoopConfig
    from mlss_grow.pid import PIDConfig
    from mlss_grow.light_schedule import parse_window
    import board, busio
    from datetime import datetime

    i2c = busio.I2C(board.SCL, board.SDA)
    sensors = auto_detect(i2c)
    pump = AutomationPHATPump()
    light = AutomationPHATLight()
    camera = Camera.detect()

    received_commands = asyncio.Queue()
    ws = WSClient(
        url=f"wss://{state.mlss_host}:5001/api/grow/{state.unit_id}/ws",
        token=state.token,
        buffer_db_path="/var/lib/mlss-grow/buffer.sqlite",
        on_command=lambda cmd: received_commands.put_nowait(cmd),
        server_cert_path=state.server_cert_path,
    )

    # Default config until MLSS sends an explicit one
    loop_cfg = LoopConfig(
        light_windows=[parse_window("06:00", "22:00")],
        pid=PIDConfig(target_pct=55),
    )

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
            try:
                if cmd["name"] == "identify":
                    light.blink_pattern(duration_s=cmd.get("args", {}).get("duration_s", 10))
                elif cmd["name"] == "water_now":
                    pump.pulse(cmd.get("args", {}).get("duration_s", 5))
                elif cmd["name"] == "snap_photo" and camera:
                    jpeg, meta = camera.capture()
                    meta["taken_at"] = datetime.utcnow().isoformat() + "Z"
                    await ws.send_photo(meta, jpeg)
            except Exception as exc:
                log.exception("command failed: %s", exc)

    await asyncio.gather(
        ws.run_forever(),
        safety_ticker(),
        command_handler(),
    )


if __name__ == "__main__":
    main()

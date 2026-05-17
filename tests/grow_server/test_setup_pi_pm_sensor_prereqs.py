"""scripts/setup_pi.sh: must enable UART and add the operator to dialout group.

The MLSS server reads PM data from the SB Components Air Monitoring HAT
over the hardware UART at /dev/serial0. Two prerequisites both needed
manual operator intervention before this fix:

  (a) serial hardware enabled in raspi-config (documented in the readme
      "UART setup for PM sensor" section, but never automated)
  (b) the operator's user in the `dialout` group (wasn't documented at
      all — /dev/serial0 is root:dialout 660 so the service user needs
      group membership to open it)

On a fresh install, missing either gives the same opaque symptom: EACCES
floods in `journalctl -u mlss-monitor` and zero PM telemetry. We bake
both into setup_pi.sh so the surprise-on-first-use trap goes away.

Companion tests:
  * test_setup_pi_ffmpeg.py — ffmpeg in the apt list (same pattern)
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup_pi.sh"


def test_setup_pi_enables_uart_hardware():
    """The script must enable the hardware UART, either via raspi-config
    (`do_serial 2` = console off + hardware on) or by appending
    `enable_uart=1` to /boot config. Either signal is acceptable; both
    end up with /dev/serial0 functional after reboot."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "do_serial" in text or "enable_uart=1" in text, (
        "scripts/setup_pi.sh must enable the hardware UART so the PM "
        "sensor can read /dev/serial0. Without this, the PM sensor "
        "reader hits EACCES on first boot and the journal fills with "
        "'could not open port /dev/serial0' errors."
    )


def test_setup_pi_adds_user_to_dialout():
    """The user running mlss-monitor needs `dialout` group membership
    to open /dev/serial0. We must call `usermod -aG dialout` somewhere
    in the script."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "dialout" in text and "usermod" in text, (
        "scripts/setup_pi.sh must add the operator to the dialout group "
        "(typically: `sudo usermod -aG dialout $USER`). Without this, "
        "the PM sensor reader hits EACCES on /dev/serial0 — independent "
        "of whether the UART hardware is enabled."
    )

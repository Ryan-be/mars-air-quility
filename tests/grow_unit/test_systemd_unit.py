"""systemd unit file is well-formed and references the right entrypoint."""
import os
import re
from pathlib import Path


_UNIT_PATH = Path(__file__).resolve().parent.parent.parent / "grow_unit" / "systemd" / "mlss-grow.service"


def test_unit_file_exists():
    assert _UNIT_PATH.exists()


def test_unit_has_required_sections():
    content = _UNIT_PATH.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content


def test_unit_uses_mlss_grow_entrypoint():
    content = _UNIT_PATH.read_text()
    assert "mlss-grow" in content or "mlss_grow.service" in content


def test_unit_runs_as_dedicated_user():
    content = _UNIT_PATH.read_text()
    assert re.search(r"User=mlss-grow", content)


def test_unit_has_systemd_watchdog():
    content = _UNIT_PATH.read_text()
    assert "WatchdogSec=" in content


def test_unit_restart_on_failure():
    content = _UNIT_PATH.read_text()
    assert re.search(r"Restart=on-failure|Restart=always", content)


def test_unit_targets_multi_user():
    content = _UNIT_PATH.read_text()
    assert "WantedBy=multi-user.target" in content

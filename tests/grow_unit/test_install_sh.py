"""install.sh syntactic checks + critical commands present."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = REPO_ROOT / "grow_unit" / "install.sh"


def test_install_script_exists():
    assert INSTALL.exists()


def test_install_script_is_executable():
    assert os.access(INSTALL, os.X_OK)


def test_install_script_starts_with_strict_mode():
    content = INSTALL.read_text()
    assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content


def test_install_script_creates_mlss_grow_user():
    content = INSTALL.read_text()
    assert "useradd" in content or "adduser" in content
    assert "mlss-grow" in content


def test_install_script_downloads_wheels_from_mlss():
    content = INSTALL.read_text()
    assert "/api/grow/dist/" in content
    assert "mlss_grow" in content
    assert "mlss_contracts" in content


def test_install_script_creates_systemd_unit():
    content = INSTALL.read_text()
    assert "/etc/systemd/system/mlss-grow.service" in content
    assert "systemctl enable" in content
    assert "systemctl start" in content


def test_install_script_creates_required_directories():
    content = INSTALL.read_text()
    for d in ["/opt/mlss-grow", "/etc/mlss", "/var/lib/mlss-grow", "/var/log/mlss-grow"]:
        assert d in content


def test_install_script_passes_shellcheck_when_available():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, f"shellcheck:\n{r.stdout}\n{r.stderr}"

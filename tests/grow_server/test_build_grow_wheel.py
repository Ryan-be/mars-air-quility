"""build_grow_wheel.sh produces both wheels and copies them to static/grow_dist."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_grow_wheel.sh"
DIST_DIR = REPO_ROOT / "static" / "grow_dist"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "script must be chmod +x"


def test_script_passes_shellcheck_when_available():
    """If shellcheck is installed, the script must lint clean."""
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, f"shellcheck failures:\n{r.stdout}\n{r.stderr}"


def test_script_starts_with_proper_bash_strict_mode():
    content = SCRIPT.read_text()
    assert content.startswith("#!/bin/bash") or content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content


@pytest.mark.slow
def test_script_produces_wheels(tmp_path, monkeypatch):
    """End-to-end: run the script, expect both wheels in static/grow_dist."""
    if shutil.which("poetry") is None:
        pytest.skip("poetry not installed in this environment")
    # Clean dist
    if DIST_DIR.exists():
        for f in DIST_DIR.glob("*.whl"):
            f.unlink()
    r = subprocess.run([str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"build failed:\n{r.stdout}\n{r.stderr}"
    wheels = list(DIST_DIR.glob("*.whl"))
    pkgs = {w.name.split("-")[0] for w in wheels}
    assert "mlss_grow" in pkgs or "mlss-grow" in pkgs
    assert "mlss_contracts" in pkgs or "mlss-contracts" in pkgs

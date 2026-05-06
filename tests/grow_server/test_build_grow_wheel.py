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


# ---------------------------------------------------------------------------
# I5 — build_grow_wheel.sh must also copy the systemd unit so install.sh can
# fetch it from the dist endpoint with SHA256 verification.
# ---------------------------------------------------------------------------

def test_build_script_copies_systemd_service_to_dist():
    """The script must reference grow_unit/systemd/mlss-grow.service and
    copy it into static/grow_dist/ alongside the wheels.

    Source-level check (no poetry needed, no wheel build needed) — we only
    care that the script *intends* to copy the file. The .slow end-to-end
    test below actually runs it.
    """
    content = SCRIPT.read_text()
    # Reference to the systemd unit in grow_unit/systemd/.
    assert "mlss-grow.service" in content, (
        "build_grow_wheel.sh must copy mlss-grow.service into the dist dir "
        "so install.sh can fetch it via /api/grow/dist/"
    )
    # And it must land in the dist dir.
    # Either via DIST_DIR shell var or a literal path that ends in grow_dist.
    assert (
        "$DIST_DIR" in content or "grow_dist" in content
    ), "build_grow_wheel.sh must copy into the dist dir"


@pytest.mark.slow
def test_script_copies_systemd_unit_into_dist_dir(tmp_path):
    """End-to-end: after running the build, the .service file is in dist."""
    if shutil.which("poetry") is None:
        pytest.skip("poetry not installed in this environment")
    # Clean dist
    if DIST_DIR.exists():
        for f in DIST_DIR.iterdir():
            if f.is_file():
                f.unlink()
    r = subprocess.run([str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, f"build failed:\n{r.stdout}\n{r.stderr}"
    service_in_dist = DIST_DIR / "mlss-grow.service"
    assert service_in_dist.exists(), (
        f"build did not place mlss-grow.service in {DIST_DIR}; contents = "
        f"{[p.name for p in DIST_DIR.iterdir()]}"
    )
    # And the contents match the source-of-truth file under grow_unit/systemd/.
    src = REPO_ROOT / "grow_unit" / "systemd" / "mlss-grow.service"
    assert service_in_dist.read_bytes() == src.read_bytes()

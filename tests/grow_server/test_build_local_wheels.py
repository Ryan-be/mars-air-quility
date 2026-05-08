"""build_local_wheels.sh produces both wheels in dist/wheels/ for offline
/ private install (Pi SD-card image baking, ad-hoc local install).

Distinct from build_grow_wheel.sh — that one ships into static/grow_dist/
for the MLSS HTTP server to serve. This one is for direct file-system
consumption (no MLSS server in the loop).
"""
import os
import shutil
import subprocess
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build_local_wheels.sh"
DIST_DIR = REPO_ROOT / "dist" / "wheels"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists()
    if os.name == "posix":
        # Windows working trees can't reliably represent the +x bit; skip
        # the assertion there. Same posture as test_pi_image_build.py.
        assert os.access(SCRIPT, os.X_OK), "script must be chmod +x"


def test_script_passes_shellcheck_when_available():
    """If shellcheck is installed, the script must lint clean."""
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(
        ["shellcheck", str(SCRIPT)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"shellcheck failures:\n{r.stdout}\n{r.stderr}"
    )


def test_script_starts_with_proper_bash_strict_mode():
    content = SCRIPT.read_text()
    assert (
        content.startswith("#!/bin/bash")
        or content.startswith("#!/usr/bin/env bash")
    )
    assert "set -euo pipefail" in content


def test_script_targets_dist_wheels_not_static_grow_dist():
    """The local-wheels output dir must be dist/wheels — distinct from
    build_grow_wheel.sh's static/grow_dist destination. Mixing the two
    would defeat the purpose of having a separate offline builder."""
    content = SCRIPT.read_text()
    # The DIST_DIR shell variable must point at dist/wheels.
    assert 'DIST_DIR="$REPO_ROOT/dist/wheels"' in content, (
        "build_local_wheels.sh must write to dist/wheels (not "
        "static/grow_dist — that's build_grow_wheel.sh's destination)"
    )


def test_script_strips_pathdep_from_grow_wheel():
    """poetry-built mlss_grow has a path-baked Requires-Dist URL that
    breaks `pip install` on a different host. The local-wheels script
    must call _strip_pathdep.py to fix it before shipping."""
    content = SCRIPT.read_text()
    assert "_strip_pathdep.py" in content


def test_script_does_not_publish_to_pypi():
    """We are NOT publishing to PyPI. Guard against a regression that
    re-introduces twine upload or similar in this script."""
    content = SCRIPT.read_text()
    assert "twine" not in content
    assert "PYPI_API_TOKEN" not in content


@pytest.mark.slow
def test_script_produces_wheels():
    """End-to-end: run the script, expect both wheels in dist/wheels."""
    if shutil.which("poetry") is None:
        pytest.skip("poetry not installed in this environment")
    if DIST_DIR.exists():
        for f in DIST_DIR.glob("*.whl"):
            f.unlink()
    r = subprocess.run(
        [str(SCRIPT)], cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, f"build failed:\n{r.stdout}\n{r.stderr}"
    wheels = list(DIST_DIR.glob("*.whl"))
    pkgs = {w.name.split("-")[0] for w in wheels}
    assert "mlss_grow" in pkgs or "mlss-grow" in pkgs
    assert "mlss_contracts" in pkgs or "mlss-contracts" in pkgs

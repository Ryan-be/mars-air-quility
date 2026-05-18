"""Phase 4 #3 — pi-image build script + stage definitions.

We don't actually run pi-gen in CI (Linux-only, ~30min build time, requires
sudo + binfmt_misc). These tests just confirm the build artifacts exist,
are shell-safe (shellcheck), and contain the expected hooks.
"""
import os
import shutil
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_pi_image.sh"
STAGE_DIR = REPO_ROOT / "scripts" / "stage-mlss-grow"
SUBSTAGE_DIR = STAGE_DIR / "00-install-mlss-grow"


def test_build_script_exists_and_is_executable():
    assert BUILD_SCRIPT.exists()
    # On Windows we can't reliably check the executable bit because
    # git stores it in the index but the working tree's NTFS doesn't
    # carry it. Skip the os.access check there.
    if os.name == "posix":
        assert os.access(BUILD_SCRIPT, os.X_OK), \
            f"{BUILD_SCRIPT} should be chmod +x"


def test_build_script_is_bash_strict_mode():
    content = BUILD_SCRIPT.read_text()
    assert content.startswith("#!/bin/bash")
    assert "set -euo pipefail" in content


def test_build_script_passes_shellcheck():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    r = subprocess.run(
        ["shellcheck", str(BUILD_SCRIPT)],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, (
        f"shellcheck failures in {BUILD_SCRIPT}:\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )


def test_stage_dir_layout():
    """pi-gen's stage layout: prerun.sh + numbered substage dirs."""
    assert (STAGE_DIR / "prerun.sh").exists()
    assert SUBSTAGE_DIR.is_dir()
    # The substage must have an apt package list and a chroot run script.
    assert (SUBSTAGE_DIR / "00-packages").exists(), (
        "substage must list apt packages in 00-packages"
    )
    assert (SUBSTAGE_DIR / "01-run-chroot.sh").exists(), (
        "substage must have 01-run-chroot.sh (the script that runs in the chroot)"
    )
    assert (SUBSTAGE_DIR / "01-run.sh").exists(), (
        "substage must have 01-run.sh (host-side; copies files into rootfs)"
    )


def test_apt_package_list_contains_mlss_essentials():
    """The packages we ship in the image — essential for grow-unit
    operation. i2c-tools is needed for the Seesaw soil sensor,
    libcamera-apps + python3-picamera2 for the camera capture path."""
    pkg_list = (SUBSTAGE_DIR / "00-packages").read_text().splitlines()
    pkg_set = {line.strip() for line in pkg_list if line.strip()}
    expected = {
        "python3", "python3-pip", "python3-venv",
        "python3-picamera2", "libcamera-apps",
        "i2c-tools",
    }
    missing = expected - pkg_set
    assert not missing, f"apt list is missing essentials: {missing}"


def test_apt_package_list_does_not_contain_ffmpeg():
    """ffmpeg must NOT be in the grow-unit SD image.

    Time-lapse video rendering happens server-side on the MLSS box
    (mlss_monitor/grow/timelapse_jobs.py), NOT on the grow unit. The
    grow-unit firmware only captures still JPEGs (camera.py) and
    uploads them via the WS protocol — it never shells out to ffmpeg.

    Keeping ffmpeg out of the grow-unit image saves ~30 MB on every
    SD card we flash and removes a dependency surface that doesn't
    earn its keep. If you find yourself wanting to add ffmpeg here,
    first add a reference from grow_unit/ that actually uses it —
    otherwise this assertion is locking in the right answer.
    """
    pkg_list = (SUBSTAGE_DIR / "00-packages").read_text().splitlines()
    pkg_set = {line.strip() for line in pkg_list if line.strip()}
    assert "ffmpeg" not in pkg_set, (
        "ffmpeg is in the grow-unit SD image's apt list but the "
        "grow-unit firmware doesn't render video (it only captures "
        "JPEGs and uploads them — see grow_unit/src/mlss_grow/camera.py). "
        "Remove it from scripts/stage-mlss-grow/00-install-mlss-grow/00-packages."
    )


def test_chroot_script_pip_installs_mlss_grow():
    """The in-chroot script must pip-install mlss-grow into the venv."""
    content = (SUBSTAGE_DIR / "01-run-chroot.sh").read_text()
    assert "/opt/mlss-grow/.venv" in content
    assert "pip install" in content
    assert "mlss-grow" in content
    # piwheels for ARM wheels — without it Pillow + cryptography compile
    # from source on the Pi (very slow, can fail).
    assert "piwheels" in content


def test_chroot_script_installs_from_local_wheels_not_pypi():
    """We are NOT publishing mlss-grow to PyPI — the image bakes the
    locally-built wheel in instead. Guard against a regression that
    re-introduces a PyPI fetch for our own packages."""
    content = (SUBSTAGE_DIR / "01-run-chroot.sh").read_text()
    # The chroot script must point pip at the staged wheels dir.
    assert "/tmp/wheels" in content, (
        "chroot script must --find-links /tmp/wheels (where the host-side "
        "stage script staged the locally-built wheels)"
    )
    assert "--find-links" in content


def test_host_stage_copies_local_wheels_into_rootfs():
    """The host-side stage must stage wheels from MLSS_LOCAL_WHEELS_DIR
    into the rootfs. Without this step, the chroot script's
    --find-links /tmp/wheels would point at an empty directory."""
    content = (SUBSTAGE_DIR / "01-run.sh").read_text()
    assert "MLSS_LOCAL_WHEELS_DIR" in content
    assert "/tmp/wheels" in content
    assert "mlss_grow" in content
    assert "mlss_contracts" in content


def test_chroot_script_creates_mlss_grow_user():
    content = (SUBSTAGE_DIR / "01-run-chroot.sh").read_text()
    assert "adduser" in content
    assert "mlss-grow" in content


def test_chroot_script_drops_systemd_unit_but_does_not_enable():
    content = (SUBSTAGE_DIR / "01-run-chroot.sh").read_text()
    # Drop the unit to /etc/systemd/system/...
    assert "/etc/systemd/system/mlss-grow.service" in content
    # systemctl enable is NOT in the chroot script — firstboot.sh does
    # that, and only after the operator has dropped a yaml.
    assert "systemctl enable" not in content


def test_firstboot_script_is_idempotent_with_marker_file():
    """The firstboot hook must self-mark complete and short-circuit
    on subsequent boots."""
    content = (
        SUBSTAGE_DIR / "files" / "firstboot.sh"
    ).read_text()
    assert ".firstboot-done" in content
    # Touches the marker once setup completes
    assert "touch" in content
    # Enables + starts the service when the yaml is present
    assert "systemctl enable mlss-grow.service" in content
    assert "systemctl start mlss-grow.service" in content
    # Bails (with a useful message) when the yaml is absent
    assert "/boot/mlss-grow.yaml" in content


def test_yaml_template_has_required_fields():
    """The template the operator copies + edits should call out every
    field the firmware needs — anything missing here means the operator
    has to grep the firmware to figure out what's required."""
    content = (
        SUBSTAGE_DIR / "files" / "mlss-grow.yaml.template"
    ).read_text()
    # Required (must be uncommented + filled in)
    assert "mlss_host:" in content
    assert "enrollment_key:" in content
    # Helpful default
    assert "mlss_port:" in content


def test_systemd_unit_is_the_real_one_from_grow_unit():
    """The systemd unit shipped in the image must match the one in
    grow_unit/systemd/. If they drift (e.g. a security hardening flag
    is added in one but not the other), the image will silently ship
    a stale unit."""
    image_unit = (
        SUBSTAGE_DIR / "files" / "mlss-grow.service"
    ).read_text()
    real_unit = (REPO_ROOT / "grow_unit" / "systemd" / "mlss-grow.service").read_text()
    assert image_unit == real_unit, (
        "The image stage's mlss-grow.service has drifted from "
        "grow_unit/systemd/mlss-grow.service. Re-copy it."
    )


def test_doc_exists_and_calls_out_linux_only():
    """docs/PI_IMAGE_BUILD.md must exist and explicitly say it's
    Linux-only — operators on macOS / Windows need to know up-front."""
    doc = (REPO_ROOT / "docs" / "PI_IMAGE_BUILD.md").read_text()
    assert "Linux-only" in doc or "Linux box" in doc
    assert "pi-gen" in doc
    # Cross-link to the release process (versioning + local wheel build
    # happen there).
    assert "RELEASE_PROCESS.md" in doc


def test_build_pi_image_calls_local_wheel_builder():
    """build_pi_image.sh must call build_local_wheels.sh so the wheels
    are present before pi-gen runs the stage. Without this the stage
    script fails fast with 'wheel directory not found'."""
    content = (REPO_ROOT / "scripts" / "build_pi_image.sh").read_text()
    assert "build_local_wheels.sh" in content, (
        "build_pi_image.sh must invoke build_local_wheels.sh before pi-gen"
    )
    # And it must export the wheels dir into pi-gen's config so the
    # stage scripts can find it.
    assert "MLSS_LOCAL_WHEELS_DIR" in content


def test_no_pypi_publish_workflows_exist():
    """The maintainer decided not to publish to PyPI. Guard against a
    regression that re-introduces auto-publish workflows on tag push."""
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    if not workflows_dir.exists():
        return  # nothing to assert
    for wf in workflows_dir.glob("*.yml"):
        text = wf.read_text()
        # If a workflow references twine upload OR PYPI_API_TOKEN it's
        # almost certainly trying to publish to PyPI, which we don't do.
        assert "twine upload" not in text, (
            f"{wf.name} contains 'twine upload' — this repo does not "
            "publish to PyPI. See docs/RELEASE_PROCESS.md."
        )
        assert "PYPI_API_TOKEN" not in text, (
            f"{wf.name} references PYPI_API_TOKEN — this repo does not "
            "publish to PyPI. See docs/RELEASE_PROCESS.md."
        )

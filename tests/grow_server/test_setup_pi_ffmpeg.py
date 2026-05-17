"""scripts/setup_pi.sh: must apt-install ffmpeg.

The MLSS server renders grow-unit time-lapses by shelling out to
ffmpeg (see ``mlss_monitor/grow/timelapse_jobs.py``). On a fresh
MLSS install the operator runs ``bash scripts/setup_pi.sh`` once
to set the box up; if ffmpeg isn't in the apt list there, the
first time someone clicks "Generate" on the History tab the POST
endpoint returns ``503 ffmpeg_not_installed`` and the operator
has to chase down ``sudo apt install ffmpeg`` separately.

This test locks ffmpeg into the setup-script's apt list so a
future refactor (e.g. splitting the apt block, sorting it) can't
silently drop it and reintroduce the surprise-on-first-use trap.

Companion tests:
  * ``test_deploy_ffmpeg_warning.py`` — bin/deploy keeps the
    runtime warning when ffmpeg is missing on an existing box.
  * ``test_pi_image_build.py::test_apt_package_list_contains_mlss_essentials``
    — the grow-unit SD image's apt list (separate path).
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup_pi.sh"


def test_setup_pi_apt_list_contains_ffmpeg():
    """ffmpeg must be in the apt-get install list in setup_pi.sh.

    We check for ``ffmpeg`` on its own line inside the apt-get
    install block (rather than just substring-anywhere) so a stray
    mention in a comment can't accidentally satisfy the assertion.
    """
    text = SETUP_SCRIPT.read_text(encoding="utf-8")

    # Find the apt-get install block and assert ffmpeg is one of
    # the listed packages (one package per line, leading whitespace
    # + optional trailing backslash for line continuation).
    in_apt_block = False
    found_ffmpeg = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("sudo apt-get install") or line.startswith("apt-get install"):
            in_apt_block = True
            continue
        if in_apt_block:
            # Block ends at the first line that isn't a package
            # (i.e. doesn't end with backslash AND isn't a bare
            # package name). The simplest signal: blank line, or
            # a line starting with a recognised shell keyword/comment.
            if not line or line.startswith("#") or line.startswith("success") \
                    or line.startswith("info") or line.startswith("echo"):
                break
            # Strip trailing backslash for continuation lines.
            pkg = line.rstrip("\\").strip()
            if pkg == "ffmpeg":
                found_ffmpeg = True
                break

    assert found_ffmpeg, (
        "scripts/setup_pi.sh apt-get install block must include ffmpeg "
        "(required for grow-unit time-lapse rendering). Without it, the "
        "first timelapse generation on a fresh install fails with "
        "503 ffmpeg_not_installed."
    )

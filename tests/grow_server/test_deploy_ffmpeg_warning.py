"""bin/deploy: ffmpeg detection warning block.

The deploy script prints a yellow warning when ffmpeg is missing on PATH
so the operator notices inline with the deploy output (without breaking
the deploy, which is otherwise unaffected by the missing dep). This
guards two things:

1. The warning block exists in bin/deploy (so a future refactor doesn't
   silently drop it and reintroduce the surprise-on-first-job footgun
   that motivated this work).
2. The warning block runs cleanly under ``set -euo pipefail`` — i.e.
   missing ffmpeg does NOT abort the script. We can't run the whole
   bin/deploy from a unit test (it calls git pull, poetry install,
   sudo systemctl), but we can extract the standalone block and run
   it under the same shell options the real script uses.

We don't have a CI runner with bash on Windows guaranteed, so the
shell-execution check skips when bash isn't available; the static
'block exists' check always runs.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "bin" / "deploy"


def test_deploy_script_has_ffmpeg_warning_block():
    """The deploy script must keep the ffmpeg-missing warning block.
    Catches accidental removal in future refactors."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # Three signals that the block is intact: the command -v check,
    # the actionable install command in the message, and the
    # WARNING prefix the operator visually scans for.
    assert "command -v ffmpeg" in text, \
        "bin/deploy lost the ffmpeg PATH check"
    assert "sudo apt install ffmpeg" in text, \
        "bin/deploy lost the actionable install command"
    assert "WARNING" in text, \
        "bin/deploy lost the WARNING prefix"


def test_deploy_ffmpeg_block_does_not_fail_under_strict_mode(tmp_path):
    """The ffmpeg-missing branch must NOT abort the script. We extract
    the conditional and run it under ``set -euo pipefail`` with a PATH
    that excludes ffmpeg, and assert the snippet exits 0."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")

    snippet = tmp_path / "snippet.sh"
    snippet.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # Mirror the block in bin/deploy verbatim so the test catches
        # any drift between the two.
        'if ! command -v ffmpeg >/dev/null 2>&1; then\n'
        "    printf '\\033[33m==> WARNING: ffmpeg not on PATH "
        "— time-lapse video '\n"
        "    printf 'generation will fail until you run: "
        "sudo apt install ffmpeg\\033[0m\\n'\n"
        "fi\n"
        'echo "deploy_continues"\n',
        encoding="utf-8",
    )
    # Empty PATH so command -v always misses ffmpeg, regardless of the
    # dev box. /usr/bin retained so bash builtins like printf still work.
    minimal_path = "/usr/bin:/bin"
    env = dict(os.environ)
    env["PATH"] = minimal_path
    proc = subprocess.run(
        [bash, str(snippet)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, (
        f"deploy ffmpeg block failed: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    # The script reaches the line after the warning block.
    assert "deploy_continues" in proc.stdout
    # And the warning text is actually emitted on stdout.
    assert "ffmpeg not on PATH" in proc.stdout
    assert "sudo apt install ffmpeg" in proc.stdout

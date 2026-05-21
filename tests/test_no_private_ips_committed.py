"""Privacy guard - fail the build if any RFC 1918 private IP is
committed to the tracked tree.

Spec section 12 (privacy guarantees): all example IPs in code, tests,
and docs use 192.0.2.0/24 (RFC 5737 documentation range). This test
pins the invariant in CI so a future inadvertent paste of a real LAN
IP gets caught at PR time.
"""
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


# Match RFC 1918 private IPs:
#   10.0.0.0/8         -> 10.<x>.<x>.<x>
#   172.16.0.0/12      -> 172.<16-31>.<x>.<x>
#   192.168.0.0/16     -> 192.168.<x>.<x>
#
# Negative lookbehind/lookahead anchor on dot boundaries so we don't
# match Python version strings like "3.10.4" or path fragments like
# ".10.0.0". The IP is captured as group 1 for the error message.
_PRIVATE_IP_RE = re.compile(
    r"(?<![0-9.])"
    r"("
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")"
    r"(?![0-9.])"
)

# Files where private IPs are expected (none today, but this list is
# the place to add them if a future feature genuinely needs a literal
# IP committed). Each entry is a path relative to REPO_ROOT, using
# forward slashes on every platform (git ls-files output is normalised).
_ALLOWLIST = frozenset({
    # e.g. "docs/some-runbook.md",
})

# Binary / vendored / generated files we never scan. These would either
# never contain real IPs OR are too large to scan usefully.
_SKIP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".otf",
    ".whl", ".tar", ".zip", ".gz", ".bz2", ".xz",
    ".sqlite", ".db",
    ".pyc", ".pyo",
    # poetry.lock is huge and pins URLs/hashes only - no private IPs
    "poetry.lock",
    "package-lock.json",
)


def _tracked_files() -> list[str]:
    """All git-tracked text-ish files (best-effort binary exclusion)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=str(REPO_ROOT),
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available — privacy CI scan cannot run")
    return [
        line for line in out.splitlines()
        if not any(line.endswith(s) for s in _SKIP_SUFFIXES)
    ]


@pytest.mark.parametrize("rel_path", _tracked_files())
def test_no_private_ip_in_file(rel_path):
    if rel_path in _ALLOWLIST:
        return
    path = REPO_ROOT / rel_path
    try:
        content = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        # Binary or unreadable — skip silently.
        return
    match = _PRIVATE_IP_RE.search(content)
    assert match is None, (
        f"Private LAN IP '{match.group(1)}' found in {rel_path}. "
        f"Use 192.0.2.0/24 (RFC 5737) for documentation examples, "
        f"or add the file to _ALLOWLIST in this test if the IP is "
        f"genuinely required."
    )

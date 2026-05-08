"""Phase 4 #4 — pypi-release prep script.

scripts/prepare_pypi_release.py rewrites path-dependencies in a
poetry pyproject.toml to versioned deps so PyPI accepts the upload.
The "prepare" mode mutates the file and stashes the original; "restore"
swaps the original back.

These tests exercise the script directly (subprocess) to confirm the
round-trip is idempotent and the rewrite hits the expected line.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "prepare_pypi_release.py"


SAMPLE_PYPROJECT = """[tool.poetry]
name = "mlss-grow"
version = "0.1.0"
description = "Plant Grow Unit firmware"
authors = ["MLSS"]

[tool.poetry.dependencies]
python = ">=3.11,<4.0"
websockets = "^12.0"
mlss-contracts = {path = "../contracts", develop = true}
requests = "^2.31"
"""


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Write a minimal grow_unit-style pyproject.toml into a temp dir."""
    p = tmp_path / "pyproject.toml"
    p.write_text(SAMPLE_PYPROJECT, encoding="utf-8")
    return p


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


def test_prepare_replaces_path_dep_with_versioned_dep(sample_project):
    """prepare rewrites the path-dep line; backup is created."""
    r = _run(str(sample_project), "prepare")
    assert r.returncode == 0, r.stderr

    rewritten = sample_project.read_text(encoding="utf-8")
    assert 'mlss-contracts = "^0.1.0"' in rewritten
    assert 'path = "../contracts"' not in rewritten

    backup = sample_project.with_suffix(".toml.bak")
    assert backup.exists(), "backup must be written"
    assert "path = \"../contracts\"" in backup.read_text(encoding="utf-8")


def test_restore_swaps_backup_back(sample_project):
    """prepare → restore returns the original file verbatim."""
    original = sample_project.read_text(encoding="utf-8")
    _run(str(sample_project), "prepare")
    r = _run(str(sample_project), "restore")
    assert r.returncode == 0, r.stderr

    assert sample_project.read_text(encoding="utf-8") == original
    assert not sample_project.with_suffix(".toml.bak").exists()


def test_restore_without_backup_errors(sample_project):
    """restore should fail loudly if no backup exists — silent no-op
    would mask a forgotten prepare step in CI."""
    r = _run(str(sample_project), "restore")
    assert r.returncode != 0
    assert "no backup" in r.stderr.lower()


def test_prepare_refuses_to_clobber_existing_backup(sample_project):
    """Two prepare calls in a row would silently lose the original — the
    second prepare's backup would itself be the rewritten file. Refuse."""
    _run(str(sample_project), "prepare")
    r = _run(str(sample_project), "prepare")
    assert r.returncode != 0
    assert "backup" in r.stderr.lower()


def test_prepare_no_op_on_pyproject_with_no_path_deps(tmp_path):
    """A pyproject.toml with no rewritable deps should report no-op
    and not leave a backup behind (would otherwise confuse a later
    restore)."""
    p = tmp_path / "pyproject.toml"
    p.write_text(
        """[tool.poetry]
name = "boring"
version = "0.1.0"

[tool.poetry.dependencies]
python = ">=3.11"
""",
        encoding="utf-8",
    )
    r = _run(str(p), "prepare")
    assert r.returncode == 0
    assert "no path-deps" in r.stdout.lower()
    assert not p.with_suffix(".toml.bak").exists()


def test_prepare_actual_grow_unit_pyproject_round_trip(tmp_path):
    """Smoke test: copy the real grow_unit/pyproject.toml into a temp
    dir, prepare → restore, confirm bytes are identical. Catches
    regressions where the regex stops matching the real file (e.g. a
    spacing change inside the path-dep dict)."""
    real = REPO_ROOT / "grow_unit" / "pyproject.toml"
    if not real.exists():
        pytest.skip("grow_unit/pyproject.toml not in repo")

    sandbox = tmp_path / "pyproject.toml"
    shutil.copy(real, sandbox)
    original = sandbox.read_text(encoding="utf-8")

    r1 = _run(str(sandbox), "prepare")
    assert r1.returncode == 0, r1.stderr
    rewritten = sandbox.read_text(encoding="utf-8")
    assert "path = \"../contracts\"" not in rewritten, (
        "real grow_unit/pyproject.toml's path-dep didn't get rewritten — "
        "did the regex stop matching the real file's spacing?"
    )

    r2 = _run(str(sandbox), "restore")
    assert r2.returncode == 0, r2.stderr
    assert sandbox.read_text(encoding="utf-8") == original

#!/usr/bin/env python3
"""Rewrite path-dependencies in a poetry pyproject.toml for PyPI release.

In-tree development uses path-dependencies (e.g. mlss-grow points at
../contracts) so changes to mlss-contracts are immediately visible in
mlss-grow without a publish step. PyPI rejects path-deps in published
metadata, so before `poetry build` we have to flip those entries to a
versioned dep that pip can resolve from the public index.

Usage:
    python scripts/prepare_pypi_release.py grow_unit/pyproject.toml prepare
    poetry build
    python scripts/prepare_pypi_release.py grow_unit/pyproject.toml restore

The "prepare" mode rewrites the file and stashes the original next to it
as ``pyproject.toml.bak``. The "restore" mode swaps the backup back so
in-tree dev can resume.

Today the only dep that needs rewriting is mlss-contracts in
grow_unit/pyproject.toml. The mapping (path → versioned dep) is hard-
coded in PATH_TO_PYPI rather than parsed from the TOML so the script
is trivially auditable — adding a new package later is a one-line
change here.

The rewrite is line-based regex (poetry ships its own TOML serialiser
that adds noise — round-tripping through it would diff comments and
field ordering. The line we touch is unambiguous in the project's
pyproject files, so a targeted regex is the lowest-blast-radius
approach).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# Mapping: path-dep → PyPI dep. Each tuple = (regex matching the path-dep
# line, replacement text). The regex is anchored at line-start to avoid
# matching nested dep lines (e.g. inside a comment block); the replacement
# preserves the package name.
PATH_TO_PYPI = [
    (
        re.compile(
            r'^mlss-contracts\s*=\s*\{\s*path\s*=\s*"\.\./contracts"[^}]*\}\s*$',
            re.MULTILINE,
        ),
        # Caret bound: pulls 0.1.x, blocks 0.2.x — semver compatible release
        # window. Bump in lockstep with the mlss-contracts release tag.
        'mlss-contracts = "^0.1.0"',
    ),
]


def prepare(pyproject: Path) -> None:
    """Rewrite path-deps to versioned deps in-place, save a backup."""
    text = pyproject.read_text(encoding="utf-8")
    backup = pyproject.with_suffix(pyproject.suffix + ".bak")
    if backup.exists():
        raise SystemExit(
            f"refusing to overwrite existing backup at {backup} — "
            "did a previous prepare run not get restored?"
        )
    backup.write_text(text, encoding="utf-8")

    rewrites = 0
    for pattern, replacement in PATH_TO_PYPI:
        text, n = pattern.subn(replacement, text)
        rewrites += n

    if rewrites == 0:
        # No path-deps matched. Either we're already in PyPI shape
        # (someone re-ran prepare without restore) or the deps moved.
        # Either way, drop the backup so the next restore doesn't
        # spuriously revert real changes.
        backup.unlink()
        print(f"prepare: {pyproject} has no path-deps to rewrite — no-op")
        return

    pyproject.write_text(text, encoding="utf-8")
    print(f"prepare: rewrote {rewrites} path-dep line(s) in {pyproject}")
    print(f"         backup at {backup}")


def restore(pyproject: Path) -> None:
    """Swap the backup back over the rewritten file."""
    backup = pyproject.with_suffix(pyproject.suffix + ".bak")
    if not backup.exists():
        raise SystemExit(
            f"no backup at {backup} — did prepare ever run? "
            "(run prepare before restore, or skip restore entirely)"
        )
    shutil.move(str(backup), str(pyproject))
    print(f"restore: swapped {backup} back over {pyproject}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("pyproject", type=Path, help="path to a pyproject.toml")
    parser.add_argument("mode", choices=("prepare", "restore"))
    args = parser.parse_args(argv)
    if not args.pyproject.exists():
        raise SystemExit(f"file does not exist: {args.pyproject}")
    if args.mode == "prepare":
        prepare(args.pyproject)
    else:
        restore(args.pyproject)
    return 0


if __name__ == "__main__":
    sys.exit(main())

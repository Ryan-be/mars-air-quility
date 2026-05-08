#!/bin/bash
# Build mlss_contracts + mlss_grow wheels into dist/wheels/ for offline /
# private use — local installs, Pi SD-card image baking, anything that
# wants the firmware without going through a public package index.
#
# Output: dist/wheels/mlss_contracts-*.whl + mlss_grow-*.whl (both
# patched so the path-dep that poetry would otherwise bake into
# mlss_grow's METADATA is replaced with a normal version constraint).
#
# Usage:
#   bash scripts/build_local_wheels.sh
#
# After the script finishes, install offline with:
#   pip install --no-index --find-links dist/wheels mlss-grow
#
# The script is intentionally network-free at build time — it only runs
# `poetry build` on each package. Poetry's build backend doesn't reach
# the network for a normal wheel build (it reads pyproject.toml and
# packages the local sources). Transitive deps for end users still come
# from PyPI when the wheels are *installed*, but that's a separate
# concern from this build step.
#
# Run from the repo root, or any cwd — the script self-locates via its
# own dir.
#
# Sister script: scripts/build_grow_wheel.sh produces the same wheels
# but copies them to static/grow_dist/ so the MLSS HTTP server can
# serve them to Pi Zeros via install.sh. This script is the
# offline-equivalent: dist/wheels/ is meant for direct file-system
# consumption (e.g. baked into the Pi image at /tmp/wheels and
# pip-installed there with --no-index).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist/wheels"

cd "$REPO_ROOT"

mkdir -p "$DIST_DIR"
# Clean stale wheels — only keep the latest of each package.
rm -f "$DIST_DIR"/*.whl

echo "==> Building mlss_contracts wheel"
( cd "$REPO_ROOT/contracts" && poetry build -f wheel )
cp "$REPO_ROOT/contracts/dist"/*.whl "$DIST_DIR/"

echo "==> Building mlss_grow wheel"
( cd "$REPO_ROOT/grow_unit" && poetry build -f wheel )
cp "$REPO_ROOT/grow_unit/dist"/*.whl "$DIST_DIR/"

# poetry build of a package with a `{path=..., develop=true}` dep bakes
# the absolute path of the build host into the wheel's METADATA. That
# fails on a different host during `pip install` ("No such file or
# directory: /home/<build-user>/.../contracts"). Strip the path-dep +
# replace with a normal version constraint so pip resolves
# mlss-contracts from --find-links instead.
echo "==> Stripping path-dep from mlss_grow wheel METADATA"
python3 "$SCRIPT_DIR/_strip_pathdep.py" "$DIST_DIR"/mlss_grow-*.whl

echo "==> Files in $DIST_DIR:"
ls -la "$DIST_DIR"/

echo ""
echo "Wheels ready for offline install. Example:"
echo "  pip install --no-index --find-links \"$DIST_DIR\" mlss-grow"

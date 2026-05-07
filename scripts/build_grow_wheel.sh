#!/bin/bash
# Build mlss_contracts + mlss_grow wheels and copy them to static/grow_dist/
# so the MLSS HTTP server can serve them to Pi Zeros.
#
# Run from the repo root, or any cwd — the script self-locates via its own dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$REPO_ROOT/static/grow_dist"

cd "$REPO_ROOT"

mkdir -p "$DIST_DIR"
# Clean stale wheels — only keep the latest of each package
rm -f "$DIST_DIR"/*.whl

echo "==> Building mlss_contracts wheel"
( cd "$REPO_ROOT/contracts" && poetry build -f wheel )
cp "$REPO_ROOT/contracts/dist"/*.whl "$DIST_DIR/"

echo "==> Building mlss_grow wheel"
( cd "$REPO_ROOT/grow_unit" && poetry build -f wheel )
cp "$REPO_ROOT/grow_unit/dist"/*.whl "$DIST_DIR/"

# poetry build of a package with a {path=..., develop=true} dep bakes
# the absolute path of the build host into the wheel's METADATA. That
# fails on the Pi during pip install ("No such file or directory:
# /home/<build-user>/.../contracts"). Strip the path-dep + replace with
# a normal version constraint so pip resolves mlss-contracts from
# --find-links (where the contracts wheel also lives) instead.
echo "==> Stripping path-dep from mlss_grow wheel METADATA"
python3 "$SCRIPT_DIR/_strip_pathdep.py" "$DIST_DIR"/mlss_grow-*.whl

# The systemd unit isn't packaged in the wheel; ship it through the dist
# endpoint so install.sh can fetch + SHA256-verify it the same way it does
# for the wheels (defends against LAN MITM substituting a hostile unit).
echo "==> Copying systemd unit to dist"
cp "$REPO_ROOT/grow_unit/systemd/mlss-grow.service" "$DIST_DIR/"

echo "==> Files in $DIST_DIR:"
ls -la "$DIST_DIR"/

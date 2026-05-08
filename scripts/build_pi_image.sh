#!/bin/bash
# Build a custom Raspberry Pi OS .img.xz pre-loaded with mlss-grow.
#
# Wraps the official pi-gen tool (https://github.com/RPi-Distro/pi-gen)
# with our stage-mlss-grow customisations: bakes locally-built mlss-grow
# + mlss-contracts wheels into the image, drops the systemd unit, hooks
# rc.local so a yaml on the boot partition triggers automatic enrolment.
#
# Output: dist/mlss-pi-os-<version>.img.xz
#
# Usage:
#   bash scripts/build_pi_image.sh
#
# The script first runs scripts/build_local_wheels.sh to produce both
# wheels in dist/wheels/, then bakes them into the pi-gen stage. No
# external package index is consulted at provision time for our two
# packages (transitive deps still come from PyPI / piwheels — see
# 01-run-chroot.sh comments).
#
# Linux-only: pi-gen uses chroot + binfmt_misc which Windows / macOS
# can't run. On a non-Linux machine, run this inside a Linux VM or
# on a native Ubuntu / Debian box.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
WORK_DIR="${REPO_ROOT}/dist/pi-image-build"
PI_GEN_DIR="${WORK_DIR}/pi-gen"
STAGE_NAME="stage-mlss-grow"
STAGE_DIR_SRC="${REPO_ROOT}/scripts/${STAGE_NAME}"
WHEELS_DIR="${REPO_ROOT}/dist/wheels"

# Pin the pi-gen branch we've tested against. Bookworm = Debian 12, the
# stable target as of mid-2026. Bullseye is older and still supported
# by pi-gen but the stage scripts assume Bookworm package names.
PI_GEN_BRANCH="${PI_GEN_BRANCH:-bookworm}"

# Image release tag — appears in the output filename.
IMAGE_VERSION="${IMAGE_VERSION:-0.1.0}"

# ── Pre-flight checks ──────────────────────────────────────────────

if [[ "$(uname)" != "Linux" ]]; then
    echo "Error: pi-gen requires Linux (chroot + binfmt_misc)." >&2
    echo "       Run inside a Linux VM or on a native Ubuntu/Debian box." >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is required" >&2
    exit 1
fi
if [[ "$EUID" -ne 0 ]]; then
    echo "Note: pi-gen will run sudo internally; you may be prompted." >&2
fi

# ── Build local wheels ─────────────────────────────────────────────
#
# Run the local wheel builder first so the stage script can bake
# fresh wheels into the image. This is intentionally ahead of the
# pi-gen clone so we fail fast if poetry isn't installed or a build
# fails — saves the operator a 30-minute pi-gen run for nothing.

echo "==> Building local wheels (mlss_grow + mlss_contracts)"
bash "${SCRIPT_DIR}/build_local_wheels.sh"

if ! ls "${WHEELS_DIR}"/mlss_grow-*.whl >/dev/null 2>&1; then
    echo "Error: build_local_wheels.sh did not produce a mlss_grow wheel." >&2
    exit 1
fi
if ! ls "${WHEELS_DIR}"/mlss_contracts-*.whl >/dev/null 2>&1; then
    echo "Error: build_local_wheels.sh did not produce a mlss_contracts wheel." >&2
    exit 1
fi

# ── Clone or update pi-gen ─────────────────────────────────────────

mkdir -p "${WORK_DIR}"
if [[ ! -d "${PI_GEN_DIR}" ]]; then
    echo "==> Cloning pi-gen (${PI_GEN_BRANCH} branch)..."
    git clone --depth 1 --branch "${PI_GEN_BRANCH}" \
        https://github.com/RPi-Distro/pi-gen.git "${PI_GEN_DIR}"
else
    echo "==> pi-gen already cloned at ${PI_GEN_DIR}; pulling latest"
    git -C "${PI_GEN_DIR}" fetch --depth 1 origin "${PI_GEN_BRANCH}"
    git -C "${PI_GEN_DIR}" reset --hard "origin/${PI_GEN_BRANCH}"
fi

# ── Drop in our stage ───────────────────────────────────────────────
#
# pi-gen expects each stage as a directory at its root. We symlink
# our stage in (so edits flow without copying) and leave the upstream
# stages 0/1/2 alone — those produce the lite Pi OS we layer on.

if [[ ! -d "${STAGE_DIR_SRC}" ]]; then
    echo "Error: stage source not found at ${STAGE_DIR_SRC}" >&2
    exit 1
fi
ln -sfn "${STAGE_DIR_SRC}" "${PI_GEN_DIR}/${STAGE_NAME}"

# Skip stages 3,4,5 (full desktop) — we only want lite.
touch "${PI_GEN_DIR}/stage3/SKIP" "${PI_GEN_DIR}/stage3/SKIP_IMAGES"
touch "${PI_GEN_DIR}/stage4/SKIP" "${PI_GEN_DIR}/stage4/SKIP_IMAGES"
touch "${PI_GEN_DIR}/stage5/SKIP" "${PI_GEN_DIR}/stage5/SKIP_IMAGES"

# ── Build config ───────────────────────────────────────────────────

cat > "${PI_GEN_DIR}/config" <<EOF
IMG_NAME="mlss-pi-os"
RELEASE="${PI_GEN_BRANCH}"
DEPLOY_COMPRESSION="xz"
LOCALE_DEFAULT="en_GB.UTF-8"
TARGET_HOSTNAME="mlss-grow"
KEYBOARD_KEYMAP="gb"
KEYBOARD_LAYOUT="English (UK)"
TIMEZONE_DEFAULT="Etc/UTC"
# Pre-create a default user so the image is bootable without first-boot
# wizard. Operator should change the password on first login.
FIRST_USER_NAME="mlss"
FIRST_USER_PASS="mlss-grow-default-CHANGE-ME"
DISABLE_FIRST_BOOT_USER_RENAME=1
# Pass-through for our stage scripts — points 01-run.sh at the
# locally-built wheels so they can be staged into the rootfs.
MLSS_LOCAL_WHEELS_DIR="${WHEELS_DIR}"
EOF

# ── Build ──────────────────────────────────────────────────────────

echo "==> Running pi-gen build (this can take 30-60 minutes)..."
cd "${PI_GEN_DIR}"
./build.sh

# ── Collect output ─────────────────────────────────────────────────

DEPLOY_DIR="${PI_GEN_DIR}/deploy"
OUT_DIR="${REPO_ROOT}/dist"
mkdir -p "${OUT_DIR}"

# Find the produced .img.xz (pi-gen names them by date)
IMG=$(ls -1 "${DEPLOY_DIR}"/*.img.xz 2>/dev/null | head -n 1 || true)
if [[ -z "${IMG}" ]]; then
    echo "Error: pi-gen finished but no .img.xz found in ${DEPLOY_DIR}" >&2
    exit 1
fi

FINAL="${OUT_DIR}/mlss-pi-os-${IMAGE_VERSION}.img.xz"
cp "${IMG}" "${FINAL}"
echo "==> Built: ${FINAL}"
echo "    Size: $(du -h "${FINAL}" | cut -f1)"
echo "    SHA256: $(sha256sum "${FINAL}" | cut -d' ' -f1)"

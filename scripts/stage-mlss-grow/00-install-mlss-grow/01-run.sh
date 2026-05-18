#!/bin/bash -e
# Host-side script run by pi-gen before 01-run-chroot.sh. Copy the
# systemd unit, firstboot script, yaml template, AND the locally-built
# wheels (mlss_grow + mlss_contracts) into the rootfs's /tmp where the
# chroot script can install them.
#
# The wheels are sourced from MLSS_LOCAL_WHEELS_DIR — set by
# scripts/build_pi_image.sh after running scripts/build_local_wheels.sh.
# Falls back to dist/wheels relative to the repo root if unset, which
# is also where build_local_wheels.sh writes by default.

install -m 644 files/mlss-grow.service     "${ROOTFS_DIR}/tmp/mlss-grow.service"
install -m 755 files/firstboot.sh          "${ROOTFS_DIR}/tmp/firstboot.sh"
install -m 644 files/mlss-grow.yaml.template \
    "${ROOTFS_DIR}/tmp/mlss-grow.yaml.template"

# Stage the local wheels for the chroot script to pip-install from.
# MLSS_LOCAL_WHEELS_DIR is set by build_pi_image.sh via pi-gen's config
# file (every variable in pi-gen/config is exported into stage scripts).
WHEELS_DIR="${MLSS_LOCAL_WHEELS_DIR:-}"
if [[ -z "${WHEELS_DIR}" ]]; then
    echo "Error: MLSS_LOCAL_WHEELS_DIR is unset." >&2
    echo "       build_pi_image.sh should export it after running" >&2
    echo "       scripts/build_local_wheels.sh." >&2
    exit 1
fi
if [[ ! -d "${WHEELS_DIR}" ]]; then
    echo "Error: wheel directory not found: ${WHEELS_DIR}" >&2
    echo "       Run scripts/build_local_wheels.sh first." >&2
    exit 1
fi
# Sanity-check both wheels are present.
if ! ls "${WHEELS_DIR}"/mlss_grow-*.whl >/dev/null 2>&1; then
    echo "Error: no mlss_grow wheel in ${WHEELS_DIR}" >&2
    exit 1
fi
if ! ls "${WHEELS_DIR}"/mlss_contracts-*.whl >/dev/null 2>&1; then
    echo "Error: no mlss_contracts wheel in ${WHEELS_DIR}" >&2
    exit 1
fi

install -d -m 755 "${ROOTFS_DIR}/tmp/wheels"
install -m 644 "${WHEELS_DIR}"/*.whl "${ROOTFS_DIR}/tmp/wheels/"

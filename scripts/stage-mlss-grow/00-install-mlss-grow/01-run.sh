#!/bin/bash -e
# Host-side script run by pi-gen before 01-run-chroot.sh. Copy the
# systemd unit, firstboot script, and yaml template into the rootfs's
# /tmp where the chroot script can install them.

install -m 644 files/mlss-grow.service     "${ROOTFS_DIR}/tmp/mlss-grow.service"
install -m 755 files/firstboot.sh          "${ROOTFS_DIR}/tmp/firstboot.sh"
install -m 644 files/mlss-grow.yaml.template \
    "${ROOTFS_DIR}/tmp/mlss-grow.yaml.template"

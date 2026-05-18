#!/bin/bash -e
# stage-mlss-grow prerun: link our work tree on top of stage2's output
# (the Pi OS Lite rootfs). pi-gen invokes this before any of the
# numbered substages run.

if [[ ! -d "${ROOTFS_DIR}" ]]; then
    copy_previous
fi

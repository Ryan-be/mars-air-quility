#!/bin/bash
# /usr/local/sbin/mlss-firstboot.sh
#
# Runs once on first boot via /etc/rc.local. Self-marks complete on
# success and becomes a no-op thereafter (rc.local still calls it,
# but the marker file shortcircuits).
#
# What it does:
#   1. Check if the marker file exists — if so, exit silently.
#   2. Read /boot/mlss-grow.yaml. If absent, print a "drop config and
#      reboot" hint to the boot console, exit (rc.local will rerun us
#      next boot).
#   3. Once the yaml is in place: enable + start mlss-grow.service.
#      The firmware itself handles enrolment (POST /api/grow/enroll
#      with the key from the yaml, persists the bearer token).
#   4. Touch the marker file so future boots skip this dance.

set -uo pipefail

MARKER="/var/lib/mlss-grow/.firstboot-done"
YAML="/boot/mlss-grow.yaml"

mkdir -p /var/lib/mlss-grow

if [[ -f "${MARKER}" ]]; then
    exit 0
fi

if [[ ! -f "${YAML}" ]]; then
    cat <<EOF | tee /dev/console
============================================================
MLSS first-boot setup is waiting for /boot/mlss-grow.yaml.

To complete setup:
  1. Power down the Pi.
  2. Pull the SD card and insert it into your computer.
  3. Copy your mlss-grow.yaml onto the boot partition
     (a template is at /boot/mlss-grow.yaml.template).
  4. Re-insert the card and boot the Pi.

The service will auto-start on the next boot once the yaml
is present.
============================================================
EOF
    exit 0
fi

# Enable I2C in case raspi-config in the chroot didn't take. Idempotent.
raspi-config nonint do_i2c 0 || true

systemctl enable mlss-grow.service
systemctl start mlss-grow.service

touch "${MARKER}"
echo "MLSS first-boot setup complete. mlss-grow.service started." | tee /dev/console

#!/bin/bash -e
# Inside the target rootfs (chroot): create the mlss-grow venv and
# pip-install the firmware FROM LOCAL WHEELS that the host-side stage
# script staged into /tmp/wheels/. We do this here rather than at first
# boot so the image ships ready-to-run — first boot just enables the
# service if a yaml is present.
#
# This intentionally does NOT reach out to PyPI: the wheels were built
# by scripts/build_local_wheels.sh on the maintainer's machine and
# baked into the image at /tmp/wheels/. That keeps the image
# self-contained — no external package-index dependency at provision
# time. Transitive deps (Pillow, pydantic, websockets, requests,
# adafruit-circuitpython-seesaw, RPi.GPIO) DO still come from PyPI /
# piwheels: shipping a full wheelhouse for every transitive dep would
# bloat the image and tie the image build to a fixed set of dep
# versions. The Pi has internet for apt anyway, so transitive PyPI
# resolution is acceptable.

set -euo pipefail

mkdir -p /opt/mlss-grow
python3 -m venv --system-site-packages /opt/mlss-grow/.venv

# Use piwheels for pre-built ARM wheels of transitive deps — same
# posture as the manual install path (see readme.md "Why piwheels?").
# Without it, Pillow + cryptography would compile from source on the
# Pi which is very slow and can fail on a Pi Zero.
/opt/mlss-grow/.venv/bin/pip install \
    --index-url https://pypi.org/simple \
    --extra-index-url https://www.piwheels.org/simple \
    --upgrade pip

# Install mlss-grow + mlss-contracts from the local wheels. --find-links
# points pip at /tmp/wheels (where 01-run.sh staged them); the package
# names "mlss-grow" + "mlss-contracts" then resolve to those wheels
# in preference to anything on PyPI.
#
# We do NOT pass --no-index because mlss-grow's transitive deps
# (Pillow, websockets, pydantic, ...) still need to come from PyPI /
# piwheels. --find-links + a normal index lets pip mix the two: local
# wheels for our packages, public index for everything else.
/opt/mlss-grow/.venv/bin/pip install \
    --index-url https://pypi.org/simple \
    --extra-index-url https://www.piwheels.org/simple \
    --find-links /tmp/wheels \
    mlss-grow mlss-contracts

# Dedicated system user matches what install.sh creates on a manual
# install. Locking down ownership of /var/lib/mlss-grow keeps the
# bearer token + buffer DB out of reach of other accounts.
adduser --system --group --no-create-home --home /var/lib/mlss-grow \
    --shell /usr/sbin/nologin mlss-grow
mkdir -p /var/lib/mlss-grow /etc/mlss
chown -R mlss-grow:mlss-grow /var/lib/mlss-grow
chmod 700 /var/lib/mlss-grow

# Drop the systemd unit (do NOT enable here — first-boot.sh enables it
# only after a valid /boot/mlss-grow.yaml is present)
install -m 644 /tmp/mlss-grow.service /etc/systemd/system/mlss-grow.service
install -m 755 /tmp/firstboot.sh /usr/local/sbin/mlss-firstboot.sh
install -m 644 /tmp/mlss-grow.yaml.template /boot/mlss-grow.yaml.template

# Hook firstboot.sh into rc.local so it runs once on first boot. Pi OS
# Lite Bookworm doesn't ship rc.local by default; we drop it ourselves.
cat > /etc/rc.local <<'RCLOCAL'
#!/bin/sh -e
# rc.local — runs once on each boot before login prompts.
# /usr/local/sbin/mlss-firstboot.sh self-marks complete on success
# and is a no-op thereafter.
if [ -x /usr/local/sbin/mlss-firstboot.sh ]; then
    /usr/local/sbin/mlss-firstboot.sh || true
fi
exit 0
RCLOCAL
chmod +x /etc/rc.local

# Enable I2C (required for the Seesaw soil sensor). raspi-config nonint
# is the headless way to flip the kernel module + boot config.
raspi-config nonint do_i2c 0 || true

# Disable the rainbow splash + boot delays — operators want fast boot
# on a headless device.
echo "disable_splash=1" >> /boot/config.txt

# Clean up the staged wheels — they're installed into the venv now,
# no point shipping the duplicates in the image.
rm -rf /tmp/wheels

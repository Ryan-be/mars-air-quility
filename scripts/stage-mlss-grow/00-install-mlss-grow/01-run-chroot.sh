#!/bin/bash -e
# Inside the target rootfs (chroot): create the mlss-grow venv and
# pip-install the firmware. We do this here rather than at first boot
# so the image ships ready-to-run — first boot just enables the
# service if a yaml is present.

set -euo pipefail

# Pin via build-time env (default: latest from PyPI). The build script
# exports MLSS_GROW_VERSION before invoking pi-gen.
PIN="${MLSS_GROW_VERSION:-latest}"

mkdir -p /opt/mlss-grow
python3 -m venv --system-site-packages /opt/mlss-grow/.venv

# Use piwheels for pre-built ARM wheels — same posture as the manual
# install path (see readme.md "Why piwheels?"). Without this, Pillow
# + cryptography would compile from source on the Pi which is very
# slow and can fail on a Pi Zero.
/opt/mlss-grow/.venv/bin/pip install \
    --index-url https://pypi.org/simple \
    --extra-index-url https://www.piwheels.org/simple \
    --upgrade pip

if [[ "${PIN}" == "latest" ]]; then
    /opt/mlss-grow/.venv/bin/pip install \
        --index-url https://pypi.org/simple \
        --extra-index-url https://www.piwheels.org/simple \
        mlss-grow
else
    /opt/mlss-grow/.venv/bin/pip install \
        --index-url https://pypi.org/simple \
        --extra-index-url https://www.piwheels.org/simple \
        "mlss-grow==${PIN}"
fi

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

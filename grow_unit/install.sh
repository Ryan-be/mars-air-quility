#!/bin/bash
# MLSS Plant Grow Unit installer.
#
# Run on a fresh Raspberry Pi Zero W (or Pi Zero 2 W) with Pi OS Lite
# and a /boot/mlss-grow.yaml config file. The one-line install command:
#
#   curl -k https://mlss.local:5000/api/grow/install.sh | sudo bash
#
# What this does:
#   1. apt-installs Python 3.11+, libcamera-apps, i2c-tools, build-essentials
#   2. Creates dedicated mlss-grow system user
#   3. Creates required directories with correct ownership
#   4. Downloads wheels (mlss_contracts + mlss_grow) from MLSS server
#   5. Creates a venv at /opt/mlss-grow/.venv and pip-installs both wheels
#   6. Drops the systemd service unit
#   7. Enables and starts the service

set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Must be run as root (use sudo)" >&2
    exit 1
fi

# ── Read MLSS host from /boot/mlss-grow.yaml so we know where to fetch wheels.
#     (The Python service later parses this fully; here we just need the host.)
MLSS_HOST=""
if [[ -f /boot/mlss-grow.yaml ]]; then
    MLSS_HOST=$(grep -E '^mlss_host:' /boot/mlss-grow.yaml | awk '{print $2}' | tr -d '"' || true)
fi
if [[ -z "$MLSS_HOST" ]]; then
    echo "Error: /boot/mlss-grow.yaml missing or doesn't set mlss_host" >&2
    exit 1
fi

echo "==> MLSS host: $MLSS_HOST"

# ── 1. apt deps
echo "==> Installing system packages"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    libcamera-apps i2c-tools \
    build-essential libffi-dev

# ── 2. Dedicated user
if ! id mlss-grow >/dev/null 2>&1; then
    echo "==> Creating mlss-grow user"
    useradd --system --shell /usr/sbin/nologin --home /opt/mlss-grow mlss-grow
    usermod -aG i2c,gpio,video mlss-grow || true
fi

# ── 3. Directories
echo "==> Creating directories"
install -d -o mlss-grow -g mlss-grow -m 0755 /opt/mlss-grow
install -d -o mlss-grow -g mlss-grow -m 0750 /etc/mlss
install -d -o mlss-grow -g mlss-grow -m 0750 /var/lib/mlss-grow
install -d -o mlss-grow -g mlss-grow -m 0755 /var/log/mlss-grow

# ── 4. Download wheels
echo "==> Downloading wheels from $MLSS_HOST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

LATEST=$(curl -ks "https://${MLSS_HOST}:5000/api/grow/dist/latest")
GROW_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_grow'])")
CONTRACTS_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_contracts'])")

curl -k -o "$TMP/mlss_grow-${GROW_VER}-py3-none-any.whl" \
    "https://${MLSS_HOST}:5000/api/grow/dist/mlss_grow-${GROW_VER}-py3-none-any.whl"
curl -k -o "$TMP/mlss_contracts-${CONTRACTS_VER}-py3-none-any.whl" \
    "https://${MLSS_HOST}:5000/api/grow/dist/mlss_contracts-${CONTRACTS_VER}-py3-none-any.whl"

# ── 5. venv + install
echo "==> Creating venv and installing wheels"
sudo -u mlss-grow python3 -m venv /opt/mlss-grow/.venv
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install --upgrade pip
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install \
    --no-index --find-links "$TMP" \
    "mlss_grow==${GROW_VER}" \
    "mlss_contracts==${CONTRACTS_VER}"

# ── 6. systemd unit
echo "==> Installing systemd unit"
INSTALL_DIR=$(/opt/mlss-grow/.venv/bin/python -c \
    "import mlss_grow, os; print(os.path.dirname(mlss_grow.__file__))")
cp "$INSTALL_DIR/../../systemd/mlss-grow.service" /etc/systemd/system/mlss-grow.service 2>/dev/null \
    || cp /opt/mlss-grow/.venv/lib/python*/site-packages/mlss_grow/systemd/mlss-grow.service \
        /etc/systemd/system/mlss-grow.service 2>/dev/null \
    || curl -k -o /etc/systemd/system/mlss-grow.service \
        "https://${MLSS_HOST}:5000/api/grow/dist/mlss-grow.service"
chmod 644 /etc/systemd/system/mlss-grow.service

systemctl daemon-reload

# ── 7. Enable + start
echo "==> Enabling + starting mlss-grow.service"
systemctl enable mlss-grow.service
systemctl start mlss-grow.service

echo "==> Done. Tail logs with: journalctl -u mlss-grow -f"

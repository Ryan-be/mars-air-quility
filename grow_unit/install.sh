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

# ── 4. Download wheels (with SHA256 verification)
echo "==> Downloading wheels from $MLSS_HOST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

LATEST=$(curl -ks "https://${MLSS_HOST}:5000/api/grow/dist/latest")

# Each manifest entry is now {version, filename, sha256} so we can verify
# integrity after download — defends against LAN MITM tampering with wheels.
GROW_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_grow']['version'])")
GROW_FILENAME=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_grow']['filename'])")
GROW_SHA256=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_grow']['sha256'])")
CONTRACTS_VER=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_contracts']['version'])")
CONTRACTS_FILENAME=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_contracts']['filename'])")
CONTRACTS_SHA256=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss_contracts']['sha256'])")
# Systemd unit ships through the same dist endpoint with its own sha256.
# It is NOT bundled in the mlss_grow wheel — keeps the wheel pure-Python
# and lets us update the unit without re-cutting a wheel.
SERVICE_FILENAME=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss-grow.service']['filename'])")
SERVICE_SHA256=$(echo "$LATEST" | python3 -c "import sys,json;print(json.load(sys.stdin)['mlss-grow.service']['sha256'])")

curl -k -o "$TMP/${GROW_FILENAME}" \
    "https://${MLSS_HOST}:5000/api/grow/dist/${GROW_FILENAME}"
curl -k -o "$TMP/${CONTRACTS_FILENAME}" \
    "https://${MLSS_HOST}:5000/api/grow/dist/${CONTRACTS_FILENAME}"
curl -k -o "$TMP/${SERVICE_FILENAME}" \
    "https://${MLSS_HOST}:5000/api/grow/dist/${SERVICE_FILENAME}"

# ── Verify SHA256 — defends against LAN MITM tampering with the wheels
# AND with the systemd unit. Both surfaces matter: the wheel runs as the
# mlss-grow user (i2c/gpio/video group member) and the systemd unit lands
# in /etc/systemd/system/ where systemd reads it as root. A tampered unit
# could expand the firmware's privileges (drop NoNewPrivileges, add
# CapabilityBoundingSet, run as root, etc.) so we treat both with equal
# care: download, hash, abort on mismatch.
echo "==> Verifying SHA256 sums"
verify_sha() {
    local file="$1" expected="$2" actual
    actual=$(sha256sum "$file" | awk '{print $1}')
    if [[ "$actual" != "$expected" ]]; then
        echo "ERROR: SHA256 mismatch for $file" >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        exit 1
    fi
}
verify_sha "$TMP/${GROW_FILENAME}" "$GROW_SHA256"
verify_sha "$TMP/${CONTRACTS_FILENAME}" "$CONTRACTS_SHA256"
verify_sha "$TMP/${SERVICE_FILENAME}" "$SERVICE_SHA256"

# ── 5. venv + install
echo "==> Creating venv and installing wheels"
sudo -u mlss-grow python3 -m venv /opt/mlss-grow/.venv
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install --upgrade pip
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install \
    --no-index --find-links "$TMP" \
    "mlss_grow==${GROW_VER}" \
    "mlss_contracts==${CONTRACTS_VER}"

# ── 6. systemd unit
# We already downloaded + SHA256-verified the unit above, so the install
# step is a plain copy from $TMP. The previous cp-from-package-data
# fallbacks were removed — the wheel doesn't ship the .service file, so
# they always failed silently and left the third (unverified curl)
# fallback as the actual install method. The verified-curl path is now
# canonical.
echo "==> Installing systemd unit"
install -m 0644 -o root -g root \
    "$TMP/${SERVICE_FILENAME}" /etc/systemd/system/mlss-grow.service

systemctl daemon-reload

# ── 7. Enable + start
echo "==> Enabling + starting mlss-grow.service"
systemctl enable mlss-grow.service
systemctl start mlss-grow.service

echo "==> Done. Tail logs with: journalctl -u mlss-grow -f"

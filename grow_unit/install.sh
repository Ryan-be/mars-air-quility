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
#   5. Pins MLSS server cert at /etc/mlss/server.crt (TOFU at install time)
#   6. Creates a venv at /opt/mlss-grow/.venv and pip-installs both wheels
#   7. Drops the systemd service unit
#   8. Enables and starts the service

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
# python3-picamera2 is the Python binding for libcamera. It's a system
# package on Pi OS (the libcamera C bindings can't be pip-installed
# cleanly) — the venv below uses --system-site-packages so it can see it.
# libcamera-apps gives us the CLI tools (libcamera-still etc.) for
# debugging from a shell.
echo "==> Installing system packages"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    libcamera-apps python3-picamera2 i2c-tools \
    build-essential libffi-dev \
    openssl

# ── 2. Dedicated user
if ! id mlss-grow >/dev/null 2>&1; then
    echo "==> Creating mlss-grow user"
    useradd --system --shell /usr/sbin/nologin --home /opt/mlss-grow mlss-grow
    usermod -aG i2c,gpio,video mlss-grow || true
fi

# ── 2.5. Enable I2C interface (for the soil moisture sensor) and the
# camera. Both are off by default on Pi OS Lite; the firmware crashes
# with "No Hardware I2C" / "no camera" if these aren't enabled.
# raspi-config writes to /boot/config.txt and /etc/modules; `0` means
# enable for the do_i2c / do_camera commands. A reboot is required for
# the kernel modules to pick them up — install.sh prints a reminder
# at the end if either was newly enabled.
echo "==> Enabling I2C and camera interfaces"
NEED_REBOOT=0
if command -v raspi-config >/dev/null 2>&1; then
    if ! raspi-config nonint get_i2c | grep -q '^0$'; then
        raspi-config nonint do_i2c 0
        NEED_REBOOT=1
        echo "    I2C: newly enabled (reboot required)"
    else
        echo "    I2C: already enabled"
    fi
    # Camera: do_camera was deprecated on Bookworm+ in favour of libcamera
    # (which doesn't need a raspi-config flag — works out-of-the-box if
    # the ribbon is wired). Try do_camera anyway for older Pi OS, ignore
    # failure on newer ones.
    if raspi-config nonint do_camera 0 2>/dev/null; then
        echo "    Camera (legacy do_camera): newly enabled"
        NEED_REBOOT=1
    fi
else
    echo "    raspi-config not found — skip (assume manual setup)"
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
# mktemp -d defaults to mode 0700, which blocks the unprivileged
# mlss-grow user from reading the wheels later via `sudo -u mlss-grow
# pip install --find-links "$TMP" ...`. Open it to 0755 so non-owner
# read+exec works. Contents are public artefacts (already SHA256-
# verified), so 0755 doesn't expose anything sensitive.
chmod 0755 "$TMP"
trap 'rm -rf "$TMP"' EXIT

LATEST=$(curl -ks "https://${MLSS_HOST}:5000/api/grow/dist/latest")

# Defensive: empty manifest means the MLSS server hasn't built its wheels.
# Catch this early with a clear message rather than letting the python3
# KeyError that follows confuse the operator. Three failure modes are
# distinguished:
#   (a) curl already errored on unreachable host (set -e aborts above)
#   (b) manifest is {} or missing required keys (this guard)
#   (c) manifest has the wrong shape (parse_error path below)
HAS_GROW=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'mlss_grow' in d else 'no')" 2>/dev/null || echo "parse_error")
if [[ "$HAS_GROW" != "yes" ]]; then
    echo "ERROR: MLSS server at ${MLSS_HOST} returned an empty wheel manifest." >&2
    echo "" >&2
    echo "  Got: $LATEST" >&2
    echo "" >&2
    echo "The wheels have not been built yet. On the MLSS server, run:" >&2
    echo "  cd /path/to/mars-air-quility" >&2
    echo "  bash scripts/build_grow_wheel.sh" >&2
    echo "" >&2
    echo "Then re-run this installer." >&2
    exit 2
fi
HAS_CONTRACTS=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'mlss_contracts' in d else 'no')" 2>/dev/null || echo "parse_error")
if [[ "$HAS_CONTRACTS" != "yes" ]]; then
    echo "ERROR: MLSS server at ${MLSS_HOST} manifest is missing mlss_contracts." >&2
    echo "" >&2
    echo "  Got: $LATEST" >&2
    echo "" >&2
    echo "The wheels have not been built yet. On the MLSS server, run:" >&2
    echo "  cd /path/to/mars-air-quility" >&2
    echo "  bash scripts/build_grow_wheel.sh" >&2
    echo "" >&2
    echo "Then re-run this installer." >&2
    exit 2
fi

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
# --system-site-packages lets the venv import system-installed Python
# packages (specifically python3-picamera2 from apt above — its libcamera
# C bindings can't be pip-installed). pip-installed packages still take
# precedence over system ones, so this doesn't break our wheel-pinned
# mlss_grow / mlss_contracts / Pillow / pydantic etc.
echo "==> Creating venv and installing wheels"
sudo -u mlss-grow python3 -m venv --system-site-packages /opt/mlss-grow/.venv
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install --upgrade pip
# --find-links "$TMP" makes pip prefer our SHA256-verified mlss_grow +
# mlss_contracts wheels for those two packages. Transitive deps (Pillow,
# pydantic, websockets, etc.) come from PyPI — we don't ship a full
# wheelhouse, and the Pi has internet for apt anyway. Don't add
# --no-index here: it would block the transitive resolution and pip
# fails with "No matching distribution" even though the deps are
# perfectly fetchable from PyPI.
sudo -u mlss-grow /opt/mlss-grow/.venv/bin/pip install \
    --find-links "$TMP" \
    "mlss_grow==${GROW_VER}" \
    "mlss_contracts==${CONTRACTS_VER}"

# ── 6. Pin MLSS trust anchor at /etc/mlss/server.crt
# The MLSS server presents a TLS cert on the LAN. Without a pinned
# trust anchor the firmware can't establish wss:// + can't verify
# enrollment POSTs — the previous `verify=False` posture leaked the
# enrollment_key (in the request body) to anyone doing LAN MITM.
#
# Strategy (preferred): pin the LOCAL CA at /etc/mlss/server.crt.
#   The hub publishes its CA at /api/grow/ca.crt (public; CA is a
#   trust anchor, not a secret). Pinning the CA means any leaf cert
#   signed by it validates — so when the operator rotates the leaf
#   via scripts/generate_local_ca.sh, the grow unit keeps working
#   without re-pinning. CA validity is 10 years.
#
# Fallback (legacy): if the hub doesn't publish a CA (404 from the
# endpoint), we fall back to the original openssl s_client TOFU pin
# of the leaf cert. That keeps install.sh working against older hubs
# but the leaf has to be re-pinned on every cert rotation.
#
# Owned by root, mode 0644 — it's a trust anchor; the mlss-grow user
# only needs to read it (the world-readable bit), never replace it.
TMP_CERT="$TMP/server.crt"

echo "==> Trying to fetch MLSS CA from https://${MLSS_HOST}:5000/api/grow/ca.crt"
if curl -sk -o "$TMP_CERT" -w "%{http_code}" \
        "https://${MLSS_HOST}:5000/api/grow/ca.crt" | grep -q '^200$' \
   && [[ -s "$TMP_CERT" ]] \
   && head -1 "$TMP_CERT" | grep -q "BEGIN CERTIFICATE"; then
    echo "==> Pinning MLSS CA at /etc/mlss/server.crt (rotation-safe)"
else
    echo "==> CA endpoint unavailable (older hub?). Falling back to leaf-cert TOFU"
    if ! openssl s_client -servername "$MLSS_HOST" \
            -connect "${MLSS_HOST}:5000" </dev/null 2>/dev/null \
            | openssl x509 -outform PEM > "$TMP_CERT"; then
        echo "ERROR: failed to fetch MLSS server cert from ${MLSS_HOST}:5000" >&2
        exit 1
    fi
    if [[ ! -s "$TMP_CERT" ]]; then
        echo "ERROR: extracted cert is empty (openssl s_client returned no PEM)" >&2
        exit 1
    fi
    echo "==> Pinning MLSS leaf cert at /etc/mlss/server.crt (TOFU)"
fi
install -m 0644 -o root -g root "$TMP_CERT" /etc/mlss/server.crt

# ── 7. systemd unit
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

# ── 8. Enable + start
echo "==> Enabling + starting mlss-grow.service"
systemctl enable mlss-grow.service

if [[ "$NEED_REBOOT" -eq 1 ]]; then
    echo ""
    echo "==> I2C / camera was newly enabled — REBOOT REQUIRED before the"
    echo "    sensors will work. The service is enabled and will start"
    echo "    automatically after reboot. Run:"
    echo ""
    echo "      sudo reboot"
    echo ""
    echo "    Then watch the logs with: journalctl -u mlss-grow -f"
else
    systemctl start mlss-grow.service
    echo "==> Done. Tail logs with: journalctl -u mlss-grow -f"
fi

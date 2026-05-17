#!/bin/bash
# First-time setup script for MLSS Monitor on Raspberry Pi.
# Run once after cloning the repo:
#   bash scripts/setup_pi.sh
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

echo ""
echo "=============================="
echo " MLSS Monitor — Pi Setup"
echo "=============================="
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
# ffmpeg is required by mlss_monitor/grow/timelapse_jobs.py for grow-unit
# time-lapse video rendering on the History tab. Without it the POST
# endpoint returns 503 ffmpeg_not_installed on first use — install it
# here so a fresh setup is ready to go end-to-end.
info "Installing system build dependencies..."
sudo apt-get update -q
sudo apt-get install -y \
    python3-dev \
    gcc \
    libssl-dev \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    i2c-tools \
    git \
    curl \
    ffmpeg
success "System packages installed"

# ── 2. Enable I2C ─────────────────────────────────────────────────────────────
if grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null || \
   grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    success "I2C already enabled"
else
    warn "Enabling I2C in /boot/firmware/config.txt — a reboot will be needed"
    CONFIG_FILE="/boot/firmware/config.txt"
    [ -f /boot/config.txt ] && CONFIG_FILE="/boot/config.txt"
    sudo sh -c "echo 'dtparam=i2c_arm=on' >> $CONFIG_FILE"
fi

# ── 2a. Enable UART for PM sensor (PMSA003 on /dev/serial0) ──────────────────
# The SB Components Air Monitoring HAT reads particulate matter via the
# hardware UART. Two things must be true:
#   (a) Serial console disabled + hardware UART enabled at the kernel
#       level. raspi-config's `do_serial 2` does both in one shot.
#   (b) The user that runs mlss-monitor must be in the `dialout` group
#       because /dev/serial0 is root:dialout 660 by default.
# Both were previously manual readme steps — the symptom of either
# missing is the same: EACCES flood in `journalctl -u mlss-monitor` and
# zero PM telemetry. Automate both so first-boot just works.
if command -v raspi-config >/dev/null 2>&1; then
    # do_serial 2: serial login console DISABLED, hardware UART ENABLED.
    # Idempotent — safe to re-run; raspi-config returns 0 either way.
    info "Enabling hardware UART for PM sensor..."
    sudo raspi-config nonint do_serial 2 || warn "do_serial returned non-zero — UART may need manual enable"
    success "UART configured (reboot required to take effect)"
else
    warn "raspi-config not found — enable UART manually per readme.md"
fi

# Add the operator to the dialout group so /dev/serial0 is readable.
# `id -nG` lists the user's current groups; `grep -qw dialout` matches
# the whole word so 'dialout' isn't also matched by e.g. 'dialoutfoo'.
if id -nG "$USER" 2>/dev/null | grep -qw dialout; then
    success "$USER already in dialout group"
else
    info "Adding $USER to dialout group (for /dev/serial0 access)..."
    sudo usermod -aG dialout "$USER"
    warn "Group change takes effect on next login OR after 'sudo systemctl restart mlss-monitor'"
fi

# ── 3. Configure piwheels (pre-built ARM wheels — much faster installs) ───────
info "Configuring pip to use piwheels..."
pip config set global.extra-index-url https://www.piwheels.org/simple
success "piwheels configured"

# ── 4. Install Poetry if missing ──────────────────────────────────────────────
if ! command -v poetry &>/dev/null; then
    info "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
    success "Poetry installed"
else
    success "Poetry already installed ($(poetry --version))"
fi

# ── 5. Pre-install C-extension packages via pip (avoids source builds) ────────
# Poetry's resolver sometimes picks PyPI sdists over piwheels armv7l wheels for
# packages with C extensions (numpy, scipy, river). Installing them via pip first
# lets piwheels serve pre-built wheels; poetry then sees them as already satisfied.
info "Pre-installing compiled packages from piwheels..."
poetry env use python3 --quiet 2>/dev/null || true
VENV_PIP="$(poetry env info --path)/bin/pip"
"$VENV_PIP" install --quiet \
    "numpy>=1.26,<2.0" \
    "scipy" \
    "river>=0.23,<0.24"
success "Compiled packages pre-installed"

# ── 6. Install project dependencies ──────────────────────────────────────────
# Skip 'visualization' (pandas/matplotlib — heavy, not needed for the web app)
# Skip 'dev' (pytest — not needed in production)
info "Installing remaining Python dependencies..."
poetry install --without visualization --without dev
# RPi.GPIO is a Pi-only hardware package not listed in pyproject.toml
# (it fails to build on non-Pi platforms) — install it directly
poetry run pip install RPi.GPIO
success "Dependencies installed"

# ── 6. Create data directory ──────────────────────────────────────────────────
info "Creating data/ directory..."
mkdir -p data
success "data/ directory ready"

# ── 7. Initialise database ────────────────────────────────────────────────────
info "Initialising SQLite database..."
poetry run python database/init_db.py
success "Database initialised"

# ── 8. Generate self-signed TLS certificate ─────────────────────────────────
if [ ! -f certs/cert.pem ] || [ ! -f certs/key.pem ]; then
    info "Generating self-signed TLS certificate..."
    poetry run python scripts/generate_certs.py
    success "TLS certificate generated in certs/"
else
    success "TLS certificates already exist"
fi

# ── 9. Create .env if it doesn't exist ───────────────────────────────────────
if [ ! -f .env ]; then
    info "Creating default .env..."
    cat > .env <<'EOF'
ENV_FOR_DYNACONF=production
MLSS_LOG_INTERVAL=10
MLSS_LOG_FILE=data/log.csv
MLSS_DB_FILE=data/sensor_data.db
MLSS_FAN_KASA_SMART_PLUG_IP=192.168.1.63
EOF
    warn "Edit .env and set MLSS_FAN_KASA_SMART_PLUG_IP to your smart plug's IP address"
else
    success ".env already exists"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================="
success "Setup complete!"
echo "=============================="
echo ""
echo "Next steps:"
echo "  1. Edit .env and set MLSS_FAN_KASA_SMART_PLUG_IP"
if grep -q "Enabling I2C" <<< "$(cat /boot/firmware/config.txt 2>/dev/null || cat /boot/config.txt 2>/dev/null)" 2>/dev/null; then
    echo "  2. Reboot to enable I2C: sudo reboot"
    echo "  3. Run: poetry run python mlss_monitor/app.py"
else
    echo "  2. Run: poetry run python mlss_monitor/app.py"
fi
echo ""
echo "  To install as a systemd service:"
echo "    sudo cp mlss-monitor.service /etc/systemd/system/"
echo "    sudo systemctl enable --now mlss-monitor"
echo ""

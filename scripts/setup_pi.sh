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
    curl
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

# ── 5. Install project dependencies ──────────────────────────────────────────
# Skip 'visualization' (pandas/matplotlib — heavy, not needed for the web app)
# Skip 'dev' (pytest — not needed in production)
info "Installing Python dependencies (this may take a few minutes)..."
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
LOG_INTERVAL=10
LOG_FILE=data/log.csv
DB_FILE=data/sensor_data.db
FAN_KASA_SMART_PLUG_IP=192.168.1.63
EOF
    warn "Edit .env and set FAN_KASA_SMART_PLUG_IP to your smart plug's IP address"
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
echo "  1. Edit .env and set FAN_KASA_SMART_PLUG_IP"
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

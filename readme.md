# MLSS Monitor: Mars Life Support Sensor Monitor

A lightweight environmental monitoring system for Raspberry Pi, designed as a prototype for Mars habitat applications. Logs sensor data to SQLite, serves a live web dashboard with historical plots, controls a fan automatically via a Kasa smart plug, and displays status on a small TFT screen.

---

## Hardware

| Component | Purpose |
|---|---|
| Raspberry Pi Zero W | Host |
| Adafruit AHT20 | Temperature & humidity (I2C) |
| Adafruit SGP30 | eCO2 & TVOC air quality (I2C) |
| 1.8" ST7735 TFT LCD | Local readout (SPI, 128×160) |
| TP-Link Kasa smart plug | Fan control |

### Wiring — I2C sensors (daisy-chained)

| Signal | Pi GPIO | Wire colour | Connected to |
|---|---|---|---|
| 3.3V | Pin 1 | Red | AHT20 → SGP30 |
| GND | Pin 6 | Black | AHT20 → SGP30 |
| SDA | Pin 3 (GPIO2) | Blue | AHT20 → SGP30 |
| SCL | Pin 5 (GPIO3) | Yellow | AHT20 → SGP30 |

### Wiring — ST7735 LCD (SPI)

| LCD pin | Pi pin | GPIO | Function |
|---|---|---|---|
| GND | 6 | — | Ground |
| VCC | 1 | — | 3.3V power |
| SCL | 23 | GPIO11 | SPI clock |
| SDA | 19 | GPIO10 | SPI MOSI |
| RES | 22 | GPIO25 | Reset |
| DC | 18 | GPIO24 | Data/command |
| CS | 24 | GPIO8 | Chip select |

---

## Features

- Live sensor dashboard with configurable time range (15 min → all time)
- Auto fan control — turns on when temperature or TVOC exceeds configurable thresholds
- Admin page to configure fan thresholds and enable/disable auto mode
- Manual fan on/off override via API
- Data annotation — mark points of interest directly on the chart
- CSV export of historical readings
- System health endpoint (CPU, memory, uptime, sensor status)

---

## Installation

### Prerequisites

- Raspberry Pi running Raspberry Pi OS (Bookworm or Bullseye)
- Python 3.11+
- I2C enabled (the setup script handles this)

### First-time setup

```bash
git clone https://github.com/Ryan-be/mars-air-quility.git
cd mars-air-quility
bash scripts/setup_pi.sh
```

The setup script:
1. Installs system build dependencies via `apt` (`python3-dev`, `libssl-dev`, `libjpeg-dev`, etc.)
2. Enables I2C if not already on — **a reboot is required after this step**
3. Configures pip to use [piwheels](https://www.piwheels.org) (pre-built ARM wheels — see below)
4. Installs [Poetry](https://python-poetry.org) if missing
5. Installs project dependencies, skipping heavy optional packages and dev tools
6. Creates the `data/` directory and initialises the SQLite database
7. Creates a default `.env` if one does not exist

> After setup, edit `.env` and set `FAN_KASA_SMART_PLUG_IP` to your plug's IP address.

### Why piwheels?

Many packages with C extensions (Pillow, cryptography, cffi) do not ship pre-built ARM wheels on PyPI. Without piwheels, pip must compile from source on the Pi — which is very slow and can fail due to missing system libraries or memory constraints. piwheels provides pre-compiled ARM wheels for the most common packages, reducing install time from tens of minutes to seconds.

piwheels is configured in `pyproject.toml` as a supplemental source, so Poetry will check it automatically.

### Manual install

```bash
pip config set global.extra-index-url https://www.piwheels.org/simple
poetry install --without visualization --without dev
mkdir -p data
poetry run python database/init_db.py
```

---

## Configuration

Settings are read from `.env` via [Dynaconf](https://www.dynaconf.com). Any variable can also be set as an environment variable prefixed with `MLSS_`.

| Variable | Default | Description |
|---|---|---|
| `ENV_FOR_DYNACONF` | `production` | Environment name |
| `LOG_INTERVAL` | `10` | Sensor polling interval (seconds) |
| `LOG_FILE` | `data/log.csv` | Legacy log path |
| `DB_FILE` | `data/sensor_data.db` | SQLite database path |
| `FAN_KASA_SMART_PLUG_IP` | `192.168.1.63` | IP of the Kasa smart plug |

Example `.env`:
```ini
ENV_FOR_DYNACONF=production
LOG_INTERVAL=10
LOG_FILE=data/log.csv
DB_FILE=data/sensor_data.db
FAN_KASA_SMART_PLUG_IP=192.168.1.63
```

---

## Running

### Directly

```bash
poetry run python mlss_monitor/app.py
```

Dashboard available at `http://<pi-ip>:5000`.

### As a systemd service

```bash
# Edit the service file if your username or project path differs from masadmin
sudo cp mlss-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mlss-monitor

# Check status / follow logs
sudo systemctl status mlss-monitor
sudo journalctl -u mlss-monitor -f
```

---

## Web interface

| URL | Description |
|---|---|
| `/` | Live sensor dashboard |
| `/admin` | Fan settings — thresholds and auto mode toggle |
| `/system_health` | JSON system status |

## API reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/data?range=24h` | Sensor readings. `range`: `15m` `1h` `6h` `12h` `24h` `all` |
| `GET` | `/api/download?range=24h` | Download as CSV |
| `POST` | `/api/annotate?point=<id>` | Add annotation — body: `{"annotation": "text"}` |
| `DELETE` | `/api/annotate?point=<id>` | Remove annotation |
| `POST` | `/api/fan?state=on\|off\|auto` | Manual fan control or switch to auto mode |
| `GET` | `/api/fan/status` | Current plug state |
| `GET` | `/api/fan/settings` | Auto fan threshold settings |
| `POST` | `/api/fan/settings` | Update settings — body: `{"temp_max": 25.0, "tvoc_max": 600, "enabled": true, ...}` |

---

## Development

### Running tests

```bash
poetry install --with dev
poetry run pytest tests/ -v
```

Tests are organised into three files:

| File | Covers |
|---|---|
| `tests/test_fan_settings.py` | DB layer round-trips, API GET/POST, admin page |
| `tests/test_async.py` | thread_loop integration, async dispatch patterns, error handling |
| `tests/test_pi_resilience.py` | Sensor failures, DB init idempotency, `/proc/uptime` fallback, background thread survival |

Hardware libraries (`board`, `busio`, `adafruit_*`) are stubbed in `tests/conftest.py` so all tests run on any machine.

### Linting

```bash
poetry run pip install pylint
poetry run pylint $(git ls-files '*.py')
```

### Optional visualization dependencies

`pandas` and `matplotlib` are not used by the web app. If you need them for data analysis:

```bash
poetry install --with visualization
```

---

## Project structure

```
mlss_monitor/
  app.py                      Flask app, sensor loop, fan control, API routes
database/
  db_logger.py                SQLite read/write helpers
  init_db.py                  Schema creation — safe to re-run on existing DB
  import_csv_to_db.py         One-off CSV import utility
sensors/
  aht20.py                    AHT20 temperature/humidity driver
  sgp30.py                    SGP30 eCO2/TVOC driver (15 s warm-up on startup)
  display.py                  ST7735 TFT display driver
  sb_components_pm_sensor.py  Particulate matter sensor driver
external_api_interfaces/
  kasa_smart_plug.py          Async TP-Link Kasa plug control
templates/
  dashboard.html              Live sensor dashboard
  admin.html                  Fan settings admin page
scripts/
  setup_pi.sh                 First-run setup script for Raspberry Pi
tests/
  conftest.py                 Pytest fixtures and hardware stubs
  test_fan_settings.py        Fan settings DB and API tests
  test_async.py               Async dispatch and thread-loop tests
  test_pi_resilience.py       Pi-specific resilience tests
config.py                     Dynaconf configuration loader
mlss-monitor.service          systemd unit file
```

---

## Known limitations

| Issue | Detail |
|---|---|
| `data/` directory must exist before starting | SQLite will fail if the directory is missing. The setup script creates it; for manual installs run `mkdir -p data`. |
| SGP30 15 s warm-up | The first few eCO2/TVOC readings after power-on may be inaccurate — this is normal sensor behaviour. |
| Kasa `SmartPlug` API deprecated | The `python-kasa` library has deprecated `SmartPlug` in favour of `IotPlug`. A migration warning appears on startup; functionality is unaffected for now. |

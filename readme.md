# 🪐 MLSS Monitor: Mars Life Support Sensor Monitor

A lightweight environmental monitoring system for Raspberry Pi Zero W, designed as a prototype for Mars habitat applications. Logs sensor data, serves a dynamic web UI with real-time and historical plots, and displays status on a small TFT screen.

---

## 📦 Hardware Overview

- **Raspberry Pi Zero W**
- **Adafruit AHT20** — Temperature & Humidity Sensor
- **Adafruit SGP30** — VOC & eCO₂ Air Quality Sensor
- **1.8" TFT LCD** — ST7735 driver (SPI, 128x160)
- **Qwiic/STEMMA QT cables** — Sensors are **daisy-chained via I²C**
- **Redport connectors** for power and signal (optional)

---

## 🔌 Wiring Layout

### 🧠 I²C Sensors (Daisy-Chained)

| Signal | Pi GPIO | Wire Color | Connected To     |
|--------|---------|------------|------------------|
| `3.3V` | Pin 1   | Red        | AHT20 → SGP30    |
| `GND`  | Pin 6   | Black      | AHT20 → SGP30    |
| `SDA`  | Pin 3   | Blue       | AHT20 → SGP30    |
| `SCL`  | Pin 5   | Yellow     | AHT20 → SGP30    |

Use 4-pin JST-SH cables to daisy chain sensors.

### 📺 ST7735 LCD (SPI Display)

| LCD Pin | Pi Pin | GPIO   | Function         |
|---------|--------|--------|------------------|
| `GND`   | 6      | —      | Ground           |
| `VCC`   | 1      | —      | 3.3V Power       |
| `SCL`   | 23     | GPIO11 | SPI Clock        |
| `SDA`   | 19     | GPIO10 | SPI MOSI         |
| `RES`   | 22     | GPIO25 | Reset            |
| `DC`    | 18     | GPIO24 | Data/Command     |
| `CS`    | 24     | GPIO8  | Chip Select      |
| `BL`    | 1      | —      | Always on (3.3V) |

---

## 🗂️ Project Structure

```bash
mlss_monitor-project/
├── mlss_monitor/
│   ├── __init__.py
│   ├── app.py            # Main Flask web app
│   ├── display.py        # LCD screen output
│   ├── logging.py        # Sensor data logging
│   ├── sensors/
│   │   ├── __init__.py
│   │   ├── aht20.py
│   │   └── sgp30.py
│   └── logs/
│       └── default.csv   # Logged data
├── templates/
│   └── dashboard.html    # Web dashboard UI
├── config.py             # Dynaconf config loader
├── settings.toml         # Config values
├── pyproject.toml        # Poetry-managed dependencies
├── poetry.lock
└── mlss-monitor.service  # systemd unit file
```
## 📦 Installation

### 1. Clone the Repository


### 2. Install Dependencies

```bash     
    sudo apt-get install libffi-dev 
    sudo apt-get install python3-dev
    sudo apt-get install python3-pip
    sudo apt-get install python3-venv
    python3 -m venv venv
    source venv/bin/activate
    pip install -U pip
    pip install -U poetry
    poetry install
```



## 🔁 Running on Boot (systemd)

To run automatically as a service:
```
sudo cp mlss-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mlss-monitor
sudo systemctl start mlss-monitor
```

To check service status:
```
sudo systemctl status mlss-monitor
```

## 📁 Data Logging

Data is logged to:
```
mlss_monitor/logs/default.csv
```

You can download it from the web UI or access it directly on the Pi for offline analysis.


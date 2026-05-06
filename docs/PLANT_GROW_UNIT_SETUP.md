# Plant Grow Unit — Setup guide

End-to-end walkthrough: from a clean MLSS install + a Pi Zero in a box, to
a plant being watered and photographed automatically.

> **Hardware reference:** [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md)
> for BOM, wiring tables, and the bench test sequence.

---

## Prerequisites

Before starting:

- **MLSS server** is installed and running on its Pi (see [PRODUCTION.md](PRODUCTION.md)). You should be able to reach the dashboard at `https://mlss.local:5000`.
- **A Pi Zero W (or Pi Zero 2 W)** flashed with Raspberry Pi OS Lite (64-bit recommended on Zero 2 W).
- **The hardware** wired per [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md) — Automation pHAT seated on GPIO header, Seesaw soil sensor on I2C, pump on OUT 1, grow light on the relay, camera on CSI, single multi-port USB wall wart split-wired.
- **Bench-tested** — soil sensor reads at `i2cdetect -y 1`, pump pulses with the test snippet, light flashes, camera captures.

---

## First unit walkthrough

### 1. Get your household enrollment key

**Sign in as an admin user first.** The enrollment-key reveal endpoint
(`/api/grow/enrollment-key/peek-once`) is gated by `require_role("admin")` —
viewers and controllers will get a 403 and the empty-state panel will not
display the key. The reason: the enrollment key authorises
`POST /api/grow/enroll`, which is idempotent by `hardware_serial`. Anyone
holding the key can re-enroll a known serial and rotate that unit's
bearer token, so only admins should ever see it.

Open the MLSS dashboard at `https://mlss.local:5000/grow`. Because no units are enrolled yet, you'll see the empty-state onboarding panel with the enrollment key shown once. **Copy it now and save it somewhere safe** — it's only displayed on first visit, and only to admins.

If you missed it (or are setting up after others have already enrolled units), you'll need to rotate the key via Settings → Grow (this is a Phase 2 feature; for now, edit `app_settings.grow_enrollment_key_hash` directly via SQLite or recreate the DB).

### 2. Drop `/boot/mlss-grow.yaml` on the SD card

Before ejecting the Pi's SD card from your laptop, the boot partition is FAT32 and writeable from any OS. Create the file:

```yaml
mlss_host: mlss.local
enrollment_key: <paste-the-key-from-step-1>
plant:
  name: Tomato 1
  type: tomato       # optional; defaults to 'generic'
  medium: soil       # optional; defaults to 'soil'
```

If WiFi wasn't pre-configured by Raspberry Pi Imager's advanced options, also drop `wpa_supplicant.conf` (standard Pi flow).

### 3. Boot the Pi + install the firmware

Insert the SD card and power on. Once the Pi has joined WiFi, SSH in:

```bash
ssh pi@<pi-zero-ip>
```

Then run the install one-liner:

```bash
curl -k https://mlss.local:5000/api/grow/install.sh | sudo bash
```

This will:

1. apt-install Python 3.11+, libcamera-apps, i2c-tools, build-essential
2. Create the `mlss-grow` system user
3. Download both wheels from the MLSS server
4. Create a venv at `/opt/mlss-grow/.venv`, install both wheels
5. Drop the systemd unit at `/etc/systemd/system/mlss-grow.service`
6. Enable + start the service

The first run of the service reads `/boot/mlss-grow.yaml`, posts to `/api/grow/enroll`, gets a per-unit token, saves it to `/etc/mlss/grow.token`, and **deletes the YAML** so the enrollment key isn't sitting on the SD card.

### 4. Watch it appear in the dashboard

Refresh `https://mlss.local:5000/grow`. Within ~60 seconds, your unit appears as a card with status **Nominal**. Click **Open** to see live readings.

Tail the unit's logs if anything's misbehaving:

```bash
ssh pi@<pi-zero-ip>
sudo journalctl -u mlss-grow -f
```

---

## Adding additional units

For unit #2 onwards, repeat steps 2–4 above with the same enrollment key (one key serves all units in your household). About 3 minutes per unit.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Unit doesn't appear in dashboard after install | WiFi not joined | `journalctl -u mlss-grow -f` on the Pi; check for connect errors |
| Card shows "Offline" with no recent telemetry | WS connection dropped | Restart the service: `sudo systemctl restart mlss-grow`. Check WiFi signal. |
| Soil sensor not detected at boot | I2C cable polarity or address conflict | `sudo i2cdetect -y 1` should show `36`. Swap red/black at JST connector if missing. |
| Photos not appearing | Camera not enabled in raspi-config | `sudo raspi-config` → Interface Options → Camera |
| Pump runs continuously | Wiring backwards (NC instead of NO on relay) | Swap relay output terminal — failsafe is dark/dry, so NO must be open at rest. |

---

## See also

- [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md) — wiring, BOM, bench tests
- [PLANT_GROW_UNIT_USAGE.md](PLANT_GROW_UNIT_USAGE.md) — day-to-day operation
- [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md) — how it works under the hood

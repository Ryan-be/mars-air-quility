# Plant Grow Unit — Setup guide

End-to-end walkthrough: from a clean MLSS install + a Pi Zero in a box, to
a plant being watered and photographed automatically.

> **Hardware reference:** [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md)
> for BOM, wiring tables, and the bench test sequence.

> **Day-to-day operation** is in [PLANT_GROW_UNIT_USAGE.md](PLANT_GROW_UNIT_USAGE.md).
> **How it works under the hood** is in [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md).

---

## Network topology

A grow unit lives on the same LAN as the MLSS server — there is no
internet exposure assumed by the threat model:

```mermaid
graph LR
    subgraph "Home LAN (192.168.x.x)"
        Pi1[Pi Zero W<br/>'Tomato 1']
        Pi2[Pi Zero W<br/>'Basil 1']
        Server[MLSS server<br/>mlss.local:5000/5001]
        Browser[Operator's browser]
    end

    Pi1 -.WSS:5001 + HTTPS:5000.-> Server
    Pi2 -.WSS:5001 + HTTPS:5000.-> Server
    Browser -.HTTPS:5000.-> Server

    style Server fill:#4dacff,color:#000
    style Pi1 fill:#56f000,color:#000
    style Pi2 fill:#56f000,color:#000
```

The MLSS server uses a self-signed cert pinned at install time (TOFU).
LAN-only deployment; no internet exposure assumed. Each grow unit holds
its own argon2-hashed bearer token + a copy of the MLSS server cert at
`/etc/mlss/server.crt`.

---

## Prerequisites

Before starting, check this table:

| What | Why | How to verify |
|---|---|---|
| **MLSS server running** at `https://mlss.local:5000` | Grow unit needs somewhere to enroll, send telemetry, fetch config | Open the dashboard; you should see the existing air-quality view |
| **Admin login** to the MLSS dashboard | The first-boot enrollment-key reveal is gated by `require_role("admin")` | Sign in; navigate to `/grow` — you should see the empty-state panel rather than a 403 |
| **Pi Zero W (or Pi Zero 2 W)** flashed with Raspberry Pi OS Lite | Host for the firmware | `cat /etc/os-release` shows Raspberry Pi OS; SSH works |
| **WiFi configured** on the Pi (Imager advanced options or `wpa_supplicant.conf`) | Firmware connects to MLSS over your home WiFi | `ip addr show wlan0` shows an IP; `ping mlss.local` works |
| **Camera + soil sensor (optional but recommended) wired** per [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md) | Camera on CSI ribbon, Seesaw on I2C `0x36` | `sudo i2cdetect -y 1` shows `36`; `libcamera-jpeg -o test.jpg` works |
| **Pump + grow light (optional)** wired per HARDWARE doc | Required for automated watering / light schedule. Sensors-only is fine to start with — see [Sense-only mode](#sense-only-mode-deploy-without-the-actuator-psu-yet) below | Bench-tested with the snippets in HARDWARE.md → "First-light bench test" |
| **Automation pHAT** seated on the Pi GPIO header (optional) | Required for pump + light. Skip if running sense-only | `python3 -c "import automationhat"` succeeds |

**Bench-tested** before the firmware install: ideally each component has
been verified individually with the snippets in
[HARDWARE.md → First-light bench test](PLANT_GROW_UNIT_HARDWARE.md#first-light-bench-test).
This catches wiring errors early, before they're hidden behind the
`mlss-grow.service` boot logs.

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
# Required
mlss_host: mlss.local              # hostname or IP of the MLSS server
enrollment_key: <paste-the-key>    # household key from step 1

# Plant identity (all optional; sensible defaults)
plant:
  name: Tomato 1                   # display label in the dashboard; defaults to "Unit <serial-tail>"
  type: tomato                     # one of: tomato, basil, lettuce, microgreens, pepper, generic; default 'generic'
  medium: soil                     # one of: soil, coco, rockwool, custom; default 'soil'

# Optional connectivity overrides (rare — defaults are normally fine)
# mlss_port_https: 5000            # HTTPS port for enroll + config + install; default 5000
# mlss_port_wss: 5001              # WSS port for the persistent telemetry channel; default 5001
# verify_ssl: true                 # set false ONLY for dev with no pinned cert; default true once /etc/mlss/server.crt exists
```

**Required vs optional at a glance:**

| Field | Required | Default | Notes |
|---|---|---|---|
| `mlss_host` | Yes | — | Hostname or IP — must be reachable from the Pi at boot |
| `enrollment_key` | Yes | — | Get from the empty-state UI as an admin (step 1). Deleted from disk after first successful enroll |
| `plant.name` | No | `Unit <serial>` | Cosmetic label for the dashboard |
| `plant.type` | No | `generic` | Controls which `grow_plant_profiles` row supplies default tunables |
| `plant.medium` | No | `soil` | Controls calibration defaults from `grow_medium_defaults` |

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
3. Download both wheels (and the systemd unit) from the MLSS server
4. **Verify each downloaded artifact's SHA256** against the manifest at
   `/api/grow/dist/latest` — defends against LAN MITM tampering with
   wheels or the unit file (a tampered unit could expand the firmware's
   privileges, drop `NoNewPrivileges`, etc.). The script aborts if any
   hash doesn't match.
5. **Pin the MLSS server cert** at `/etc/mlss/server.crt` (Trust On First
   Use). Subsequent enrolment + WS + config-pull calls verify against
   this pinned cert, so a future LAN MITM with a swapped cert is
   rejected even if the original `curl -k` install line was unverified.
6. Create a venv at `/opt/mlss-grow/.venv`, install both wheels
7. Drop the systemd unit at `/etc/systemd/system/mlss-grow.service`
8. Enable + start the service

The first run of the service reads `/boot/mlss-grow.yaml`, posts to `/api/grow/enroll` (verifying against the pinned `/etc/mlss/server.crt`), gets a per-unit token, saves it to `/etc/mlss/grow.token` (mode 0600), and **deletes the YAML** so the enrollment key isn't sitting on the SD card.

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

## Sense-only mode (deploy without the actuator PSU yet)

The unit is **safe to deploy with only the Pi powered** — no second
USB port to the load rail, no wires to the pump, no wires to the grow
light. The Pi itself, the camera, and any I2C sensors all run from the
single PSU on Port 1; you finish the actuator side later when you're
ready.

What you'll see in the dashboard immediately:

- The unit's tile renders with live moisture / temperature / lux as
  normal
- Photos capture and upload at the configured cadence
- The **Water 5s** and **Toggle light** buttons are **greyed out**, with
  a tooltip saying "no evidence the actuator is responding — check
  power and wiring"

This is driven by the [capability health watchdog](PLANT_GROW_UNIT_USAGE.md#sense-only-mode-greyed-out-actuator-buttons):
when a command is pushed to an actuator, the server records the
timestamp; if no follow-up evidence (a `grow_watering_events` row for
pump, a telemetry frame with `light_state=1` for light) arrives within
30 seconds, the channel is marked `unresponsive` and the buttons
disable themselves.

### Adding the actuator PSU later

1. Power down the Pi cleanly (`sudo shutdown -h now`).
2. Wire Port 2 of your wall wart through a USB-A breakout into the
   load rail terminal block; tie load-rail GND to the pHAT GND
   terminal.
3. Wire pump red → load-rail +5V via flyback diode → pump black →
   pHAT OUT 1; light red → pHAT RELAY COM, NO terminal → load-rail
   +5V, light black → load-rail GND. Full wiring tables:
   [HARDWARE.md → Wiring sections](PLANT_GROW_UNIT_HARDWARE.md#wiring--soil-sensor-adafruit-seesaw-i2c).
4. Power on. The firmware needs no reconfiguration — the next pump
   pulse or light toggle will succeed, the watchdog flips the channel
   from `unresponsive` back to `connected`, and the buttons un-grey
   themselves on the next page refresh.

There's nothing to toggle on the server side. The watchdog is lazy —
it only consults its memory dict on each `GET /api/grow/units/<id>`,
so when fresh evidence arrives the button comes back automatically.

---

## Token rotation

There are two credentials in play, rotated independently:

### Per-unit bearer token

Each unit has its own argon2-hashed `grow_units.bearer_token_hash`
stored on the unit at `/etc/mlss/grow.token` (mode 0600, owned by
`mlss-grow`). Rotate when:

- You suspect the SD card was compromised
- You've cloned an SD image to a new Pi (the new unit will fail
  authentication; you need a fresh token)
- Routine periodic rotation (yearly is fine for a home deployment)

**To rotate** for a known unit:

```bash
# On the Pi:
sudo rm /etc/mlss/grow.token
sudo systemctl restart mlss-grow
```

The firmware boots, sees no token, falls back to re-reading
`/boot/mlss-grow.yaml` (which is gone after the first enroll). To
re-enroll cleanly: drop a fresh `mlss-grow.yaml` on `/boot/` with the
current household enrollment key, restart the service, and the
firmware re-POSTs to `/api/grow/enroll`. Because enroll is idempotent
by `hardware_serial`, the existing `grow_units` row updates its
`bearer_token_hash` and the same dashboard tile keeps its history
intact.

### Household enrollment key

`app_settings.grow_enrollment_key_hash` authorises **new**
`POST /api/grow/enroll` calls (and idempotent re-enrolls of known
serials, which is why it's admin-only). Rotate via
**Settings → Grow → Rotate enrollment key** in the dashboard. The new
raw key is shown once after rotation.

Rotating the household key does **not** invalidate existing per-unit
tokens — those keep working. To revoke a specific unit, set
`grow_units.is_active=0` directly in SQLite or via the (Phase 4)
fleet management UI.

---

## Decommission a unit

When you're retiring a Pi (broken, repurposed, plant died):

1. **In the dashboard:** open the unit's detail page → **Configure** tab →
   **Diagnostics** subtab → **Danger zone** → **Deactivate unit**. This
   sets `grow_units.is_active=0`, server-side. The unit's per-unit token
   stops authenticating new WS upgrades immediately. Historical telemetry
   and photos are kept for analysis.
2. **On the Pi (optional):** wipe the on-disk credentials:
   ```bash
   sudo systemctl stop mlss-grow
   sudo systemctl disable mlss-grow
   sudo rm -rf /etc/mlss/grow.token /etc/mlss-grow /var/lib/mlss-grow
   ```
   The pinned `/etc/mlss/server.crt` can stay if you'll redeploy this Pi
   to your fleet later.
3. **To permanently delete history** for the unit (after you've extracted
   anything you want for ML training): `DELETE FROM grow_units WHERE
   id=<n>` cascades to telemetry, photos, watering events, capabilities,
   light windows, and errors via `ON DELETE CASCADE` — see
   [DATABASE.md](DATABASE.md). One-shot operation; double-check the unit
   ID first.

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
- [DATABASE.md](DATABASE.md) — schema reference for both server + buffer DBs

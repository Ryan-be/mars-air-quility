# Plant Grow Unit — Usage guide

Day-to-day operation. Audience: anyone using the MLSS dashboard who has at
least one Plant Grow Unit enrolled.

> First-time setup → [PLANT_GROW_UNIT_SETUP.md](PLANT_GROW_UNIT_SETUP.md)
> How it works internally → [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md)

---

## The Grow tab

`https://mlss.local:5000/grow` shows your fleet as cards. Each card represents one growing area (a single plant pot, a microgreens tray, etc.). Counts at the top: total units, online, stale, offline.

Cards are colour-coded:

- **Nominal** (green) — unit reporting recent telemetry, no errors
- **Caution** (amber) — moisture below threshold or other warning condition
- **Stale** (cyan) — last telemetry 30s–5min ago (likely brief WiFi drop)
- **Offline** (orange) — no telemetry for >5 min; unit's local safety loop is still running on the Pi

Click any card to open its detail page.

---

## Identifying which physical unit is which

In a fleet of 5+ units, "which one is *this* card?" is a real question. Click **Identify** on any card (or on the detail-page header). The unit's grow light blinks for 10 seconds — distinct from any normal on/off transition. The button shows a countdown so you know the blink is in progress.

---

## Manual controls

The detail page has a **Quick controls** panel with four buttons:

- **⚡ Identify** — 10s blink (always available)
- **💧 Water 5s** — pulse the pump for 5 seconds. **Disabled during the soak window** — see below.
- **💡 Toggle light** — manual override on/off. The schedule will resume on the next 30s tick.
- **📷 Snap photo** — capture immediately, outside the normal 30-min cadence.

---

## The soak window — why "Water now" sometimes won't fire

The soak window is the minimum enforced cool-down between watering pulses. **Default 30 minutes.** Defends against the failure mode "water doesn't reach the sensor for several minutes → system thinks it's still dry → fires another pulse → drowns the pot."

When the soak window is active, the **Water 5s** button is greyed out and shows the unlock time on hover. Identify, light toggle, and snap photo are unaffected.

To override globally, an admin can change `grow_default_soak_window_min` in Settings → Grow (Phase 2). To override for one specific unit (e.g. a unit with deep slow-draining soil), edit `grow_units.soak_window_min_override` (Phase 2).

---

## Phase changes

Each unit has a current phase: `seedling` / `vegetative` / `flowering` / `fruiting` / `dormant`. The phase determines which light schedule and PID watering profile apply.

In Phase 1 (current MVP), changing phase requires editing the database (`grow_units.current_phase`). Phase 2 will add a phase picker in the Configure tab. A future Phase 4 feature will detect phase transitions automatically from the camera images.

---

## Calibration

The Seesaw soil sensor reports a raw capacitance value (200–2000) that varies with the medium type. To get a meaningful "%" reading on the dashboard, the unit needs two calibration points:

- **Dry**: the raw value when the sensor is in dry medium (or air)
- **Wet**: the raw value just after watering when the medium is fully saturated

Defaults are seeded per medium (`soil`, `coco`, `rockwool`) — usable out of the box. For better accuracy, use the Configure tab's calibration two-step (Phase 2): "Calibrate dry" with sensor dry → "Calibrate wet" after watering. Until then, the dashboard shows raw values for `medium_type='custom'` units that haven't been calibrated.

---

## What happens if MLSS goes offline

The unit's safety loop runs every 30 seconds on the Pi itself, with the last-known config persisted to `/var/lib/mlss-grow/config.json`. If MLSS is unreachable:

- Light schedule continues from local config
- PID watering continues from local config
- Photos are captured but **not** buffered (to save SD-card writes); they resume on reconnect
- Telemetry is buffered to local SQLite (default 7 days)
- On reconnect, buffered telemetry replays in original-timestamp order

Bottom line: if your router dies for the weekend, your plants survive. The dashboard will show "Offline" — clicking refresh after MLSS is back will show the unit transitioning through "Stale" → "Online" as the buffer drains.

---

## Photos

By default each unit captures one photo every 30 minutes during daylight hours (06:00–22:00). All photos are kept on the MLSS Pi at `MLSS_GROW_IMAGES_DIR/unit_NNN/YYYY-MM-DD/HHMMSS.jpg` (default `/var/lib/mlss/grow_images`). Each photo is joined to the closest telemetry reading at capture time so you can later train ML models on (image, soil moisture, temperature) tuples.

**Storage:** ~10 MB/day/unit. At 30 units that's ~110 GB/year. **Strongly recommend a USB SSD** rather than relying on the SD card. To migrate: stop MLSS, `rsync` the existing images dir to the new disk, set `MLSS_GROW_IMAGES_DIR` env var, restart.

---

## Troubleshooting recipes

### Unit went offline overnight

1. Check the Grow card → status should be Offline
2. SSH to the Pi → `sudo journalctl -u mlss-grow -f` shows current state
3. Most often: WiFi flap. Restart networking: `sudo systemctl restart wpa_supplicant`
4. If the service crashed: `sudo systemctl status mlss-grow`. Restart with `sudo systemctl restart mlss-grow`.
5. Last resort: reboot the Pi. The systemd watchdog should have caught wedges, but a hard reboot is safe.

### Pump won't fire even though soil is dry

1. Check the soak window — is the **Water 5s** button greyed out? If yes, you're inside the cool-down. Wait or use the global override (Phase 2).
2. Open the unit's detail page → check capabilities. Is `pump` listed? If not, hardware not detected — check OUT 1 wiring.
3. Check the unit logs for `safety_cap_hit` events — pump may be in cooldown after hitting the 30s pulse cap.

### Plant looks stressed and chart shows constant pump pulses

Either:
- Sensor calibration is off (raw → % mapping wrong) — recalibrate
- PID is over-watering — bump `soak_window_min` for that unit, lower `kp`, or both
- Plant profile is wrong for the actual plant — update `plant_type` in `grow_units`

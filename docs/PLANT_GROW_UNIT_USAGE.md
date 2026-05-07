# Plant Grow Unit — Usage guide

Day-to-day operation. Audience: anyone using the MLSS dashboard who has at
least one Plant Grow Unit enrolled.

> First-time setup → [PLANT_GROW_UNIT_SETUP.md](PLANT_GROW_UNIT_SETUP.md)
> How it works internally → [PLANT_GROW_UNIT_ARCHITECTURE.md](PLANT_GROW_UNIT_ARCHITECTURE.md)
> Database tables behind it all → [DATABASE.md](DATABASE.md)

## Roles required

The grow tab is gated by RBAC (same `viewer` / `controller` / `admin` roles
as the rest of MLSS). At a glance:

| Action | Min role |
|---|---|
| View Grow tab + cards + History | viewer |
| Identify, water-now, light toggle, snap photo | controller |
| Configure tab edits (PID, light windows, calibration, plant profiles) | admin for writes; controller may read |
| Safety override (force pump/light), key rotation, holiday mode | admin |

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

## Sense-only mode (greyed-out actuator buttons)

A unit can come up with the sensors wired but the actuators (pump, grow
light) **not yet powered** — typically the case when you've installed
the Pi + soil sensor but haven't run a separate PSU to the
Automation HAT actuator side yet (see [PLANT_GROW_UNIT_HARDWARE.md](PLANT_GROW_UNIT_HARDWARE.md#power)
for why pump + light need their own rail).

In that state the dashboard:

- Renders the unit's tile normally with live moisture/temperature
- Shows each actuator capability (pump, light) with a small "unresponsive"
  badge next to the tile
- **Greys out the Water 5s and Toggle light buttons** with a tooltip
  explaining "no evidence the actuator is responding — check power and
  wiring"

This is driven by the **capability health watchdog** in
[`mlss_monitor/grow/health_watchdog.py`](../mlss_monitor/grow/health_watchdog.py).
When the server pushes a command to an actuator, it records the
timestamp; if no follow-up evidence (a `grow_watering_events` row for
pump, a telemetry frame with `light_state=1` for light) arrives within
30 seconds, the capability is flipped to `unresponsive` for that GET
response. The next telemetry frame that proves the actuator works
quietly upgrades it back to `connected`.

**You don't toggle a flag.** Wire up the actuator PSU, fire the button
(it'll still fail the first time because health is `unresponsive`),
the unit reports a watering event, and on the next page refresh the
button comes back. This means a half-built unit gets useful sensor
telemetry from day one, then upgrades itself to full control as you
finish the build.

---

## The soak window — why "Water now" sometimes won't fire

The soak window is the minimum enforced cool-down between watering pulses. **Default 30 minutes.** Defends against the failure mode "water doesn't reach the sensor for several minutes → system thinks it's still dry → fires another pulse → drowns the pot."

When the soak window is active, the **Water 5s** button is greyed out and shows the unlock time on hover. Identify, light toggle, and snap photo are unaffected.

To override globally, an admin can change `grow_default_soak_window_min` in Settings → Grow. To override for one specific unit (e.g. a unit with deep slow-draining soil), edit `grow_units.soak_window_min_override` directly in the Configure tab.

---

## The Configure tab

Each unit's detail page has a **Configure** tab where admins (read access
for controllers, write access for admins) can edit the per-unit profile
without touching the database directly. The tab is divided into
sub-editors:

### Plant profile

- **Plant type** — `tomato`, `basil`, `lettuce`, `microgreens`, `pepper`,
  `generic`, plus any custom profiles you've added in
  Settings → Grow → Plant Profiles
- **Phase picker** — `seedling` / `vegetative` / `flowering` / `fruiting`
  / `dormant`. Saving changes the schedule and PID profile on the next
  30s tick. Sets `grow_units.phase_set_by='user'` for audit.
- **Medium type** — `soil` / `coco` / `rockwool` / `custom`

### Light windows

A list of `HH:MM`–`HH:MM` ranges per phase (e.g. `06:00–14:00` and
`16:00–22:00` for a split day). Add/remove rows; the UI saves them as
rows in `grow_light_windows`. NULL/empty means "use the default
`default_light_hours` from `grow_plant_profiles` for this phase".

### PID tunables

Numeric editors for `target_moisture_pct`, `deadband_pct`, `kp`, `ki`,
`kd`, `min_pulse_s`, `max_pulse_s`, `soak_window_min`. Each field is
the per-unit override (`grow_units.<field>_override`); leaving a field
blank inherits from the plant profile, then the household default,
then the firmware default. There's a "reset to inherited" button next
to each.

### Calibration wizard

Two-step flow for accurate moisture %:

1. Place the sensor in dry medium (or air). Click **Calibrate dry** —
   reads the current Seesaw raw value and saves it to
   `grow_units.soil_dry_raw`.
2. Saturate the medium (water until run-off). Click **Calibrate wet**
   — saves `grow_units.soil_wet_raw`.

Until calibration is done for a `medium_type='custom'` unit, the
dashboard shows raw values rather than percentages.

### Safety override (admin only)

A 3-click flow to break out of normal operation: click **Safety
override**, choose action (`force_pump_on` / `force_pump_off` /
`force_light_on` / `force_light_off` / `skip_next_soak`), confirm.
The 3-click guard is deliberate — these bypass PID, soak window, and
schedule. `force_*_on` accepts a `duration_s` and auto-flips off via
a non-blocking timer (see
[`grow_unit/src/mlss_grow/safety_override.py`](../grow_unit/src/mlss_grow/safety_override.py)).

---

## The History tab

`https://mlss.local:5000/grow/units/<id>/history` opens a long-range
history view for one unit:

- **Range selector**: 24h / 7d / 30d / 90d / All
- **Multi-channel chart**: soil moisture %, soil temp, ambient lux,
  air temp/humidity, pump pulses (vertical bars), light state (background
  shading)
- **Downsampling**: For ranges > 7d the server downsamples by averaging
  inside 5-min/30-min/1-h buckets so a 90d view doesn't ship 250k points
  to the browser. The endpoint is
  [`mlss_monitor/routes/api_grow_history.py`](../mlss_monitor/routes/api_grow_history.py).
- **Photo timelapse**: Below the chart, a horizontal strip of thumbnails
  shows every photo taken in the visible range. Click any thumbnail to
  open it full-size with the matching telemetry (joined via
  `grow_photos.telemetry_id`) overlaid. The strip itself is virtualised
  so 30d × 48 photos/day = 1,440 thumbnails scroll smoothly.

---

## Phase changes

Each unit has a current phase: `seedling` / `vegetative` / `flowering` / `fruiting` / `dormant`. The phase determines which light schedule and PID watering profile apply.

The phase picker lives in the Configure tab → Plant profile editor. Saving
sets `grow_units.current_phase` and `grow_units.phase_set_by='user'`. A
future Phase 4 feature will detect phase transitions automatically from the
camera images and set `phase_set_by='image_classifier'`.

---

## Calibration

The Seesaw soil sensor reports a raw capacitance value (200–2000) that varies with the medium type. To get a meaningful "%" reading on the dashboard, the unit needs two calibration points:

- **Dry**: the raw value when the sensor is in dry medium (or air)
- **Wet**: the raw value just after watering when the medium is fully saturated

Defaults are seeded per medium (`soil`, `coco`, `rockwool`) — usable out of the box. For better accuracy, use the [Calibration wizard](#calibration-wizard) in the Configure tab: "Calibrate dry" with sensor dry → "Calibrate wet" after watering. Until then, the dashboard shows raw values for `medium_type='custom'` units that haven't been calibrated.

---

## What happens if MLSS goes offline

The unit's safety loop runs every 30 seconds on the Pi itself, with the last-known config persisted to `/var/lib/mlss-grow/config.json`. If MLSS is unreachable:

- Light schedule continues from local config
- PID watering continues from local config
- Photos taken during the outage are **buffered to disk** at `/var/lib/mlss-grow/photos/` as JPEGs with sidecar JSON metadata, then uploaded oldest-first when the WS reconnects. Bounded by a 1 GB hard size cap (oldest-evicted FIFO when exceeded) and a 7-day age prune that runs on each reconnect — so a multi-day outage won't fill the SD card and an indefinite outage won't accumulate forever. If the byte cap evicts photos a `buffer_eviction` event surfaces in the dashboard.
- Telemetry is buffered to local SQLite (default 7 days)
- On reconnect, buffered telemetry replays in original-timestamp order

Bottom line: if your router dies for the weekend, your plants survive. The dashboard will show "Offline" — clicking refresh after MLSS is back will show the unit transitioning through "Stale" → "Online" as the buffer drains.

**Buffer housekeeping & disk safety.** The local buffer is bounded two ways: an age-based prune that runs on every successful reconnect (driven by `grow_units.buffer_retention_days`, falling back to the firmware default of 7 days), and a hard size cap (100,000 rows / 50 MB) that evicts oldest-first regardless of retention setting. The size cap is defence-in-depth for misconfigured-server / cert-missing / MLSS-permanently-down scenarios where prune never gets to run — it stops the SD card from filling. When eviction does fire, the unit emits a `buffer_eviction` event so the dashboard surfaces the data loss explicitly rather than letting old telemetry silently disappear. Schema reference: [DATABASE.md → grow buffer](DATABASE.md#grow-unit-buffer-database).

**If you edit a unit's config while it's offline:** the change is saved
on the server immediately. As soon as the unit reconnects, the firmware
calls back to `GET /api/grow/units/<id>/config` to pull the latest
values (PID tunables, light windows, calibration) and applies them to
the running safety loop without a service restart. So an admin can
re-tune a unit during a router outage and the changes take effect on
reconnect — no need to wait for the unit to come back online before
saving.

---

## Settings → Grow page

`https://mlss.local:5000/settings/grow` (admin nav link) is the
household-wide control panel:

### Enrollment key rotation

Click **Rotate enrollment key** to generate a new
`app_settings.grow_enrollment_key_hash`. This invalidates the old key
for **new** enrollments only — units already enrolled keep their
per-unit bearer tokens until they're explicitly revoked
(`UPDATE grow_units SET is_active=0`). Use this if you suspect the
old key leaked, or as a routine periodic rotation.

The new raw key is shown once after rotation, same flow as first-boot.

### Default tunables

Edit the household-wide defaults that apply when a per-unit override
isn't set:

| Key | Effect |
|---|---|
| `grow_default_soak_window_min` | Default minutes between PID pulses |
| `grow_default_buffer_retention_days` | Default days the firmware buffer prunes |
| `grow_disk_warn_pct` | Threshold for the "MLSS storage almost full" alert |

These are the second tier in the cascade
(unit-override → plant-profile → app_setting → firmware-default). See
[DATABASE.md → grow_units cascade](DATABASE.md#grow_units--one-row-per-enrolled-pi).

### Plant profile editor

Add/edit/remove rows in `grow_plant_profiles`. Shipped profiles
(`is_shipped=1`) are read-only — clone one to start a custom profile.
Custom profiles are picked up by the Configure tab's plant-type dropdown
on every unit.

### Holiday mode

Toggle to suspend PID watering across the entire fleet (e.g. you're
away and a friend is hand-watering). Light schedules and telemetry
continue normally. Implemented as `app_settings.grow_holiday_mode='1'`;
the firmware checks this flag on every PID tick.

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

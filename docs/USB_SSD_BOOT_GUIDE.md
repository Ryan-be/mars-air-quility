# USB SSD Boot Guide

How to migrate the MLSS server from an SD card to a USB-attached SSD for 24/7 stability.

[Back to main README](../readme.md)

---

## Why migrate

SD cards wear out under sustained random-write workloads. The MLSS server is one
of those workloads:

- SQLite WAL on `data/sensor_data.db` is rewritten every few seconds.
- Sensor poll loop, weather log, inference engine, and (since Phase 4) per-photo
  metadata each emit small writes that hit the same FAT/exFAT/ext4 free-block
  pool over and over.
- gunicorn access logs and `journalctl` for `mlss-monitor` add another steady
  stream.

In real-world MLSS deployments, consumer-grade SD cards have started showing
read-only mode or filesystem corruption inside **2-6 months**. Industrial cards
last longer but still aren't a great fit. A cheap USB SSD is the durable,
boring answer: same throughput once cached, ~100-1000x more write endurance,
and trivial to replace if it does fail.

> **Note on Plant Grow Units:** The Pi Zero W grow units are write-light by
> comparison (2 photos per minute, occasional sensor + outbox writes). SD cards
> are probably fine there for 12+ months. Don't bother migrating a grow unit
> unless one shows wear — see the symptoms list below.

## When to migrate

Migrate proactively if you've been running on SD for more than 3 months.
Migrate immediately if you see any of:

| Symptom | What you'll see |
|---|---|
| I/O errors in dmesg | `dmesg | grep -iE 'i/o error|EXT4-fs error'` returns recent hits |
| Filesystem corruption on boot | `fsck` runs at boot and reports orphaned inodes or bad blocks |
| Slow `systemctl restart` | The unit takes >30s to come back up where it used to take <5s |
| sqlite "database disk image is malformed" | App log shows this on `data/sensor_data.db` |
| Card has gone read-only | `touch /tmp/x` works but `touch /data/x` returns "Read-only file system" |

If any of those land, **stop writing to the card** — copy the DB off via
`scp` or a fresh USB stick before doing anything else.

---

## Hardware shopping list

You don't need anything fancy. The bottleneck on a Pi 4 is USB 3.0 itself
(~400 MB/s real-world); a $30 SATA SSD in a $10 enclosure beats any SD card
on writes.

| Item | Spec | Notes |
|---|---|---|
| USB enclosure | USB 3.0 (5 Gbps), UASP-supporting bridge | Look for ASMedia ASM1153E or JMicron JMS580 chipsets. Avoid no-name JMS567 enclosures — UASP support is patchy. |
| SSD | 240-500 GB SATA III | Crucial MX500, Samsung 870 EVO, WD Blue. NVMe is overkill for a Pi and adds heat. |
| USB cable | Short (15-20 cm) USB 3.0 A-to-microB or A-to-C | Long cables drop link speed; some flaky enclosures need an externally-powered USB hub. |
| Optional: powered USB hub | 5V 2A | Only needed if `dmesg` shows `over-current change` or the SSD disconnects under load. |

**Pi-specific gotchas:**

- A **Pi 4** boots from USB out of the box once the bootloader is updated
  (Bookworm ships with a recent enough bootloader). Older Pi 4s may need
  `sudo rpi-eeprom-update -a` first.
- A **Pi 5** boots from USB by default; no bootloader update needed.
- A **Pi 3** can boot from USB but it's slower and the migration recipe below
  needs minor tweaks (use `PARTUUID=` not `UUID=` in `cmdline.txt`); not
  recommended for the MLSS server.
- A **Pi Zero W / Zero 2 W** has only USB 2.0 OTG; no USB SSD migration is
  worth it. If a grow-unit SD card fails, just reflash it.

---

## Migration recipe (live, no downtime beyond a reboot)

Total time: ~15-30 minutes depending on how full your SD card is.

### Step 1: Flash the SSD with the same OS as the SD card

This gives you a known-good filesystem layout. We'll overwrite the data in
Step 3.

1. Plug the SSD enclosure into a desktop or laptop.
2. Run [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and choose
   the **same** OS the Pi is currently running:
   - For the MLSS server, that's typically **Raspberry Pi OS Lite (64-bit)
     Bookworm**. Confirm with `cat /etc/os-release` on the running Pi.
3. Flash to the SSD. Eject when done.
4. Plug the SSD into a USB 3.0 port on the Pi. **Don't unplug the SD yet.**

### Step 2: Confirm the bootloader can see USB

```bash
# On the running Pi
sudo rpi-eeprom-update           # Confirms current bootloader version
sudo raspi-config
# → Advanced Options → Boot Order → USB Boot
# Reboot prompt will appear; choose "No" — we still need to migrate data
```

After this change the Pi will *try* USB first on boot. If no bootable USB is
found it falls back to the SD card, so this is safe to set before the SSD has
data on it.

### Step 3: Live-copy the running root filesystem to the SSD

```bash
# Mount the SSD's root partition
sudo mkdir -p /mnt/ssd
sudo mount /dev/sda2 /mnt/ssd          # /dev/sda2 = root on the freshly flashed SSD
sudo mount /dev/sda1 /mnt/ssd/boot/firmware  # boot partition (Bookworm path)

# rsync the running system. --exclude'd dirs are virtual or transient.
sudo rsync -aAXv --info=progress2 \
  --exclude='/dev/*' \
  --exclude='/proc/*' \
  --exclude='/sys/*' \
  --exclude='/tmp/*' \
  --exclude='/run/*' \
  --exclude='/mnt/*' \
  --exclude='/media/*' \
  --exclude='/lost+found' \
  / /mnt/ssd/
```

This is safe to run while `mlss-monitor` is up — rsync handles in-flight writes
gracefully, and we'll do a second pass after stopping the service for the
final cut-over.

> **For the database:** to avoid copying a half-flushed WAL, briefly stop the
> service and re-rsync just `/data` after the bulk copy finishes:
> ```bash
> sudo systemctl stop mlss-monitor
> sudo rsync -aAXv /data/ /mnt/ssd/data/
> # Don't restart mlss-monitor — we're about to reboot
> ```

### Step 4: Update fstab and cmdline on the SSD copy

The freshly flashed SSD has its own UUIDs. The rsync just clobbered its
`/etc/fstab` and `/boot/firmware/cmdline.txt` with the SD card's values, which
point at the SD card's partitions. We have to fix that.

```bash
# Get the SSD's PARTUUIDs
sudo blkid /dev/sda1 /dev/sda2
# Example output:
#   /dev/sda1: ... PARTUUID="abc12345-01"
#   /dev/sda2: ... PARTUUID="abc12345-02"
```

Edit `/mnt/ssd/etc/fstab` (the copy on the SSD, not the running system):

```bash
sudo nano /mnt/ssd/etc/fstab
```

Replace the existing PARTUUID values with the new ones from `blkid`. Bookworm
defaults look like this — keep the mount paths and options, change only the
PARTUUIDs:

```
PARTUUID=abc12345-01  /boot/firmware  vfat    defaults          0  2
PARTUUID=abc12345-02  /               ext4    defaults,noatime  0  1
```

Edit `/mnt/ssd/boot/firmware/cmdline.txt` and update `root=PARTUUID=...` to
the SSD's root partition PARTUUID:

```bash
sudo nano /mnt/ssd/boot/firmware/cmdline.txt
# Change:  root=PARTUUID=<old-sd-card-id>-02
# To:      root=PARTUUID=<new-ssd-id>-02
```

`cmdline.txt` is one long line — don't insert newlines.

### Step 5: Reboot

```bash
sudo umount /mnt/ssd/boot/firmware
sudo umount /mnt/ssd
sudo reboot
```

The Pi will try USB first (per Step 2), find the SSD, and boot from it. If
something is wrong with the SSD it falls back to the SD card and you're back
where you started — nothing destructive has happened to the SD.

---

## Validation

After the reboot, confirm root is on the SSD:

```bash
df -h /
# Should show /dev/sda2 (or similar /dev/sd*) — NOT /dev/mmcblk0p2

mount | grep ' / '
# Should show /dev/sda2 on / type ext4
```

Confirm USB enumerated cleanly:

```bash
dmesg | grep -iE 'usb|sda' | tail -40
# Should show normal "USB 3.0 SuperSpeed" enumeration; no
# "reset SuperSpeed" / "device disconnected" / "over-current" warnings
```

Confirm `mlss-monitor` is happy:

```bash
sudo systemctl status mlss-monitor
sudo journalctl -u mlss-monitor -n 50 --no-pager
# Look for the usual "Auth ENABLED" / "Background services started" lines
```

A quick I/O smoke test:

```bash
# Write 100 MB to the SSD-backed root, time it
sudo dd if=/dev/zero of=/tmp/io_test bs=1M count=100 conv=fdatasync 2>&1 | grep MB
# Should report 100+ MB/s on USB 3.0; SD cards typically do 10-30 MB/s
sudo rm /tmp/io_test
```

---

## Rollback

The SD card is still bootable and untouched (you only stopped writing to it
during Step 3's final rsync). To roll back:

1. `sudo shutdown now`
2. Pull the USB SSD.
3. Power on. The Pi falls through USB boot to SD, exactly as it did before.

If you migrated cleanly and want to free up the SD card slot for something
else, leave the SSD as the only boot medium. Keep the old SD card in a drawer
for a few weeks before reformatting it — handy as a known-good "the SSD just
died, get me back online in 60 seconds" recovery option.

---

## After-migration cleanup

Once you've been running on SSD for a week and everything looks healthy:

- Add the SSD's serial number to your inventory notes (`lsblk -o NAME,SIZE,SERIAL`).
- Re-check the daily DB backup cron job in [`PRODUCTION.md`](PRODUCTION.md) —
  with `data/` now on a single, larger device, you may want to back up to
  external storage (a second USB stick, a NAS, or `rsync.net`) rather than
  to the same drive.
- Optional: enable `fstrim.timer` for weekly TRIM:
  ```bash
  sudo systemctl enable --now fstrim.timer
  systemctl list-timers fstrim.timer
  ```
  The default Raspberry Pi OS install enables this for you on Bookworm; check
  before changing anything.

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| Pi hangs at "Waiting for root device" | `cmdline.txt` PARTUUID still points at SD | Boot off SD, re-edit `/boot/firmware/cmdline.txt` on the SSD copy |
| Boot loop, then drops to emergency shell | `fstab` PARTUUIDs wrong | Same fix — fix `/etc/fstab` on the SSD root |
| SSD disconnects under load | Enclosure under-powered or buggy UASP | Try a different USB port, a powered hub, or add `usb-storage.quirks=<vid:pid>:u` to `cmdline.txt` to disable UASP for that device |
| `dmesg` shows `over-current change` | Cable too long or enclosure draws >900 mA | Use a powered USB hub |
| Boot is *slower* on SSD than SD | Bootloader is still waiting for USB to enumerate | First boot can take +5-10s; it stabilises after that. If persistent, update bootloader: `sudo rpi-eeprom-update -a` |

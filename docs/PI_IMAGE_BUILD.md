# Building the MLSS Pi SD-card image

How to build a custom Raspberry Pi OS `.img.xz` pre-loaded with the
`mlss-grow` firmware so a fresh Pi Zero W can be flashed, configured
once with a `mlss-grow.yaml`, and brought online without SSH.

[Back to main README](../readme.md)

---

## Why a custom image

Without it, provisioning a new grow unit means:

1. Flash Pi OS Lite via `rpi-imager`
2. SSH in, run `bash scripts/setup_pi.sh`
3. Wait 10-30 minutes for `pip install` (Pi Zero W is slow)
4. Drop the yaml, restart the service, hope nothing was missed

That's fine for one or two units; it's painful for ten. The custom
image bakes everything in: ffmpeg, libcamera-apps, picamera2, the
mlss-grow venv, the systemd unit. First boot reads `/boot/mlss-grow.yaml`
and starts the service. Total provisioning time per unit drops from
~30 minutes to ~3 minutes (mostly the Pi booting + enrolling against
the MLSS server).

---

## Prerequisites

- A **Linux box** (Ubuntu / Debian / Fedora). pi-gen uses `chroot` +
  `binfmt_misc` to run aarch64 binaries on the build host, neither of
  which works on macOS or Windows. If you're on macOS / Windows, run
  this in a Linux VM (UTM, Multipass, WSL2 with `binfmt-support`).
- ~10 GB free disk for the build cache + output.
- 4-8 GB RAM (the build does parallel apt installs in the chroot).
- `poetry` installed locally — `build_pi_image.sh` calls
  `scripts/build_local_wheels.sh` internally to produce the
  mlss-grow + mlss-contracts wheels that get baked into the image.
  No public package index is consulted for our two packages at
  provision time. See [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md)
  for the local-wheels build flow.

The build itself takes 30-60 minutes depending on hardware. CI doesn't
build it — it's a manual maintainer step before cutting an image
release.

---

## Build

```bash
cd /path/to/mars-air-quility
bash scripts/build_pi_image.sh
```

What this does:

1. **Builds local wheels** — runs `scripts/build_local_wheels.sh` to
   produce `mlss_grow-*.whl` + `mlss_contracts-*.whl` in `dist/wheels/`
   (path-dep stripped, ready for offline `pip install`).
2. **Clones pi-gen** (the official Pi OS image builder, ~50 MB) into
   `dist/pi-image-build/pi-gen/`. Cached for subsequent builds.
3. **Symlinks `scripts/stage-mlss-grow/`** into pi-gen as a custom
   stage that runs after pi-gen's stage 0 / 1 / 2 (the lite Pi OS
   rootfs). Stages 3-5 (full desktop) are SKIPPED.
4. **Writes `pi-gen/config`** — image name, locale, timezone, default
   user (`mlss` with a placeholder password — change it on first
   login), and exports `MLSS_LOCAL_WHEELS_DIR` so the stage scripts
   know where to pick up the wheels.
5. **Runs `pi-gen/build.sh`** — apt-installs system packages into the
   chroot, copies the local wheels into `/tmp/wheels/` of the rootfs,
   pip-installs from those wheels (no public-index lookup for our two
   packages — transitive deps still come from PyPI / piwheels), drops
   the systemd unit, the firstboot hook, and the yaml template into
   `/boot/`.
6. **Compresses + outputs** the image to
   `dist/mlss-pi-os-<version>.img.xz` (~700 MB).

### Pinning a specific firmware version

The version is whatever's in `grow_unit/pyproject.toml` and
`contracts/pyproject.toml` at build time. Bump those before running
the build script (see [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md)).

### Bumping the image version tag

```bash
IMAGE_VERSION=0.2.0 bash scripts/build_pi_image.sh
# → dist/mlss-pi-os-0.2.0.img.xz
```

---

## What ships in the image

The stage-mlss-grow customisation is structured as one substage:

| Layer | Contents |
|---|---|
| `00-packages` (apt) | `python3`, `python3-pip`, `python3-venv`, `python3-picamera2`, `libcamera-apps`, `i2c-tools`, `build-essential`, `libffi-dev`, `libjpeg-dev`, `ffmpeg` |
| `01-run.sh` (host-side) | Stages our service unit + firstboot script + yaml template + locally-built wheels into `${ROOTFS_DIR}/tmp/` |
| `01-run-chroot.sh` (chroot) | Creates the `mlss-grow` system user, `/opt/mlss-grow/.venv` venv, `pip install mlss-grow mlss-contracts` from `/tmp/wheels` (via piwheels for transitive ARM wheels), drops the systemd unit (NOT enabled), enables I2C, hooks `/etc/rc.local` to call firstboot |

After build, the image contains:

| Path | What |
|---|---|
| `/opt/mlss-grow/.venv/` | The mlss-grow venv (with picamera2 visible via `--system-site-packages`) |
| `/etc/systemd/system/mlss-grow.service` | systemd unit, NOT enabled — first-boot enables it |
| `/usr/local/sbin/mlss-firstboot.sh` | First-boot hook (idempotent — marks itself complete) |
| `/etc/rc.local` | Calls the first-boot hook on every boot (it self-shortcircuits) |
| `/boot/mlss-grow.yaml.template` | Template the operator copies + edits |
| `/var/lib/mlss-grow/` (mode 700, owner mlss-grow) | State + buffer DB will land here |

---

## Operator flow (per Pi)

1. **Flash** the image to an SD card via `rpi-imager`'s "use custom" option.
2. **Mount the boot partition** (the FAT32 partition shows up on Mac /
   Windows / Linux when the SD card is plugged in).
3. **Copy `mlss-grow.yaml`** onto the boot partition. The template is
   already there — copy `mlss-grow.yaml.template` to `mlss-grow.yaml`
   in the same dir, edit in:
   ```yaml
   mlss_host: mlss.local
   mlss_port: 5000
   enrollment_key: <one-shot key from MLSS UI>
   label: "Tomato 1 — kitchen"
   ```
4. **Eject + insert into Pi, power on.**
5. **Wait ~60 seconds.** First boot expands the filesystem, runs
   firstboot.sh, enables `mlss-grow.service`, the firmware enrols
   against the MLSS server. Watch the unit appear in the fleet view.

The yaml is read once. Once enrolled, the firmware persists the
bearer token to `/etc/mlss/grow.token` (mode 0600) and the yaml can
be deleted from the boot partition (it'll be ignored on subsequent
boots anyway, since `/var/lib/mlss-grow/.firstboot-done` shortcircuits).

---

## Distributing a built image

The image is a private build artefact today — there's no automated
publish step (pi-gen needs Linux + ~30 min of CPU + sudo, none of
which the standard GH-hosted runner does well, and we're not pushing
images to a public location anyway).

Local handoff is straightforward:

1. Build on a Linux box: `IMAGE_VERSION=0.1.0 bash scripts/build_pi_image.sh`
2. Compute SHA256: `sha256sum dist/mlss-pi-os-0.1.0.img.xz`
3. Move the `.img.xz` + the SHA256 into wherever the operator picks it up
   (a shared drive, a laptop, an internal release server).

If/when distribution policy changes, that's a separate ticket.

---

## Troubleshooting the build

| Symptom | Cause | Fix |
|---|---|---|
| `pi-gen requires Linux` | Running on macOS / Windows | Use a Linux VM (UTM / Multipass / WSL2) |
| `chroot: cannot run binary format` | binfmt_misc not registered for aarch64 | `sudo apt install qemu-user-static binfmt-support` |
| Build hangs at "Downloading wheels" | piwheels rate-limited | Retry; if persistent, comment out the `--extra-index-url` line in `01-run-chroot.sh` (will fall back to compiling from source — slow but works) |
| `Disk full` mid-build | pi-gen needs ~10 GB free | Free disk or move `WORK_DIR` to a larger volume |
| Image boots but firstboot.sh never runs | `/etc/rc.local` not executable | Mount the SD card, `chmod +x /etc/rc.local`, retry |
| Image boots, mlss-grow.service fails | yaml malformed | SSH in (`ssh mlss@<ip>`, password `mlss-grow-default-CHANGE-ME` — change after first login), check `journalctl -u mlss-grow -n 100` |

---

## Related

- [`scripts/build_pi_image.sh`](../scripts/build_pi_image.sh) — the wrapper
- [`scripts/build_local_wheels.sh`](../scripts/build_local_wheels.sh) — produces the wheels baked into the image
- [`scripts/stage-mlss-grow/`](../scripts/stage-mlss-grow/) — the pi-gen stage
- [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md) — local-only release flow + version bumps
- [`docs/PLANT_GROW_UNIT_SETUP.md`](PLANT_GROW_UNIT_SETUP.md) — operator first-boot flow (uses this image)
- [`docs/PLANT_GROW_UNIT_HARDWARE.md`](PLANT_GROW_UNIT_HARDWARE.md) — hardware stack the image targets
- [`grow_unit/install.sh`](../grow_unit/install.sh) — the manual install path (alternative to the image flow)
- [pi-gen upstream](https://github.com/RPi-Distro/pi-gen) — the tool we wrap

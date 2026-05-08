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
- A valid `mlss-grow` package on PyPI — see
  [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md). The image runs
  `pip install mlss-grow` at build time, so this depends on item #4
  having shipped first.

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

1. **Clones pi-gen** (the official Pi OS image builder, ~50 MB) into
   `dist/pi-image-build/pi-gen/`. Cached for subsequent builds.
2. **Symlinks `scripts/stage-mlss-grow/`** into pi-gen as a custom
   stage that runs after pi-gen's stage 0 / 1 / 2 (the lite Pi OS
   rootfs). Stages 3-5 (full desktop) are SKIPPED.
3. **Writes `pi-gen/config`** — image name, locale, timezone, default
   user (`mlss` with a placeholder password — change it on first
   login).
4. **Runs `pi-gen/build.sh`** — apt-installs system packages into the
   chroot, pip-installs `mlss-grow` from PyPI, drops the systemd unit,
   the firstboot hook, and the yaml template into `/boot/`.
5. **Compresses + outputs** the image to
   `dist/mlss-pi-os-<version>.img.xz` (~700 MB).

### Pinning a specific firmware version

Default: latest from PyPI.

```bash
MLSS_GROW_VERSION=0.2.0 bash scripts/build_pi_image.sh
```

When cutting an image release, always pin so the image is reproducible.

### Bumping the image version tag

```bash
IMAGE_VERSION=0.2.0 MLSS_GROW_VERSION=0.2.0 bash scripts/build_pi_image.sh
# → dist/mlss-pi-os-0.2.0.img.xz
```

---

## What ships in the image

The stage-mlss-grow customisation is structured as one substage:

| Layer | Contents |
|---|---|
| `00-packages` (apt) | `python3`, `python3-pip`, `python3-venv`, `python3-picamera2`, `libcamera-apps`, `i2c-tools`, `build-essential`, `libffi-dev`, `libjpeg-dev`, `ffmpeg` |
| `01-run.sh` (host-side) | Stages our service unit + firstboot script + yaml template into `${ROOTFS_DIR}/tmp/` |
| `01-run-chroot.sh` (chroot) | Creates the `mlss-grow` system user, `/opt/mlss-grow/.venv` venv, `pip install mlss-grow` (via piwheels for ARM wheels), drops the systemd unit (NOT enabled), enables I2C, hooks `/etc/rc.local` to call firstboot |

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

## Publishing a release

This is a **manual** process today (no GitHub Actions workflow because
pi-gen needs Linux + ~30 min of CPU + sudo, none of which the standard
GH-hosted runner does well).

1. Build on a Linux box: `IMAGE_VERSION=0.1.0 MLSS_GROW_VERSION=0.1.0 bash scripts/build_pi_image.sh`
2. Compute SHA256: `sha256sum dist/mlss-pi-os-0.1.0.img.xz`
3. Tag: `git tag pi-image-v0.1.0 && git push --tags`
4. Create a GitHub Release attached to the tag at
   <https://github.com/Ryan-be/mars-air-quility/releases/new?tag=pi-image-v0.1.0>
5. Upload the `.img.xz` and the SHA256 as release assets.
6. In the release notes, paste the SHA256 + a quick changelog of what
   changed since the last image.

A future Phase 5 enhancement would automate this on a self-hosted
Linux runner (the build is too slow for GH-hosted runners), but for
now manual is fine — image releases are rare (every few months).

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
- [`scripts/stage-mlss-grow/`](../scripts/stage-mlss-grow/) — the pi-gen stage
- [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md) — how `mlss-grow` lands on PyPI in the first place (item #4)
- [`grow_unit/install.sh`](../grow_unit/install.sh) — the manual install path the image replaces
- [pi-gen upstream](https://github.com/RPi-Distro/pi-gen) — the tool we wrap

# mlss-grow

Firmware for a Plant Grow Unit running on a Raspberry Pi Zero W with the
Pimoroni Automation pHAT. Talks to the MLSS server over a single
authenticated WebSocket per unit.

This package is built into a wheel by `scripts/build_grow_wheel.sh` and
served from the MLSS HTTP server at `/api/grow/dist/` for installation
on Pi Zeros via the install script.

Install (dev, on a non-Pi machine — Pi-only deps are skipped via markers):
    poetry install

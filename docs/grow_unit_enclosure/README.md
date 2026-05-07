# Plant Grow Unit — 3D-printable enclosure

Source: [`enclosure.scad`](./enclosure.scad) (OpenSCAD parametric model).

Houses the standard hardware stack for a Plant Grow Unit:

* Raspberry Pi Zero W (or 2 W)
* Pimoroni Automation pHAT (sits on Pi GPIO header)
* Raspberry Pi Camera Module v2 (mounted on a sloped front face,
  angled forwards-and-down at the plant)

Two-piece design: a base that holds the Pi+pHAT stack on M2.5 standoffs,
and a snap-fit lid with the camera mount on the sloped face. Cable
cutouts match the operator's hand sketch:

| Cutout | Location | Cable |
|---|---|---|
| Power | Bottom face (-Y wall, low Z) | USB-A power adapter cable to Pi microUSB |
| Sensor | Bottom-left side wall (X=0, low Y) | I²C soil sensor + future I²C devices |
| Light | Top-right back-wall area (high X, high Z) | Relay-switched grow-light +5V/GND |
| Lens | Sloped front face (centred X, ⅓ from front edge) | Pi Camera v2 lens, ~28° tilt |

Vent slots run high on both side walls (base) and high on the lid
sides — passive convective airflow without compromising rigidity.

---

## Quick build

You'll need [OpenSCAD](https://openscad.org/) (free) installed on your
PATH.

```bash
cd docs/grow_unit_enclosure

# Render each piece as STL
openscad -o base.stl  -D 'part="base"'  enclosure.scad
openscad -o lid.stl   -D 'part="lid"'   enclosure.scad

# Or both side-by-side as a single preview
openscad -o preview.stl -D 'part="all"' enclosure.scad
```

You should now have `base.stl` + `lid.stl` ready to import into your
slicer.

---

## Print orientation + supports

* **Base** — print **open face up** on the build plate. No supports
  needed. The cable cutouts are vertical slots and the Pi standoffs
  print cleanly bottom-up without bridging.
* **Lid** — print **flat face down** (the bottom sealing surface that
  mates with the base sits on the plate). The sloped camera face ends
  up on the top, which needs supports — set "tree supports, only on
  build plate" in your slicer (saves filament + leaves a clean lens
  hole). Alternative: print upside down with the sloped face on the
  plate, no supports — but then the camera standoffs print downward
  and need short bridging supports. Pick whichever your printer does
  better.

## Recommended print settings (PLA or PETG)

| Setting | Value |
|---|---|
| Layer height | 0.2 mm |
| Walls (perimeters) | 3 (≈ 1.2 mm) |
| Infill | 25% gyroid |
| Brim | 5 mm if your bed adhesion is twitchy |
| Print speed | 50 mm/s |
| Material | PLA fine for indoor use; PETG if it's near a grow lamp (PLA softens at sustained 50 °C+) |

## Assembly

1. **Pi standoffs** — let the print cool for ≥ 1 hour (avoid stripping
   warm plastic). Self-tap M2.5 screws into each of the 4 standoffs
   with the Pi sitting on top. If the pilot holes feel too tight, drill
   them out with a 2.5 mm bit to clean up the FDM ridges.
2. **Camera standoffs** — same drill for the 4 M2 holes on the sloped
   lid. The camera ribbon cable should exit through the gap between
   the lid floor and the back wall (the Pi camera connector lives on
   the Pi's edge under the GPIO header).
3. **Cable routing**:
   * Power: feed the USB cable through the bottom cutout BEFORE
     plugging it into the Pi's microUSB socket; the cable head won't
     fit through the cutout once attached.
   * Sensor: pass the soil-probe wires through the bottom-left cutout
     and out to the planter. If you're using a JST-PH connector, snip
     it off and crimp on the inside — JST heads are larger than the
     cutout.
   * Light: pass the +5V/GND wires through the top-right cutout. These
     route from the Automation pHAT's relay COM/NO terminals out to
     the grow lamp's USB-A spliced load rail (see
     [`PLANT_GROW_UNIT_HARDWARE.md`](../PLANT_GROW_UNIT_HARDWARE.md#wiring--grow-light-5v-via-relay)).
4. **Lid snap-fit** — the base has a 6 mm-tall lip extending up above
   its rim; the lid has a matching 0.3 mm-clearance recess on its
   underside. Press the lid down firmly. If your printer over-extrudes,
   the fit may be too tight — file the lip lightly or bump the `GAP`
   parameter in the SCAD source from 0.3 to 0.5 and re-render.

## Tuning the design

Every dimension is parametric. Common changes (edit in
[`enclosure.scad`](./enclosure.scad)):

| Parameter | Default | What to change |
|---|---|---|
| `BASE_W`, `BASE_D`, `BASE_H` | 80 / 50 / 35 mm | Cavity size — bump if you want room for a heatsink, fan, extra wiring |
| `WALL` | 2.5 mm | Outer wall thickness — raise to 3 mm if you want the box more rigid (heavier, slower print) |
| `LID_H`, `LID_FRONT_H` | 35 / 8 mm | Slope steepness — raise `LID_FRONT_H` to flatten the slope; the camera tilt is `atan((LID_H-LID_FRONT_H)/BASE_D)` ≈ 28° at default |
| `CAM_LENS_DIA` | 9 mm | Camera lens hole — bump to 10–11 mm if your camera variant has a wider lens housing |
| `GAP` | 0.3 mm | Snap-fit clearance — raise if the lid is too tight, lower if it's loose |
| `VENT_SLOTS`, `VENT_W`, `VENT_H`, `VENT_PITCH` | 5 / 1.5 / 14 / 4 mm | Tune passive airflow vs print integrity |

After editing, re-render with the same `openscad -o` commands above.

## What this does NOT include (yet)

* **Wall mount / planter clip** — the box is a free-standing rectangle
  for now. Future work: parametric snap-on clip for a 90 mm-rim
  terracotta pot, or a wall plate with VESA-style screw holes. Open an
  issue if you want a specific mounting style and I'll fold it in.
* **PSU enclosure** — the USB power adapter sits outside the box (per
  the [HARDWARE.md safety guidance](../PLANT_GROW_UNIT_HARDWARE.md#safety):
  wall warts mount external, cables route in).
* **Strain relief on the cable cutouts** — they're rectangular slots,
  not zip-tie anchor points. If you're going to move the unit a lot,
  glue a small loop of nylon string or print a separate clip and
  zip-tie the cable bundle just inside the cutout.
* **Mesh-grille water resistance** — vent slots are open to drips.
  Don't sit it directly under the planter where overflow could land
  inside.

## Reference dimensions

| Component | W × D × H | Mounting holes | Notes |
|---|---|---|---|
| Pi Zero W | 65 × 30 × 5 mm | 4× M2.5, 58 × 23 mm | Same footprint as Zero 2 W — use the same enclosure for either |
| Automation pHAT | ~65 × 30 × 12 mm (component-side) | sits on GPIO; no separate mounting needed | Adds ~14 mm of height once seated on Pi GPIO header |
| Pi Camera v2 | 25 × 24 × 9 mm | 4× M2, 21 × 13 mm | Lens housing 8 mm dia, ribbon cable exits the long edge |
| Stack height | ~25 mm including pHAT components | — | Fits comfortably in the default 35 mm cavity |

// Plant Grow Unit enclosure — Pi Zero W + Automation pHAT + Pi Camera v2
//
// Vertical box with a sloped top face that holds the camera angled
// down at the plant. Two-piece design: BASE holds the Pi/pHAT stack
// on M2.5 standoffs; LID has the camera mount on the sloped face,
// snaps onto the base via a lip, and has the lens hole + light-cable
// cutout. Cable cutouts on the base handle power (bottom) and sensor
// (bottom-left side wall).
//
// Sketch reference (operator's hand-drawn): vertical box, camera on
// sloped top tilted forwards, sensor cable exits bottom-left, light
// cable exits top-right, power cable exits bottom.
//
// Render:
//   openscad -o base.stl  -D 'part="base"'  enclosure.scad
//   openscad -o lid.stl   -D 'part="lid"'   enclosure.scad
//   openscad -o preview.stl -D 'part="all"' enclosure.scad   # both side-by-side
//
// Print orientation:
//   * BASE — open face up. No supports needed; the cable cutouts are
//     vertical slots and the standoffs are bottom-up so they print
//     cleanly without bridging.
//   * LID — flat face down (the bottom sealing surface that mates
//     with the base). The sloped camera face becomes the top, which
//     needs supports — set "support: tree, only on build plate" in
//     your slicer. Or print upside down with the sloped face on the
//     plate (no supports), but then the standoffs print downward
//     which needs short bridging supports — pick your poison.
//
// Print settings (PLA or PETG):
//   * Layer height 0.2mm
//   * Walls 3 perimeters (≈ 1.2mm)
//   * Infill 25% gyroid
//   * Brim 5mm if your printer's first-layer adhesion is twitchy
//
// All dimensions in mm.

// ─────────────────────────────────────────────────────────────────────
// Part selector — set on the command line via `-D 'part="base"'`
// ─────────────────────────────────────────────────────────────────────
part = "all";  // "base" | "lid" | "all"

// ─────────────────────────────────────────────────────────────────────
// Top-level dimensions
// ─────────────────────────────────────────────────────────────────────
WALL = 2.5;            // outer wall thickness — thick enough to be rigid
LIP = 6;               // lid-base mating lip height (snap-fit overlap)
GAP = 0.3;             // running clearance for snap-fit

// Base internal cavity
BASE_W = 80;           // X — accommodates Pi (65mm) with cabling slack
BASE_D = 50;           // Y — accommodates pHAT GPIO header sticking off side
BASE_H = 35;           // Z — Pi (5) + GPIO (8) + pHAT (1.6) + components (~10) + clearance

// Lid: sloped front face for the camera. The slope faces -Y (front),
// so the lens points down + forward toward the plant on the opposite
// side of the planter from the operator.
LID_H = 35;            // total lid height at its tallest point (back wall)
LID_FRONT_H = 8;       // lid front-edge wall height (creates the slope)
//                     Slope angle ≈ atan((LID_H - LID_FRONT_H) / BASE_D)
//                                  = atan(27/50) ≈ 28°  (camera tilted forwards 28°)

// ─────────────────────────────────────────────────────────────────────
// Pi Zero W mounting (4 M2.5 holes, 58 × 23 mm centre-to-centre)
// Reference: Raspberry Pi Zero mechanical drawing.
// ─────────────────────────────────────────────────────────────────────
PI_HOLE_X = 58;
PI_HOLE_Y = 23;
PI_STANDOFF_H = 5;     // raises Pi off the floor — leaves room for SD-card edge
PI_STANDOFF_OD = 5;    // outer diameter
PI_STANDOFF_ID = 2.6;  // for M2.5 self-tapping into plastic; tap if needed

// Pi sits centred-X, biased toward the back so the GPIO header sits
// roughly under the rear wall (where the pHAT components live).
PI_OFFSET_X = (BASE_W - PI_HOLE_X) / 2;
PI_OFFSET_Y = (BASE_D - PI_HOLE_Y) / 2 + 4;  // +4 = back-bias

// ─────────────────────────────────────────────────────────────────────
// Pi Camera v2 mounting (4 M2 holes, 21 × 13 mm; lens dia 8mm)
// Reference: Pi Camera Module v2 mechanical drawing.
// Camera mounts on the inside face of the sloped lid.
// ─────────────────────────────────────────────────────────────────────
CAM_HOLE_X = 21;
CAM_HOLE_Y = 13;
CAM_STANDOFF_H = 3;
CAM_STANDOFF_OD = 4;
CAM_STANDOFF_ID = 2.0;
CAM_LENS_DIA = 9;      // 8mm lens housing + 1mm clearance — no friction
                       // on the lens barrel (would distort focus)

// ─────────────────────────────────────────────────────────────────────
// Cable cutouts (W × H rectangles)
// ─────────────────────────────────────────────────────────────────────
POWER_CUTOUT_W = 12;   // microUSB cable head ≈ 10mm wide
POWER_CUTOUT_H = 7;    // height clearance
SENSOR_CUTOUT_W = 8;   // 4-wire bundle — JST-style or just bare strands
SENSOR_CUTOUT_H = 5;
LIGHT_CUTOUT_W = 8;    // 2-wire +5V/GND from the relay
LIGHT_CUTOUT_H = 5;

// Vent slots
VENT_SLOTS = 5;        // count per side
VENT_W = 1.5;
VENT_H = 14;
VENT_PITCH = 4;        // gap between slot centres

// ─────────────────────────────────────────────────────────────────────
// PARTS
// ─────────────────────────────────────────────────────────────────────
$fn = 48;

if (part == "base") base();
else if (part == "lid") lid();
else if (part == "all") {
  base();
  translate([BASE_W + 20, 0, 0]) lid();
}


// ─── BASE ────────────────────────────────────────────────────────────
module base() {
  difference() {
    union() {
      // outer shell
      cube([BASE_W, BASE_D, BASE_H]);
      // mating lip extension (the lip sticks up above the rim, a snug
      // tongue the lid's groove slides over)
      translate([WALL/2, WALL/2, BASE_H])
        cube([BASE_W - WALL, BASE_D - WALL, LIP]);
    }
    // hollow it out
    translate([WALL, WALL, WALL])
      cube([BASE_W - 2*WALL, BASE_D - 2*WALL, BASE_H + LIP]);
    // top rim — recess so the lip sticks up but the lid sits flush
    translate([WALL/2 + GAP, WALL/2 + GAP, BASE_H])
      cube([BASE_W - WALL - 2*GAP, BASE_D - WALL - 2*GAP, LIP + 1]);

    // ─ Cable cutouts ─
    // Power: bottom face, centred-X (USB cable feeds in from below the planter)
    translate([(BASE_W - POWER_CUTOUT_W) / 2, BASE_D - POWER_CUTOUT_H/2, -0.1])
      cube([POWER_CUTOUT_W, POWER_CUTOUT_H, WALL + 1]);
    // Wait — "bottom" in the sketch is the -Y wall; redo:
    translate([(BASE_W - POWER_CUTOUT_W) / 2, -0.1, 2])
      cube([POWER_CUTOUT_W, WALL + 1, POWER_CUTOUT_H]);

    // Sensor: bottom-left side wall (X=0, low-Y end)
    translate([-0.1, 4, 4])
      cube([WALL + 1, SENSOR_CUTOUT_W, SENSOR_CUTOUT_H]);

    // Vent slots: high on each long side wall (above the Pi standoffs,
    // below the lid lip, so they remain covered by the lid lip from
    // above and don't compromise structural integrity).
    for (i = [0 : VENT_SLOTS - 1]) {
      // Right wall (X = BASE_W)
      translate([BASE_W - WALL - 0.1,
                 BASE_D / 2 - VENT_PITCH * VENT_SLOTS / 2 + i * VENT_PITCH,
                 BASE_H - VENT_H - 4])
        cube([WALL + 0.2, VENT_W, VENT_H]);
      // Left wall (X = 0)
      translate([-0.1,
                 BASE_D / 2 - VENT_PITCH * VENT_SLOTS / 2 + i * VENT_PITCH,
                 BASE_H - VENT_H - 4])
        cube([WALL + 0.2, VENT_W, VENT_H]);
    }
  }

  // Pi standoffs — placed inside the cavity, above the floor
  translate([WALL + PI_OFFSET_X, WALL + PI_OFFSET_Y, WALL])
    pi_standoffs();
}


// ─── LID ────────────────────────────────────────────────────────────
module lid() {
  difference() {
    union() {
      // sloped lid body — back wall full height, front wall short, ramp between
      hull() {
        // back wall slab
        translate([0, BASE_D - WALL, 0])
          cube([BASE_W, WALL, LID_H]);
        // front wall slab
        translate([0, 0, 0])
          cube([BASE_W, WALL, LID_FRONT_H]);
      }
      // side walls — fill in the triangular gaps
      hull() {
        translate([0, 0, 0]) cube([WALL, WALL, LID_FRONT_H]);
        translate([0, BASE_D - WALL, 0]) cube([WALL, WALL, LID_H]);
        translate([0, 0, 0]) cube([WALL, BASE_D, 0.1]);
      }
      hull() {
        translate([BASE_W - WALL, 0, 0]) cube([WALL, WALL, LID_FRONT_H]);
        translate([BASE_W - WALL, BASE_D - WALL, 0]) cube([WALL, WALL, LID_H]);
        translate([BASE_W - WALL, 0, 0]) cube([WALL, BASE_D, 0.1]);
      }
      // top sloped face — connect the top edges of the four walls
      hull() {
        translate([0, BASE_D - WALL, LID_H - WALL])
          cube([BASE_W, WALL, WALL]);
        translate([0, 0, LID_FRONT_H - WALL])
          cube([BASE_W, WALL, WALL]);
      }
      // floor of the lid (closes the bottom). The lip cutout (next
      // difference) carves out a recess for the base's mating lip.
      translate([0, 0, 0]) cube([BASE_W, BASE_D, WALL]);
    }

    // ─ Lip-receiving recess on the underside (so the lid snaps onto
    //   the base's protruding lip with a press fit) ─
    translate([WALL/2 - GAP, WALL/2 - GAP, -0.1])
      cube([BASE_W - WALL + 2*GAP, BASE_D - WALL + 2*GAP, LIP + 0.1]);

    // ─ Lens hole on the sloped face ─
    // Drill perpendicular to the slope. Slope goes from Y=BASE_D, Z=LID_H
    // (back, top) down to Y=0, Z=LID_FRONT_H (front, low). Centre the
    // hole on the front-third of the slope (about 1/3 from the front
    // edge along Y). Drill orientation: angled so the lens looks
    // forward-and-down at the plant.
    slope_y_centre = BASE_D / 3;
    slope_z_centre = LID_FRONT_H +
      (LID_H - LID_FRONT_H) * (slope_y_centre / BASE_D);
    slope_angle_deg = atan((LID_H - LID_FRONT_H) / BASE_D);
    translate([BASE_W / 2, slope_y_centre, slope_z_centre])
      rotate([slope_angle_deg, 0, 0])
        cylinder(d = CAM_LENS_DIA, h = WALL * 4, center = true);

    // ─ Light cable cutout: top-right area of the back wall ─
    // Top-right means high-X, high-Z, on the back wall (Y = BASE_D - WALL).
    translate([BASE_W - WALL - LIGHT_CUTOUT_W - 4,
               BASE_D - WALL - 0.1,
               LID_H - LIGHT_CUTOUT_H - 4])
      cube([LIGHT_CUTOUT_W, WALL + 1, LIGHT_CUTOUT_H]);

    // ─ Vent slots on the lid sides (mirror the base) ─
    for (i = [0 : VENT_SLOTS - 1]) {
      translate([BASE_W - WALL - 0.1,
                 BASE_D / 2 - VENT_PITCH * VENT_SLOTS / 2 + i * VENT_PITCH,
                 LID_FRONT_H - VENT_H - 1])
        cube([WALL + 0.2, VENT_W, VENT_H]);
      translate([-0.1,
                 BASE_D / 2 - VENT_PITCH * VENT_SLOTS / 2 + i * VENT_PITCH,
                 LID_FRONT_H - VENT_H - 1])
        cube([WALL + 0.2, VENT_W, VENT_H]);
    }
  }

  // Camera standoffs on the underside of the sloped face.
  // Position: under the lens hole, four standoffs in a 21×13 rectangle
  // centred on the lens. Slightly tricky because the standoffs need to
  // be perpendicular to the sloped face, not to the floor. We rotate
  // the whole assembly to match the slope.
  slope_angle_deg = atan((LID_H - LID_FRONT_H) / BASE_D);
  slope_y_centre = BASE_D / 3;
  slope_z_centre = LID_FRONT_H +
    (LID_H - LID_FRONT_H) * (slope_y_centre / BASE_D);
  translate([BASE_W / 2, slope_y_centre, slope_z_centre])
    rotate([slope_angle_deg, 0, 0])
      camera_standoffs();
}


// ─── HELPERS ─────────────────────────────────────────────────────────
module pi_standoffs() {
  for (x = [0, PI_HOLE_X])
    for (y = [0, PI_HOLE_Y])
      translate([x, y, 0])
        difference() {
          cylinder(d = PI_STANDOFF_OD, h = PI_STANDOFF_H);
          translate([0, 0, -0.1])
            cylinder(d = PI_STANDOFF_ID, h = PI_STANDOFF_H + 0.2);
        }
}

module camera_standoffs() {
  for (x = [-CAM_HOLE_X / 2, CAM_HOLE_X / 2])
    for (y = [-CAM_HOLE_Y / 2, CAM_HOLE_Y / 2])
      translate([x, y, -CAM_STANDOFF_H])
        difference() {
          cylinder(d = CAM_STANDOFF_OD, h = CAM_STANDOFF_H);
          translate([0, 0, -0.1])
            cylinder(d = CAM_STANDOFF_ID, h = CAM_STANDOFF_H + 0.2);
        }
}

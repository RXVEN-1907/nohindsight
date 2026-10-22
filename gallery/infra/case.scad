// case.scad
// Enclosure for a Raspberry Pi Zero 2 W running the nohindsight kill-switch
// daemon. Parametric: change board_w, board_d, standoff_h for other boards.
//
// Print settings that work:
//   0.2mm layers, 2 to 3 perimeters, 15 to 20% infill, PLA or PETG.
//   No supports if printed lid-up: the lip sits on the bed and the engraved
//   top face prints last. The box prints floor-down, opening up: the
//   standoffs and walls bridge nothing tall, so it also needs no supports.
//   The crimson indicator ring prints as its own small part and glues into
//   the recess, or pause at the recess layer and swap filament for a
//   two-color lid.
//
// Palette: the indicator ring is the only colored part. Crimson #E5484D,
// reserved for the latched state. It never appears as decoration.

$fn = 64;

// ---- parameters -----------------------------------------------------------

// Board
board_w     = 65;     // Pi Zero 2 W long edge, mm
board_d     = 30;     // Pi Zero 2 W short edge, mm
board_t     = 1.0;    // PCB thickness, only sets interior height
standoff_h  = 3.0;    // clears the through-hole GPIO header on the underside
clear_top   = 6.0;    // headroom above the board top for the camera connector

// Case
wall        = 2.4;    // twelve 0.2mm layers, stiff at this footprint
tol         = 0.4;    // slip fit for the lid lip and the board pocket
corner_r    = 4.0;    // outer corner radius
lid_lip_h   = 4.0;    // lip engagement depth

// Mounting holes: Pi holes sit 3.5mm in from each edge, M2.5 self-tappers
hole_pitch_w = 58;    // 65 - 2 * 3.5
hole_pitch_d = 23;    // 30 - 2 * 3.5
pilot_d      = 2.7;
boss_w       = 6.0;   // standoff boss cross section

// USB-C power cutout, on the short edge without the card slot.
// The receptacle is about 8.9mm wide, 3.3mm tall, sitting roughly 1.6mm
// above the board surface. power_offset is the cutout center along the long
// axis, measured from the board centerline toward the GPIO edge. The default
// is set for the Pi Zero 2 W layout; put calipers on your board before
// printing, connector placement varies between revisions.
power_cut_w  = 9.8;
power_cut_h  = 4.2;
power_offset = 8.4;

// GPIO ribbon slot, on the long edge that carries the 40-pin header.
// A 40-way ribbon on 1.27mm pitch is about 51mm across.
ribbon_slot_w = 52.0;
ribbon_slot_h = 2.2;

// Lid features
led_hole_d = 5.0;   // the latched LED on the board shows through this hole
ring_out_d = 9.0;
ring_depth = 0.8;   // recess the ring sits in, flush with the lid surface
text_depth = 0.5;

// ---- derived --------------------------------------------------------------

inner_w   = board_w + 2 * tol;
inner_d   = board_d + 2 * tol;
inner_h   = standoff_h + board_t + clear_top;
outer_w   = inner_w + 2 * wall;
outer_d   = inner_d + 2 * wall;
floor_t   = wall;
outer_h   = floor_t + inner_h;

power_z   = floor_t + standoff_h + board_t + 1.6;  // receptacle centerline
ribbon_z  = floor_t + standoff_h + board_t + 4.5;  // ribbon cable centerline

lid_lip_w = inner_w - 2 * tol;
lid_lip_d = inner_d - 2 * tol;
lid_top_z = outer_h + lid_lip_h + wall;  // assembled top surface

module rounded_box(w, d, h) {
    hull() {
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (w/2 - corner_r), sy * (d/2 - corner_r), 0])
                cylinder(r = corner_r, h = h);
    }
}

// ---- box ------------------------------------------------------------------

module box() {
    difference() {
        rounded_box(outer_w, outer_d, outer_h);
        translate([0, 0, floor_t])
            rounded_box(inner_w, inner_d, inner_h + 1);
        // USB-C power, through the +Y short wall
        translate([power_offset, inner_d/2 + wall/2, power_z])
            cube([power_cut_w, wall + 2, power_cut_h], center = true);
        // GPIO ribbon, through the -Y long wall
        translate([0, -inner_d/2 - wall/2, ribbon_z])
            cube([ribbon_slot_w, wall + 2, ribbon_slot_h], center = true);
        // Ventilation: one row of slots in each long wall. Vertical slots
        // bridge layer by layer, no supports needed at this width.
        for (i = [-2:2])
            for (sy = [-1, 1])
                translate([i * 12, sy * (outer_d/2), 9.0])
                    cube([4.0, wall + 2, 2.5], center = true);
    }
    // Standoff bosses with pilot holes at the Pi's 58 x 23 pattern.
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * hole_pitch_w/2, sy * hole_pitch_d/2, floor_t]) {
            difference() {
                cube([boss_w, boss_w, standoff_h], center = true);
                cylinder(d = pilot_d, h = standoff_h + 1, center = true);
            }
        }
}

// ---- lid ------------------------------------------------------------------

// Lid modeled in assembled position: lip down into the cavity, engraved
// outer face up. "Printed lid-up" means this same orientation on the bed,
// so the lip starts on the bed and nothing bridges.
module lid() {
    translate([0, 0, outer_h]) {
        difference() {
            union() {
                // lip, a slip fit into the cavity
                translate([0, 0, 0])
                    rounded_box(lid_lip_w, lid_lip_d, lid_lip_h);
                // top plate
                translate([0, 0, lid_lip_h])
                    rounded_box(outer_w, outer_d, wall);
            }
            // engraved name on the outer face
            translate([0, -6, lid_lip_h + wall - text_depth])
                linear_extrude(height = text_depth + 1)
                    text("NOHINDSIGHT", size = 5, halign = "center",
                         valign = "center");
            // hole for the latched LED
            translate([0, 6, 0])
                cylinder(d = led_hole_d, h = lid_lip_h + wall + 1);
            // recess for the crimson ring inlay
            translate([0, 6, lid_lip_h + wall - ring_depth])
                cylinder(d = ring_out_d, h = ring_depth + 1);
            // lid vents, clear of the text and the LED
            for (i = [-1:1])
                translate([i * 16, outer_d/2 - 4, lid_lip_h + wall/2])
                    cube([8.0, wall + 2, 1.6], center = true);
        }
    }
}

// ---- crimson ring inlay ---------------------------------------------------

// Prints separately, glue into the recess. #E5484D in RGB fractions.
module ring() {
    color([0.898, 0.282, 0.302])
        translate([0, 6, lid_top_z - ring_depth])
            difference() {
                cylinder(d = ring_out_d, h = ring_depth);
                translate([0, 0, -0.1])
                    cylinder(d = led_hole_d + 0.2, h = ring_depth + 0.2);
            }
}

box();
lid();
ring();

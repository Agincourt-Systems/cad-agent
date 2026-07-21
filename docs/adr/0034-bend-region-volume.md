# ADR 0034: Bend-Region Volume via a Swept Constant-Thickness Ribbon (D-005)

## Status

Accepted for implementation.

## Context

Deficiency **D-005** (`docs/specs/arm-deficiencies.md`, MINOR) observes that the
folded solid's volume is ~4.9% low because the rectilinear envelope omits the
bend-region material. For the documented case (40/60 mm flanges, width 30, t=2.29,
R=2.29, K=0.44, 90 deg) the conserved blank is `developed_length * t * width =
7225.86 mm^3`, but the two-box model reports `6870.0 mm^3` — a 355.86 mm^3 (4.92%)
deficit equal to `BA * t * width`, the bend-region material the sharp corner drops.
The flat pattern and developed length are unaffected, but the deficit biases every
mass / CoM / inertia / stability result that includes a bent part.

The two-box (and, for chains, the rigid-fold walk from ADR 0032) construction
models the bend as a sharp corner. Modelling it as a **cylindrical (annular)
sector** restores the missing material.

## Decision

Replace the folded-solid construction with a single unified model that sweeps a
**constant-thickness ribbon** along the part's neutral centreline in the XZ
cross-section plane, then extrudes it by the width along `y`:

- The centreline is a walk of **straight segments** (one per flange, of length equal
  to the flange length) joined by **circular arcs** (one per bend). Each bend arc has
  radius `rho_j = inside_radius_j + k_factor_j * thickness` (the neutral fibre) and
  sweeps the bend angle, turning left for `up` and right for `down`. The arc's
  centreline length is therefore exactly the bend allowance `BA_j = radians(angle) *
  rho_j`.
- The 2D ribbon is produced by `build123d`'s `trace(line_width=thickness)` over that
  centreline, giving a constant-`thickness` band; `extrude(..., width)` gives the
  solid. Every bend region is thus a true annular sector (inner radius
  `rho_j - t/2`, outer `rho_j + t/2`).

By **Pappus's theorem** the swept volume is exactly the ribbon's cross-sectional
area times the width, and the cross-sectional area is
`thickness * (sum(flange lengths) + sum(BA_j)) = thickness * developed_length`. So
the folded volume equals the conserved blank volume `developed_length * thickness *
width` **exactly** (to numerical tolerance), for a single bend and for a chain
alike — one construction now serves both, and the ADR 0032 rigid-fold walk and the
ADR 0016 two-box closed form are retired.

**Envelope change (justified).** Conserving volume requires the folded straight
runs to keep their flat lengths (sheet metal does not stretch the flat portions),
so the arc offsets each downstream flange by the bend radius rather than collapsing
it into a sharp corner. The single-bend bounding box therefore changes from the old
`(flange_a + t, width, flange_b)` to `(flange_a + rho + t/2, width, flange_b + rho +
t/2)`: the extents grow by the bend geometry at the corner, exactly the material the
old model omitted. The straight flange lengths (the meaningful "flange dimensions")
remain `flange_a` and `flange_b`; only the corner geometry changes. The three
existing tests that pinned the old rectilinear bounding box are updated to the new
envelope with this justification; no unrelated assertion is weakened.

## Tolerance

The construction conserves volume by Pappus's theorem, so the folded volume equals
the blank volume up to floating-point / kernel rounding. The tests assert equality
within `rel=1e-4` (0.01%) — far tighter than the deficiency's 0.5% target — which
leaves ample margin for OCCT boolean/meshing round-off while still catching any
real material omission (the old model was off by 4.9%, ~500x this bound).

## Success Criteria

Written so the new/updated tests fail before implementation and pass after:

- `test_folded_volume_matches_blank`: for the deficiency's 40/60 part the folded
  volume equals `developed_length * t * width` (7225.86 mm^3) within `rel=1e-4`.
  Before implementation the two-box model gives 6870 mm^3 (fails); after, it matches.
- `test_chain_folded_volume_matches_blank`: the U-channel chain's folded volume
  equals its blank volume within `rel=1e-4`, and it is a single connected solid.
- `test_bend_line_layer`, `test_down_bend_envelope_matches_up`,
  `test_bend_delegates_to_chain`: updated to the new bend-corner envelope
  `(flange_a + rho + t/2, width, flange_b + rho + t/2)`, computed from `rho`, not
  hardcoded.
- Existing flat-pattern / bend-table / DFM tests are unchanged (the flat pattern,
  developed length, bend lines, and `kind="bend"` features do not depend on the
  folded-solid construction).

## Consequences

- Mass / CoM / inertia / stability for bent parts are now correct: the folded solid
  conserves the blank's volume exactly, removing the 4.9% bias.
- One construction (`_folded_profile`) handles single bends and chains, so the
  bend-region model can no longer drift between the two paths.
- The folded bounding box of a bent part grows at the corner by the bend geometry;
  this is the correct rounded-bend envelope and is documented here. Downstream
  consumers that assumed the old sharp `(flange_a + t, ..., flange_b)` envelope must
  use the new value (only the internal sheet-metal tests did).

## After Action Report

The red state failed as predicted: the two-box model reported 6870 mm^3 for the
40/60 part (against the 7225.86 mm^3 blank) and the U-channel 6791 mm^3 (against
7581.71 mm^3), and the three envelope tests still pinned the old sharp bounding
box.

`build123d`'s `trace(line_width=t)` proved exact: the ribbon cross-sectional area
matched `thickness * developed_length` to machine precision, and the extruded
volume matched the blank to ~1e-12 relative for every case (single up, single down,
non-90-degree, U-channel, and the deficiency's 40/60). Pappus's theorem holds in
practice, not just on paper, so the `rel=1e-4` test bound is met with ~8 orders of
magnitude of margin. Each case is a single connected solid because the whole
cross-section is one traced face extruded in one direction.

One unified construction (`_folded_profile`) now replaces both the ADR 0016 two-box
closed form and the ADR 0032 rigid-fold walk, so single bends and chains share the
same volume-conserving model and cannot drift. `bend_chain` collects each bend's
inside radius and k-factor to feed the neutral arc radius `rho = R + K*t`.

The single-bend bounding box changed from `(flange_a + t, w, flange_b)` to
`(flange_a + rho + t/2, w, flange_b + rho + t/2)`; three internal sheet-metal tests
were updated to the new envelope (computed from `rho`, not hardcoded) with the
justification above. No other test pinned the folded envelope — the BOM test uses
the flat-pattern area, which is unchanged — and the full suite passed with 175
tests (173 prior + 2 new), no regressions.

# ADR 0032: Multi-Bend Sheet-Metal Chains (D-003)

## Status

Accepted for implementation.

## Context

Deficiency **D-003** (`docs/specs/arm-deficiencies.md`, MAJOR) observes that the
sheet-metal API tops out at a single bend. `bend(flange_a, flange_b, ...)` accepts
exactly two flanges and one bend, and `SheetMetalPart` always carries one bend
line and one bend-table row. The arm this harness targets needs **U-channels** and
**clevis brackets** — parts with *two* 90° bends that a shop cuts as **one** flat
blank and folds twice (spec §1.3: every pitch joint is a clevis).

Today the only workaround is to compose two `bend()` calls, which yields two
disjoint blanks with the shared web double-counted (the deficiency's example:
75.18 mm + 75.18 mm instead of the correct single blank 110.36 mm) and two
disconnected folded solids that the interference / center-of-mass machinery cannot
treat as one part.

The flat-pattern arithmetic generalises directly: a chain of flanges joined by
bends has a developed length equal to the sum of every flange length plus the sum
of every bend allowance, and one bend line per bend at its own developed position.

## Decision

Add `bend_chain(flanges, bends, *, thickness, width)` to `src/cadx/sheetmetal.py`,
and re-express `bend(...)` as a thin wrapper that delegates to it, so the existing
single-bend API and all its published behaviour are unchanged.

- `flanges` is an ordered list of `N` outside flange lengths (mm). `bends` is a
  list of `N-1` bend descriptions, each a dict `{angle_deg, inside_radius,
  k_factor, direction}` (`direction` in `{"up", "down"}`). The two lists are
  validated to satisfy `len(bends) == len(flanges) - 1`, along with the same
  positivity / membership checks `bend()` already enforced.
- **Developed length.** For each bend `j`, `BA_j = (pi/180) * angle_j *
  (inside_radius_j + k_factor_j * thickness)`. The developed (flat) length is
  `sum(flanges) + sum(BA_j)` — one blank, never the per-pair double-count.
- **One flat blank.** `flat_profile` is a single `developed_length x width`
  rectangle with `x` running `0..developed_length`, exactly as the single-bend
  case, so a chain still exports as one DXF cut outline.
- **N bend lines.** Bend `j` sits at developed position
  `x_j = sum(flanges[0..j]) + sum(BA[0..j-1]) + BA_j/2` — the centreline of its
  bend region measured along the developed axis. For a single bend this reduces to
  `flange_a + BA/2` (unchanged). Each bend line spans the full width and is drawn
  on the DXF `bend` layer; each contributes one `{line, angle, direction,
  inside_radius}` row to `bends` (so `bends.json` and the ADR 0016 `bend` check
  see every bend).
- **Connected folded solid.** For `N == 1` the folded solid is built by the
  existing closed-form construction (`_folded_single`) verbatim, so the single-bend
  envelope `(flange_a + thickness, width, flange_b)` and every existing test are
  preserved bit-for-bit. For `N >= 2` a rigid-fold *walk* (`_folded_chain`) places
  each flange as a `Box` whose length points along a running heading in the XZ
  cross-section plane; at each bend the heading turns by the bend angle (`+` for
  `up`, `-` for `down`). The material thickness is centred on each flange's
  mid-plane (`z in [-t/2, t/2]` before folding), so consecutive flanges overlap in
  a `t x t` corner region and the boolean union is a **single connected solid** —
  the property the interference / CoM machinery requires. All bends are folded
  about axes parallel to the width (`y`) axis, matching the U-channel / clevis
  geometry the deficiency calls out.

The chain folded solid deliberately uses **sharp corners** (no bend-region
material): its overall envelope is correct to within the material thickness, and
the per-flange outer dimensions are exact. Bend-region volume fidelity is the
subject of ADR 0034; the flat pattern's developed length already carries the
bend-allowance correction that governs fabrication.

## Success Criteria

Written so the new tests in `tests/test_sheet_metal_bend.py` fail before and pass
after implementation:

- `test_uchannel_single_blank`: `bend_chain([30, 40, 30], [two 90 deg bends], ...)`
  reports `developed_length == 30 + 40 + 30 + 2*BA` (derived from the BA formula,
  ~110.36 mm for the deficiency's parameters), **not** the 75.18 mm double-count;
  the `flat_profile` x-length equals it; there are exactly two bend lines and two
  `bends` rows at the computed developed positions; and the folded solid is a
  **single** connected solid (`len(folded.solids()) == 1`).
- `test_bend_delegates_to_chain`: the existing single-bend `bend(...)` result is
  unchanged — same developed length, same one bend line, and the same
  `(flange_a + thickness, width, flange_b)` folded envelope.
- `test_bend_chain_validates_lengths`: a `flanges`/`bends` length mismatch, an
  empty `flanges`, or a bad `direction` raises a clear `ValueError`.
- Existing ADR 0013 / 0016 / 0018 sheet-metal tests continue to pass unchanged.

## Consequences

- A single `bend_chain(...)` description yields the U-channels and clevis brackets
  the arm needs as one blank + one DXF + one connected folded solid, unblocking the
  clevis pitch joints.
- `bend()` becomes a two-flange convenience over the general chain, so there is one
  code path for the flat-pattern arithmetic and the bend table.
- The chain folded solid is a sharp-cornered rigid fold; its envelope is correct to
  within one material thickness and its volume omits bend-region material (ADR 0034
  addresses volume; this ADR keeps the single-bend closed-form envelope exact).
- Bend DFM rules still need the spatial `kind="bend"` feature that ADR 0033 adds;
  this ADR only generalises the geometry and the bend table.

## After Action Report

The red state failed as predicted: `bend_chain` did not exist, so the three new
tests raised `ImportError`.

The U-channel reproduces the deficiency's figures exactly: `bend_chain([30, 40,
30], ...)` with `t = R = 2.29`, `k = 0.44` reports `developed_length == 110.36 mm`
(one blank, web counted once), against the 75.18 mm each naive two-flange `bend()`
call would report. Both bend lines land at their computed developed positions, and
the folded solid is a single connected solid (`len(folded.solids()) == 1`).

Two implementation realities were confirmed empirically:

1. **build123d rotation sign.** A positive rotation about `+y` sends `+x` toward
   `-z`, so a heading angle `phi` (CCW from `+x` toward `+z`) is realised by
   rotating the axis-aligned flange box by `-degrees(phi)`. Verified with a probe
   box before wiring it into `_folded_chain`.
2. **Centred thickness is required for connectivity.** With the thickness centred
   on each flange mid-plane, consecutive flanges overlap in a `t x t` corner and
   the union collapses to one solid. The single-bend closed-form construction uses
   a different (one-sided) convention and does **not** reproduce this envelope, so
   `bend_chain` keeps `_folded_single` verbatim for `N == 1` (delegation is
   bit-for-bit identical) and uses the rigid-fold walk only for `N >= 2`.

`bend()` is now a thin wrapper over `bend_chain`; its non-negative-flange
validation is retained at the wrapper so its error messages are unchanged. The
full suite passed with 169 tests (166 prior + 3 new), no regressions.

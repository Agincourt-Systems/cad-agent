# ADR 0040: Flat-Pattern Holes and Cutouts on Sheet-Metal Parts (D-019)

## Status

Accepted for implementation.

## Context

Deficiency **D-019** (`docs/specs/arm-deficiencies.md`, MAJOR) observes that the
flat-pattern DXF produced by `bend_chain` / `publish_sheet_metal` is a **bare
outline**. A bent bracket almost always carries fastener holes and lightening
cutouts, but today those holes exist only as hand-published DFM `spatial.json`
features — they are **absent from the exported DXF**. A shop that cuts the file
receives an undrillable blank: it must re-derive every hole by hand from a
separate document, which defeats the purpose of a machine-cuttable flat pattern
and is exactly the wrong-but-plausible class this harness exists to catch (the
DXF looks complete but is not).

Three artifacts must agree on the same holes for a part to be fabricable:

1. the **flat-pattern DXF** the laser/waterjet cuts (holes as cut-layer
   geometry);
2. the **`spatial.json` features** the DFM rules inspect (so `hole_to_bend`,
   `hole_to_edge`, `min_hole_diameter` gate the holes); and
3. the **folded 3-D solid** the mass / volume / render machinery reasons over
   (so a hole removes material everywhere, not only on paper).

Before this ADR none of the three carried holes for a sheet-metal part.

## Decision

Let `bend_chain(...)` (and its `bend(...)` wrapper) accept a `holes=[...]`
argument: a list of hole / cutout primitives positioned in **flange-local**
frames, unfolded into the developed blank the same way the bend lines are.

### Flange-local frame (the agent-facing ergonomics)

Each hole is a plain dict — an agent authors these without touching build123d:

```python
{"flange": j, "u": <along-flange mm>, "v": <across-width mm>, "diameter": d}   # round hole
{"flange": j, "u": ..., "v": ..., "length": L, "width": W}                     # rectangular cutout
```

* `flange` is the 0-based index into `flanges`.
* `u` runs **from the flange's leading edge** (the edge at the smaller developed
  x, i.e. `x=0` for flange 0 and the bend boundary for later flanges) toward the
  trailing edge, `0 <= u <= flanges[flange]`.
* `v` runs **across the width, from the width centreline** (`v=0` is the middle
  of the strip; the blank spans `v in [-width/2, +width/2]`), matching the
  width-centred flat blank the module already emits.
* A `diameter` key marks a round hole; a `length` + `width` pair marks a
  rectangular cutout (`length` along `u`, `width` along `v`). Exactly one of the
  two forms must be present or a `ValueError` is raised.

This frame is chosen so an agent reasons about each hole **on the flange it can
see**, never about the developed blank's cumulative bend-allowance arithmetic —
the unfold is the harness's job.

### Unfold into developed-blank coordinates

Flange `j` occupies developed span `[x_start(j), x_start(j) + flanges[j]]` where
`x_start(j) = sum(flanges[0..j-1]) + sum(BA[0..j-1])` — the same accumulation of
flange lengths plus bend allowances that positions the bend lines. Because the
neutral-fibre arc length of bend `j` equals its bend allowance `BA_j`, this
developed x-coordinate is *also* the arc-length parameter of the folded
centreline, so the flat and folded placements are guaranteed consistent. A hole
unfolds to developed centre `[x_start(j) + u, v, 0]`.

### 1 — DXF cut layer

The developed hole is subtracted from the flat-pattern sketch, so `flat_profile`
becomes the outer rectangle with inner wires. `ExportDXF` emits a round hole as a
`CIRCLE` and a rectangular cutout as connected `LINE`s, **all on the `cut`
layer** alongside the outline (the bend lines stay on the `bend` layer,
unchanged). A shop cutting the DXF gets the holes for free.

### 2 — `spatial.json` features (DFM binding)

`publish_sheet_metal` emits one feature per hole in the **flat-pattern frame**,
mirroring how ADR 0033 emits `kind="bend"` features:

* a round hole is published as `kind="cylindrical_hole"` with `diameter`,
  `center` (developed `[x, y, 0]`), `axis=[0,0,1]`, `through=True`, and
  `source_object="obj.<label>"`;
* a rectangular cutout is published as `kind="cutout"` (informational; it carries
  `length`/`width`/`center`).

**Why `cylindrical_hole` and not the literal string `"hole"`:** the ADR 0018 DFM
rules (`min_hole_diameter`, `hole_to_edge`, `hole_to_bend`) key on the exact kind
`"cylindrical_hole"` (see `_CYLINDRICAL` in `dfm.py`). Emitting that kind is what
makes those rules **bind automatically** on API-placed holes — the functional
requirement D-019 states. Since the DFM rule module is owned elsewhere and must
not be forked, the feature adopts the string the rules already recognise. Both
the bend line (ADR 0033) and the hole are therefore in one shared flat-pattern
frame, so `hole_to_bend` — a pure 2-D flat-pattern measurement — is exact.
(`hole_to_edge` measures against the owning object's bounding box, which for a
folded part is the folded envelope, not the flat blank; this is the same
documented caveat ADR 0033 records for a folded part's thickness, and
`hole_to_bend` is the reliable bend-proximity gate.)

### 3 — Subtraction from the folded 3-D solid

Each hole is also cut out of the folded solid at its flange's 3-D pose.
`_folded_profile` records, per flange, the centreline start point and heading
`phi` of its straight run. A hole on flange `j` at `(u, v)` sits on the flange
mid-surface at centreline point `start + u*(cos phi, 0, sin phi)`, offset across
the width to `y = v - width/2` (the folded strip spans `y in [-width, 0]`, so the
centreline `v=0` maps to `y=-width/2`). The flange surface normal is
`(-sin phi, 0, cos phi)`. A round hole subtracts a `Cylinder` (diameter `d`,
axis along the normal, length `3*thickness` so it pierces cleanly); a cutout
subtracts an oriented `Box`. Because the hole lies entirely within the flat
flange (see the crossing guard below), the cylinder pierces a flat `thickness`
plate and removes **exactly** `pi*(d/2)^2 * thickness` (a cutout removes
`length*width*thickness`), which is what makes the volume identity below exact.

### Holes may not cross a bend line (hard error)

A hole whose developed extent overlaps any bend-allowance region would, when
folded, wrap around the radius — its removed volume would no longer be a clean
prism and the flat/folded geometry would disagree. Rather than silently produce
garbage, `bend_chain` raises a clear `ValueError` naming the flange and bend when
a hole's developed extent overlaps a bend region, and likewise when a hole runs
off the blank's outline (past a free edge or the width). Supporting holes that
straddle a bend is deferred (it needs a wrapped-cutout model); the error
documents the limit at the point of use.

### Volume conservation (extends ADR 0034)

ADR 0034 established `folded volume == developed_length * thickness * width`
exactly. With holes the identity becomes

```
folded_volume == developed_length * thickness * width - sum(hole_material)
```

where `hole_material` is `pi*(d/2)^2 * thickness` per round hole and
`length*width*thickness` per cutout — exact because every hole is a clean prism
through the flat flange. This is tested directly.

## Success Criteria

Written so the new tests in `tests/test_sheet_metal_bend.py` fail before and pass
after implementation:

- `test_flat_pattern_hole_in_dxf`: a bracket with two round holes (one per
  flange) exports a DXF whose `cut` layer carries exactly two `CIRCLE`s at the
  **developed** centres (parsed back with `ezdxf`), radii correct, plus the
  outline; the `bend` layer still carries its one bend line.
- `test_hole_subtracted_from_folded_volume`: the folded solid's volume equals
  `developed_length * t * width - sum(pi r^2 t)` within `rel=1e-4`, and stays a
  single connected solid.
- `test_hole_published_as_spatial_feature`: `spatial.json` carries one
  `kind="cylindrical_hole"` feature per hole in the flat-pattern frame, with the
  right developed centre and `source_object`.
- `test_api_hole_binds_hole_to_bend`: a hole placed near (not across) a bend line
  via the `holes` API fails `hole_to_bend`, naming the hole and the bend —
  proving the feature binds the DFM rule with no hand-publishing.
- `test_api_hole_binds_min_hole_diameter`: an undersized API-placed hole fails
  `min_hole_diameter`.
- `test_hole_crossing_bend_raises` / `test_hole_off_blank_raises`: a hole whose
  extent crosses a bend region, or runs off the blank, raises a clear
  `ValueError`.
- Every existing sheet-metal / DXF test continues to pass unchanged (a part with
  no holes is byte-identical to before: the flat profile is still the bare
  `Pos*Rectangle`, and the folded solid is untouched).

## Consequences

- A folded sheet part now exports a **fabricable** flat pattern: outline + holes
  on the cut layer, bend lines on the bend layer, one DXF.
- Holes, DXF, and folded solid cannot drift: all three derive from one
  flange-local specification through one unfold.
- The bend DFM rules gate API-placed holes with no hand-published redundant
  features.
- Holes that straddle a bend are rejected loudly rather than mis-modelled;
  wrapped cutouts are future work.

## After Action Report

The red state failed as predicted: `bend()`/`SheetMetalPart` had no `holes`
parameter/field, so all nine new tests raised `AttributeError`/`TypeError`
(missing `holes`) or asserted on holes that never reached the DXF, the folded
solid, or `spatial.json`.

Two build123d realities were confirmed empirically before wiring:

1. **Cutter placement.** `Plane(origin, x_dir, z_dir) * Cylinder(r, 3t)` locates
   the cutter on the flange face; subtracting it removes exactly `pi r^2 t` from a
   flat `t`-plate (measured 37.699 mm^3 for r=2, t=3), so the volume identity is
   exact and the result stays one connected solid.
2. **Flat subtraction.** Algebra-mode chained `-` (`s - a - b`) silently dropped a
   hole, but an accumulating `-=` loop subtracts every hole correctly (verified 2
   inner wires, exact area). The implementation uses the `-=` loop.

The folded strip spans `y in [-width, 0]` (Plane.XZ extrude normal is `-y`), so
the width centreline maps to `y = -width/2`; hole placement uses that offset.

The literal feature kind is `"cylindrical_hole"`, not `"hole"`: the ADR 0018 DFM
rules key on that exact string (`_CYLINDRICAL` in `dfm.py`), and that module is
owned by a concurrent track and must not be forked, so adopting the recognised
string is what makes `hole_to_bend` / `min_hole_diameter` bind automatically on
API-placed holes — the functional requirement of D-019. `hole_to_bend` is exact
(pure flat-pattern 2-D); `hole_to_edge` still measures against the folded
envelope, the same documented caveat as ADR 0033's thickness note.

All 27 tests in `tests/test_sheet_metal_bend.py` (18 prior + 9 new) pass, and the
DXF-export and manufacturability suites are unchanged (a no-hole part is
byte-identical: bare `Pos*Rectangle` flat profile, untouched folded solid).

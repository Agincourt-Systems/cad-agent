# ADR 0044: Frame-consistent sheet DFM + bend-arc slot suppression (D-023)

## Status

Accepted for implementation. Stacked on ADR 0043.

## Context

Deficiency **D-023** (MINOR) has two halves that together stop the sheet-metal DFM
rules from running as one coherent check on a **bent** part:

1. **Frame mismatch.** `hole_to_bend` measures in the **flat-pattern** frame (the
   bend `line` coordinates, ADR 0033), while `hole_to_edge` and `min_web` measure
   against the published object's bounding box — which for a `publish_sheet_metal`
   part is the **folded 3-D solid**. A hole authored in flat-pattern coordinates
   (so `hole_to_bend` can see it) lands far outside the folded bbox, so
   `hole_to_edge` reports a spurious edge violation. The two rule sets therefore
   cannot be combined in one manufacturability check on a bent part.

2. **Bend arcs auto-detect as spurious slots.** The folded solid's bend region is a
   swept annular sector (ADR 0034), so each bend contributes two **partial
   cylindrical** faces — an inner arc (radius `rho - t/2`) and an outer arc
   (`rho + t/2`), with `rho = inside_radius + k*t`. On a multi-bend part the
   inspector's obround-slot detector (`_slot_features`) pairs equal-radius arcs
   **across bends** (bend 0's outer arc with bend 1's outer arc, etc.) into
   `kind="slot"` features that do not exist. These phantom slots pollute every
   feature-based rule (`min_slot_width`, `hole_to_edge`, `min_web`). Empirically a
   1-bend part yields 0 phantom slots (its two arcs differ in radius, so they do
   not pair), a 2-bend part yields 2 (one per radius cluster).

## Decision

### Half (2): suppress bend-arc slots — the shipped, complete fix

Discriminate bend arcs from real slots by a **geometric signature that a real
obround slot can never have**, gated on the part being sheet-metal:

- **Sheet-metal gate.** Suppression runs only for a part that has published
  `kind="bend"` features (it is a `publish_sheet_metal` part). A plain flat part
  has no bend features, so its real slots are never touched — no over-suppression.
- **Concentric-arc signature.** A partial cylindrical face is a bend arc if
  another partial cylinder on the same part is **concentric** with it — the same
  axis-of-rotation *line* (same direction and same line position) — but with a
  **different radius**. This is exactly the inner/outer pair of one bent ribbon.
  A real obround slot's two ends have **equal** radii on **parallel but displaced**
  axes, so they are never concentric-with-different-radius. (A counterbore is two
  concentric cylinders of different radii, but those are *full* 360° cylinders,
  detected as `cylindrical_hole`/`boss`, never partial cylinders — so they are not
  candidates here.)
- **Bend-radius corroboration.** The concentric pair's radii must be consistent
  with a published bend: with the measured thickness `t = |r_outer - r_inner|`, the
  inner radius must lie within `0.5*t` of some bend's `inside_radius` (because
  `r_inner = inside_radius + (k - 0.5)*t` for `k in [0, 1]`). This ties suppression
  to the bend table (the task's discriminator) and derives thickness from the
  geometry itself, so no external thickness is needed at inspect time.

Implementation: `_detected_topology_features` records each partial cylinder's
axis-line position; `_auto_detect_features` passes the per-label bend
`inside_radius` list; a new `_bend_arc_indices` marks the arcs; `_slot_features`
receives only the survivors. Flat-part detection is byte-identical (empty bend
list → no marking).

### Half (1): frame-consistent `hole_to_edge` — a documented subset

Make `hole_to_edge` evaluate in the **flat-pattern frame** when the check declares
it, so it is coherent with `hole_to_bend`. A check on a sheet-metal part sets
`frame: flat` and supplies the developed blank extent (`blank_length`,
`blank_width`); `hole_to_edge` then measures each hole's flat-frame center against
the flat blank rectangle `x in [0, blank_length]`, `y in [-blank_width/2,
+blank_width/2]` (thickness axis z), instead of the folded bbox. `hole_to_bend`
already works in this frame, so `hole_to_bend` + `hole_to_edge` (+ `min_flange`)
run together on a bent part with no false positive from the fold.

**Why a subset, and why explicit `blank_length`/`blank_width`.** The clean
"auto-derive the blank outline from the flat pattern" design is blocked: the flat
pattern's extent is not serialized into `spatial.json` (the object bbox is the
folded solid), and `publish_sheet_metal` / `sheetmetal.py` — the only places that
hold `part.developed_length` and the blank width — are **owned by another agent in
this cycle and must not be modified here**. So the blank extent is supplied on the
check, exactly as ADR 0033 already requires an explicit `thickness` for a folded
part and as ADR 0043's `min_flange` takes `blank_length`. This is the honest,
non-half-working subset: full auto-derivation is recorded as future work below.

**Non-sheet parts are unchanged.** With no `frame` key (the default), `hole_to_edge`
uses the object bbox exactly as before — byte-identical behaviour.

## Success Criteria

Tests fail before implementation, pass after:

- `test_folded_bend_arcs_are_not_slots`: a folded 1-bend part and a folded 2-bend
  U-channel each produce **zero** `slot` features in `spatial.json`.
- `test_real_slot_on_flat_part_still_detected`: a real obround slot cut in a flat
  plate is still detected as exactly one `slot` (no over-suppression).
- `test_hole_to_bend_and_hole_to_edge_coherent_in_flat_frame`: a bent part with a
  hole authored in flat coordinates passes **both** `hole_to_bend` and
  `hole_to_edge` in one `frame: flat` check, whereas the same check without
  `frame: flat` false-positives on `hole_to_edge` (folded-bbox incoherence).
- `test_flat_frame_hole_to_edge_catches_real_violation`: under `frame: flat`, a
  hole genuinely too close to the flat blank edge still fails `hole_to_edge`.

## Consequences

- The sheet-metal DFM rule set finally runs as one coherent check on a bent part.
- Phantom bend-arc slots no longer pollute feature counts or feature-based rules.
- A frame-consistent `hole_to_edge` on a sheet part requires the blank extent on
  the check; this is explicit and documented, mirroring the thickness convention.

## After Action Report

Implemented as designed; both halves green.

**Half (2).** A probe on the folded solids confirmed the geometry the design
assumed: a 1-bend part exposes two partial cylinders of *different* radii
(`2.1526` and `4.4426` mm for `R=2.29, t=2.29, k=0.44`, i.e. `rho ± t/2`), which
never pair, so it already produced zero phantom slots; a 2-bend part's equal-radius
arcs pair *across bends* into two phantom slots. Critically, the two faces of one
bend share an identical axis-of-rotation position (`(40, 0, 4.443)`), so the
concentric-line test fires cleanly, while a real slot's ends sit on displaced axes.
The suppression drops all four arcs of the 2-bend part (each has a concentric
different-radius partner at its own bend), yielding zero slots, and leaves the flat
plate's real slot untouched (no bend features → empty gate). `test_richer_feature_detection`
(the real-slot regression) stays green.

**Half (1).** The `frame: flat` branch of `hole_to_edge` made the flat-frame hole
pass both `hole_to_bend` and `hole_to_edge` in one check, and the red baseline
proved the incoherence: the same check without `frame: flat` false-positives on
`hole_to_edge` because flat x=35 lies outside the folded bbox. A hole crowding the
flat blank edge (x=1.5) still fails, so the frame-aware rule is coherent, not
always-pass. Non-`flat` checks are untouched (the existing ADR 0018 `hole_to_edge`
tests stay green).

**Constraint honoured.** Both fixes live entirely in `dfm.py` and `inspector.py`
(the `_slot_features`/`_detected_topology_features` region and the bend-radius map
in `_auto_detect_features`); the aggregate-inertia section of `inspector.py` and
both `registry.py`/`sheetmetal.py` were not touched. Because the blank extent could
not be auto-derived without serialising it from `publish_sheet_metal` (a forbidden
file this cycle), Half (1) is delivered as the documented `frame: flat` +
`blank_length`/`blank_width` subset, with full auto-derivation recorded below.

Full repository suite (both ADRs stacked): [recorded at end of run] — see the
final report; the ADR 0044 module contributes 4 tests and ADR 0043 contributes 5.

### Future work

- Auto-derive the flat blank outline (`developed_length`, blank width) so
  `frame: flat` needs no explicit `blank_length`/`blank_width`. This requires
  `publish_sheet_metal` to serialize the flat-pattern extent (and/or the flat
  bbox) into the object record — out of scope here because `registry.py` and
  `sheetmetal.py` are owned by a concurrent change this cycle.
- Project auto-detected folded-frame holes back into the flat frame so a hole
  modelled as real 3-D geometry (not published in flat coordinates) can also be
  edge-checked in the flat frame.

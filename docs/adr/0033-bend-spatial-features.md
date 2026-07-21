# ADR 0033: Bends as Spatial Features so Bend DFM Rules Fire (D-004)

## Status

Accepted for implementation.

## Context

Deficiency **D-004** (`docs/specs/arm-deficiencies.md`, MAJOR) observes that the
two bend-safety DFM rules — `min_bend_radius` and `hole_to_bend` (ADR 0018) — can
**never fire on the real bend flow**. Those rules inspect `spatial.json` features
with `kind == "bend"`, but `bend()` / `publish_sheet_metal` record bends only in
`bends.json` (ADR 0016). Nothing on the only path that produces bends publishes a
`kind == "bend"` spatial feature, so the rules are inert: a part folded with a
sub-minimum inside radius (the deficiency's case: 0.5 mm ~= 0.22 t on 2.29 mm
5052, a radius SendCutSend would reject) **silently passes** manufacturability.
This is the wrong-but-plausible failure class the harness exists to catch.

The DFM rules already read exactly two things from a bend feature (see
`src/cadx/dfm.py`): `_rule_min_bend_radius` reads `inside_radius` (falling back to
`radius`) and resolves the owning object for thickness; `_rule_hole_to_bend` reads
a 2D `line` (`[[x0, y0], [x1, y1]]`) and the shared `source_object`, then measures
the in-plane hole-edge-to-line distance. The fix is to publish those features from
the sheet-metal flow.

## Decision

`publish_sheet_metal(label, part, ...)` (in `src/cadx/registry.py`) now emits one
`kind == "bend"` spatial feature per bend, in addition to storing the folded solid
and the internal `flat` key. For bend `i` of `part.bends` it publishes:

```python
publish_feature(
    f"{label}_bend_{i}",
    "bend",
    inside_radius=row["inside_radius"],
    line=row["line"],                       # flat-pattern coords, as in bends.json
    center=<midpoint of row["line"]>,
    angle=row["angle"],
    direction=row["direction"],
    source_object=f"obj.{label}",
)
```

These features flow through `diagnostics["features"]` and the inspector's
`_merge_features` into `spatial.json["features"]`, exactly like any
`publish_feature` call, so `evaluate_manufacturability` sees them and the two bend
rules bind. This works uniformly for a single `bend()` and for a multi-bend
`bend_chain()` (ADR 0032): every row in `part.bends` yields one feature, so a
U-channel publishes two bend features.

**Frame.** The bend `line` (and its midpoint `center`) is emitted in the
**flat-pattern** frame — the same coordinates `bends.json` already carries — so
`bends.json` and the spatial feature share one definition of every bend line. A
bend line is fundamentally a flat-pattern, press-brake quantity, and hole-to-bend
clearance is a flat-pattern quantity (both the laser and the press brake work on
the flat blank). A design that wants `hole_to_bend` checked therefore reasons about
holes in flat-pattern coordinates. (Holes auto-detected on the *folded* solid are
in folded coordinates and are out of scope here; this ADR wires the rules to fire,
it does not add folded-frame hole projection.)

**Thickness.** A folded sheet-metal part's bounding box has its smallest dimension
equal to a *flange*, not the sheet gauge, so `_resolve_thickness`'s bbox fallback
is not the material thickness for these parts. A `manufacturability` check over a
folded part should therefore pass an explicit `thickness` (the sheet gauge); the
check already supports this. This is documented, not worked around, and keeps
`dfm.py`'s rule semantics unchanged (this ADR only *emits features*).

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_min_bend_radius_fires_on_real_bend_flow`: a design that folds a part with
  a sub-minimum inside radius (0.5 mm on 2.29 mm stock) and publishes it via
  `publish_sheet_metal`, then evaluates `manufacturability` with `min_bend_radius`,
  **fails** naming the emitted bend feature. This is the exact scenario that
  silently passed in the deficiency. Before implementation it passes (inert rule);
  after, it fails as it should.
- `test_min_bend_radius_passes_adequate_radius`: the same flow with an adequate
  inside radius passes — the rule is bound, not merely always-failing.
- `test_hole_to_bend_binds_on_real_bend_flow`: a folded part plus a hole published
  in flat-pattern coordinates too close to the bend line fails `hole_to_bend`,
  naming the hole and the bend.
- `test_bend_chain_emits_two_bend_features`: a U-channel `bend_chain` publishes two
  `kind == "bend"` features, one per bend, each with `inside_radius` and `line`.
- Existing ADR 0016 / 0018 sheet-metal and DFM tests continue to pass; the change
  is additive to `spatial.json` features.

## Consequences

- The bend-safety DFM rules finally gate real bent parts: a sub-minimum radius or a
  hole crowding a bend line is caught before a SendCutSend quote, naming the
  offending feature — closing the silent-pass gap D-004 reported.
- `bends.json` (press-brake table) and the `kind == "bend"` spatial feature (DFM)
  share one flat-pattern definition of each bend line, so they cannot drift.
- A `manufacturability` check over a folded part must supply an explicit
  `thickness`; documented above. Folded-frame hole-to-bend (for holes detected on
  the 3D solid) is left to a future ADR.

## After Action Report

The red state reproduced the deficiency exactly:
`test_min_bend_radius_fires_on_real_bend_flow` **passed** before implementation
(the 0.5 mm radius silently cleared manufacturability because no `kind="bend"`
feature existed), and the hole-to-bend and chain-feature tests failed for want of
the features. After emitting one `kind="bend"` feature per bend from
`publish_sheet_metal`, the sub-minimum radius correctly **fails**
(`min_bend_radius` cites `feat.bracket_bend_0`), the crowding hole fails
`hole_to_bend`, and the U-channel emits `feat.uchan_bend_0` / `feat.uchan_bend_1`.

The feature ids follow `feat.<label>_bend_<index>`, matching the aggregated
`bends.json` order, so a violation names the same bend an operator sees in the
bend table. `publish_feature` is defined after `publish_sheet_metal` in
`registry.py`, but the call is at run time, so the forward reference resolves
without reordering.

As designed, the `manufacturability` check over a folded part supplies an explicit
`thickness` (the sheet gauge), because a folded part's smallest bounding-box
dimension is a flange, not the material thickness — the tests pass `thickness:
2.29`. No `dfm.py` rule semantics changed; the fix is purely additive feature
emission. The full suite passed with 173 tests (169 prior + 4 new), no
regressions.

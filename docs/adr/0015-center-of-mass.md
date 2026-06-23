# ADR 0015: Center of Mass, Inertia, and Stability Checks

## Status

Accepted for implementation.

## Context

Deficiency D5 of `docs/ca-sheet-metal-fixes.md` observes that
`runner._normalize_published()` records `mass_properties = {volume, area}` only.
The harness spec (`docs/agentic-cad-harness-spec.md`) anticipates a
`center_of_mass` (and inertia) on every solid, but neither is emitted, so an
agent cannot reason about tip-over stability or locate a load cell under an
assembly's center of gravity.

This matters for the target SRM test-stand workflow. A multi-plate stand is only
safe if the projected center of mass falls inside its support footprint, and the
load path is only correct if the load cell sits under the assembly center of
gravity. Both require (a) a per-part center of mass in `spatial.json`, (b) a
mass-weighted assembly aggregation across placed parts, and (c) requirement
checks an agent can assert against.

build123d 0.10.0 exposes the needed primitives. `Shape.center(CenterOf.MASS)`
returns the centroid as a `Vector`, and `Shape.matrix_of_inertia` is a property
returning a 3x3 list-of-lists about the centroid. A solid built off the origin —
`Pos(5, 7, 11) * Box(...)` — reports its true shifted centroid, and a part
relocated with `shape.located(Location(...))` reports the relocated centroid, so
center of mass composes correctly with the ADR 0014 placement frame.

Density is required to weight an assembly's center of mass exactly. ADR 0017 will
publish per-part material metadata including density. This ADR lands standalone
before 0017, so the aggregation degrades gracefully: with no density it weights
by volume (uniform density), with densities it weights by mass. The aggregation
reads only normalized facts already in `spatial.json`, so it is pure Python,
deterministic, and needs no CAD kernel in the evaluator.

## Decision

1. **Per-part center of mass and inertia.** A new `runner._mass_properties(obj)`
   helper computes `volume`, `area`, `center_of_mass` (`obj.center(CenterOf.MASS)`)
   and `matrix_of_inertia` (the property, about the part centroid). It runs on the
   *placed* object (via ADR 0014's `_placed_object`), so center of mass and
   bounding box share one frame. Each optional key is guarded: a shape that cannot
   produce a centroid — or a kernel-free environment — omits the key (never null,
   never a run failure). `volume`/`area` are unchanged. Dict publications pass a
   caller-supplied `center_of_mass` through untouched and synthesize nothing.

2. **Assembly aggregation.** `inspector._assembly_center_of_mass(objects)`
   computes the weighted center of mass across published `role == "part"` objects
   that carry a positive `volume` and a `center_of_mass`. It mass-weights
   (`density * volume`) only when *every* contributing part supplied a positive
   `metadata.density`; in any mixed or absent-density case it weights *all* parts
   by volume, so the result is always a consistent centroid rather than a
   unit-inconsistent hybrid of mass and volume weights. The result is written to
   `spatial.json` under a new top-level `assembly = {center_of_mass, mass,
   weighting: "mass"|"volume", part_count}`, present only when ≥1 part qualifies.

3. **`center_of_mass` check.** A new evaluate check `type: center_of_mass` asserts
   a center of mass against a target point with per-axis `tolerance`
   (`expected: [x,y,z]`) or an axis-aligned `region: {min, max}`. The target
   defaults to `assembly` and may also be an object path
   (`obj.<label>.mass_properties.center_of_mass`) via the existing dimension
   grammar. An unresolvable target fails with a descriptive `error` rather than
   raising.

4. **`stability` check.** A new evaluate check `type: stability` projects a center
   of mass onto XY and tests whether it lies inside the support polygon, given as
   an unordered `support: [[x,y], ...]`. It reports the projected point, the
   signed `margin` (shortest distance to the support boundary, positive inside),
   and — when `com_height` is supplied — the worst-case tip angle
   `atan2(margin, com_height)` in degrees, gateable with `min_tip_angle_deg`. The
   math is pure Python (monotone-chain convex hull, ray-cast point-in-polygon,
   point-to-segment distance); the evaluator never imports build123d.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_com_prism`: an off-origin `Pos(40,25,10) * Box(...)` reports
  `center_of_mass` equal to its shifted centroid within 1e-3 mm (absent today).
- `test_inertia_present`: the same part reports a 3x3 `matrix_of_inertia` with a
  positive diagonal (absent today).
- `test_assembly_com_weighted` / `test_assembly_com_density_weighted`: a two-part
  assembly reports the volume-weighted (no density) or mass-weighted (density)
  `assembly.center_of_mass` with the matching `weighting` label (absent today).
- `test_com_check_pass_fail`, `test_com_check_region_and_object_target`: the
  `center_of_mass` check passes/fails against a point, a region, and an object
  target (the type raises today).
- `test_stability_check`, `test_stability_tip_angle`: the `stability` check passes
  inside the support polygon, fails outside (negative margin), and gates on tip
  angle (the type raises today).
- Existing ADR 0001–0014 tests continue to pass: `volume`/`area` and all current
  `spatial.json` / `checks.json` keys are unchanged and only added to.

## Consequences

- Every real solid now advertises where its mass is, so later ADRs (0017 BOM
  densities, 0018 DFM) build on a stable per-part center of mass.
- The `assembly` key is additive and advisory. The `weighting` field tells an
  agent whether the number is a true mass center or a uniform-density
  approximation, so a guess is never silently presented as exact.
- `center_of_mass` and `stability` are pure-`spatial.json` checks, preserving the
  evaluator's no-kernel determinism.
- The support polygon is the convex hull of the supplied support points. For a
  rigid assembly resting on those points this *is* the base of support — tipping
  occurs about the hull edges — so it is the correct model, not merely
  conservative. A non-rigid or genuinely concave footprint (independent feet with
  the center of mass over an unsupported gap) is out of scope and would need an
  explicit non-convex polygon.
- Inertia is reported about the part centroid in model units and is not
  re-expressed in any assembly frame; consumers needing assembly-frame inertia
  must compose it themselves.

## After Action Report

The red state failed as predicted: `mass_properties` had no `center_of_mass` or
`matrix_of_inertia`, `spatial.json` had no `assembly` key, and `center_of_mass`/
`stability` raised `ValueError` as unsupported check types.

`Shape.matrix_of_inertia` is a **property**, not a method (calling it raises), so
it is attribute-accessed inside the same guard as `center(CenterOf.MASS)`; a
kernel-free `_mass_properties(object())` returns exactly `{volume, area}` with no
null keys (covered by a unit test). Running `_mass_properties` on the placed
object was verified to keep center of mass and bbox in one frame for a part placed
with a `Location`.

An adversarial review of the diff drove two fixes. First, the assembly
aggregation originally summed `density * volume` for parts that had a density and
bare `volume` for parts that did not, producing a unit-inconsistent number in
mixed-density assemblies; it now mass-weights only when *every* part has a
density and otherwise weights all parts by volume (covered by
`test_assembly_mixed_density_weights_by_volume`). Second, the degenerate-support
and kernel-failure branches gained explicit tests
(`test_stability_degenerate_support_is_unstable`,
`test_mass_properties_without_kernel_omits_optional_keys`). The review also
clarified that the convex hull is the correct base-of-support model for a rigid
body (not just a conservative approximation), which is reflected in the
Consequences above, and prompted defaulting the `center_of_mass` target to
`assembly` for parity with `stability`.

Coverage: real-geometry tests pin the off-origin centroid and the inertia matrix;
synthetic tests cover volume- and mass-weighted aggregation, the mixed-density
fallback, the point/region/object-target check forms, the inside/outside/tip-angle
stability paths, the degenerate-support and missing-assembly failure paths, and
the kernel-free `_mass_properties`. The full suite passed with 60 tests
(48 prior + 12 new), no regressions.

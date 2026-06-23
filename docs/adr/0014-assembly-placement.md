# ADR 0014: Assembly Placement, Feature Alignment, and Interference Checks

## Status

Accepted for implementation.

## Context

Deficiency D3 (High) in `docs/ca-sheet-metal-fixes.md` observes that
`registry.publish()` stores every object in its own local frame with no
transform or parent. A test stand is many bolted plates, and the dominant
failure mode is a misaligned bolt pattern or interference between parts that
were each modeled at the origin. The harness cannot currently express "part A
is mounted here relative to part B", so it cannot assert that two parts bolt
together (coaxial holes) or that they do not collide.

The evaluator already proves the spatial contract is enough to carry these
assertions: `_aabb_clearance` works purely from `spatial.json` bounding boxes,
and `_check_exact_clearance` (ADR 0009) imports the STEP exports and calls
`Shape.distance` for true BREP clearance. Feature detection (ADR 0007/0010) and
deduplication (ADR 0012) already populate `spatial.json["features"]` with a
`center`, an `axis`, and a `diameter` per cylindrical hole, addressable by a
stable `id` and a `source_object`. What is missing is (1) a common assembly
frame and (2) two checks that consume it.

This ADR adds the placement frame and the two checks. It depends on no other ADR
in this batch; ADR 0015 (assembly center of mass) reuses the same recorded
placement through the shared `_placed_object` helper, and ADR 0013's DXF exports
inherit the placed geometry for free because placement is applied before export.

## Decision

### Placement on `publish`

Extend the public signature to
`publish(label, obj, role="part", placement=None, **metadata)`.

- When `placement` is a build123d `Location`, the runner applies it with
  `obj.located(placement)` before any bbox, mass property, topology, or export
  computation, centralized in a `runner._placed_object(entry)` helper so every
  derived fact observes the same placed geometry. build123d 0.10.0 confirms
  `Shape.located(loc)` returns a new shape whose `bounding_box()` reflects the
  location and that the transform is baked into the STEP/STL/DXF exports (so
  feature detection on the imported STEP observes the placed hole axes).
- The placement is recorded JSON-serialized on the spatial object as
  `object["placement"] = {"position": [x, y, z], "orientation": [rx, ry, rz]}`
  (degrees, build123d intrinsic XYZ convention from `Location.orientation`). This
  is an additive key; every existing object key is untouched.
- For dictionary (synthetic) publications that carry no build123d shape, a
  `placement` argument is still accepted as a `Location`, a `{"position": [...]}`
  mapping, or a bare 3-sequence; the runner translates the dict's `bbox` and
  records the same `placement` key, so evaluator and CLI tests exercise placement
  without a CAD kernel.
- `placement=None` leaves objects exactly as today: no `placement` key is
  written, preserving byte-compatible output for existing designs.

### `feature_alignment` check

A new `evaluate` check `type: feature_alignment` asserts that two features are
coaxial. Each selector picks features by `id` or by `{kind, source_object}`:

```yaml
- id: bolt_pattern_top_left
  type: feature_alignment
  features:
    - {kind: cylindrical_hole, source_object: obj.plateA}
    - {kind: cylindrical_hole, source_object: obj.plateB}
  tolerance: 0.05          # mm and degrees
  diameter_tolerance: 0.1  # optional; defaults to tolerance
```

Two axis lines are **collinear** when their unit directions are parallel
(anti-parallel counts as parallel) within the angular tolerance **and** the
perpendicular distance between the axis lines is within the linear tolerance.
When a selector matches several features (a whole bolt pattern), the check pairs
the **closest-to-coaxial** features across the two match sets rather than relying
on the two parts' holes being detected in the same order — i.e. it asks "does
each hole on A have a coaxial partner on B". The result reports the two resolved
feature ids, the measured `axis_offset`, `axis_angle_deg`, and the two diameters,
so a failure names which holes are misaligned and by how much. A selector that
resolves zero features fails with a descriptive `error` in the result rather than
raising.

### `interference` check

A new assembly-wide `evaluate` check `type: interference` flags any pair of
solids that physically overlap. `Shape.distance` returns `0.0` for both touching
and overlapping solids (verified), so distance alone cannot distinguish a flush
mate from a collision. Instead the check imports each object's STEP export
(reusing the `_step_export_index` path from ADR 0009) and computes
`left.intersect(right).volume`; a positive intersection volume above a small
`tolerance` (default `1e-6 mm^3`) is an interference. The check reports every
offending `[left_label, right_label]` pair and its overlap volume and passes when
no pair overlaps. By default it considers all published objects; an optional
`between` list restricts it to named objects. For synthetic dict publications
(no STEP export) the check falls back to an AABB-overlap test on the recorded
bboxes.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_placement_roundtrip`: a real `Box` published with
  `placement=Location((dx, dy, dz))` reports a `spatial.json` bbox shifted by
  `(dx, dy, dz)` and carries `placement.position == [dx, dy, dz]`; the unplaced
  part has no `placement` key.
- `test_alignment_pass` / `test_alignment_fail_names_feature_ids`: two plates
  sharing a 4-hole pattern, the second placed coaxially, pass `feature_alignment`
  with `axis_offset ~ 0`; offsetting the second plate in-plane by 3 mm makes the
  check fail, names both resolved feature ids, and reports `axis_offset ~ 3`.
- `test_interference_detects_overlap`: two solids placed to overlap fail
  `interference` and report the offending label pair with a positive overlap
  volume; placed apart they pass.
- Synthetic evaluator tests cover selector resolution, the alignment math, the
  AABB interference fallback, the `between` subset, and dict-publication
  placement without a CAD kernel.
- Existing ADR 0001–0013 tests continue to pass, including the `cadx init`
  starter flow, since every change is additive and `placement=None` is the
  default.

## Consequences

- `publish` gains a fourth positional-or-keyword parameter before `**metadata`.
  Existing call sites are unaffected.
- `spatial.json` objects gain an optional `placement` record, and `checks.json`
  gains `feature_alignment` and `interference` result shapes. All additive.
- Interference is BREP-exact via intersection volume, so it correctly treats a
  flush mate (zero volume) as non-interfering, unlike a naive `distance == 0`
  test, at the cost of one STEP import per object and one boolean per pair.

## After Action Report

The red state failed as predicted: `publish` ignored `placement` (the placed box
reported an unshifted bbox), and `feature_alignment`/`interference` raised
`ValueError` as unsupported check types.

Implementation surfaced a real **cross-ADR interaction bug** that the per-feature
tests of ADR 0012 could not have caught. ADR 0012 deduplicates cylindrical
features by *radial* distance to the detected axis line (so a hole's published
"center" may sit anywhere along its axis). With placement, two plates stacked for
bolting have holes whose axes are genuinely collinear — and the dedup merge,
which accumulates detected features and compared each new detection against the
growing list, collapsed plate B's holes into plate A's, so plate B's holes
vanished from `spatial.json` entirely and `feature_alignment` found nothing to
align. The fix adds a guard to `inspector._is_duplicate`: features that name
different `source_object`s are never merged (an explicit publication without a
`source_object` stays unconstrained, so ADR 0012's explicit/detected
corroboration is preserved). The ADR 0012 starter-flow and phantom-feature tests
still pass.

The second discovery was that build123d's STEP face ordering is **not**
translation-invariant, so "first detected hole of plate A" and "first detected
hole of plate B" are not the same corner. A first-match selector would compare
mismatched corners and report a spurious 30 mm offset. `feature_alignment`
therefore resolves each selector to *all* matching features and reports the
best-aligned (minimum-offset) pair, which is both robust to detection order and
the semantically correct "is there a coaxial partner" question.

`Shape.intersect(other).volume` cleanly distinguishes overlap (positive) from a
flush touch or separation (zero), confirming the design's choice of intersection
volume over `distance`. `_step_export_index` is guarded so the synthetic AABB
fallback works when no `diagnostics.json` exists.

An adversarial review of the diff drove two robustness fixes: `interference` now
returns a descriptive `error` result when a `between` reference names a missing
object (instead of a bare `KeyError` aborting the whole `evaluate` run), matching
`feature_alignment`; and `feature_alignment` never pairs a feature with itself,
so a too-broad selector that resolves both sides to the same feature fails
explicitly rather than reporting a vacuous self-aligned pass.

Coverage: real-geometry tests (`test_placement_roundtrip`, `test_alignment_pass`,
`test_alignment_fail_names_feature_ids`, `test_interference_detects_overlap`)
exercise placement apply/record, detection through placement, the alignment math
on detected features, and the BREP intersection path; kernel-free tests
(`test_dict_publication_placement_translates_bbox`,
`test_feature_alignment_synthetic`, `test_feature_alignment_missing_selector_fails`,
`test_interference_synthetic_aabb`, `test_interference_between_limits_pairs`)
cover the dict-placement branch, selector resolution, the alignment failure path,
and the AABB fallback and `between` subset, plus the missing-selector and
missing-object error paths and the self-pair guard. The full suite passed with 48
tests (34 prior + 14 new), no regressions.

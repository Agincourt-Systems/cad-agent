# ADR 0052: `view_cone` Field-of-View Containment Check

## Status

Accepted for implementation.

## Context

Deficiency **D-027** (`docs/specs/arm-deficiencies.md`, filed by the downstream
robot-arm wrist group): their spec §5.4 requires that the gripper jaw tips sit
inside a wrist camera's field-of-view (FOV) cone and that the sightline from the
camera to each tip is unobstructed. cadx has **no FOV / view-angle / occlusion
check primitive**. The group had to compute containment as pytest assertions
over raw `spatial.json` coordinates (a published construction cone is geometry
only, not a check), and plate occlusion of the sightline was not expressible at
all.

The existing checks that reason about geometric relationships between parts
(`clearance`, `interference`, `center_of_mass`, `stability`) all work from
`spatial.json` facts, per the evaluator's design contract: it "intentionally
works from `spatial.json` instead of CAD objects" so an agent gets a
deterministic observation without loading the kernel. A FOV check fits the same
mould: an apex point, an axis direction, a half-angle, and a set of target
objects whose extents are already recorded as bounding boxes in `spatial.json`.

## Decision

Add a new check `type: view_cone`. It is **additive**: one new evaluator
function plus one dispatch entry in `_evaluate_check`; no existing code path
changes.

### Schema

```yaml
- id: jaws_in_fov
  type: view_cone
  apex: [0, 0, 0]              # world point, OR a reference string (see below)
  axis: [0, 0, 1]             # direction; normalized internally
  half_angle_deg: 30          # cone half-angle in degrees, (0, 180]
  targets:                    # object references and/or explicit points
    - obj.left_jaw_tip
    - obj.right_jaw_tip
  occluders:                  # optional line-of-sight blockers (object labels)
    - obj.wrist_plate
```

- **`apex`** — either an explicit `[x, y, z]` world point, or a reference
  string. The reference grammar reuses the existing dimension resolver
  (`_resolve_dimension`), so any path that resolves to a 3-vector is accepted
  (e.g. `obj.<label>.mass_properties.center_of_mass`). As a convenience,
  `obj.<label>.center` and `obj.<label>.bbox.center` resolve to the midpoint of
  the object's world bounding box (objects carry a `bbox`, not a `center` key, so
  this shorthand is computed rather than looked up).
- **`axis`** — a direction `[x, y, z]`, normalized internally. A zero-length axis
  is a loud error.
- **`half_angle_deg`** — the cone half-angle. Must be in `(0, 180]`; a negative,
  zero, or `>180` value is a loud error.
- **`targets`** — a list; each entry is either an object reference `obj.<label>`
  (tested by its bounding-box points, below) or an explicit `[x, y, z]` point
  (tested as a single point).
- **`occluders`** — optional list of object references whose solids must not
  block the sightline (see Occlusion).

### Containment semantics (explicit)

For an **object** target, the test points are the **8 corners of its world
bounding box AND the box center** (9 points). The target passes only when
**every** one of those points is inside the cone. Testing the corners (not just
the center) means the whole extent of the target must be visible, which is the
conservative reading of "the jaw tips are inside the FOV" — a jaw whose center is
visible but whose tip pokes out of the cone fails, as it should. For an explicit
**point** target, that single point is the only test point.

A single point `P` is **inside** the cone defined by apex `A`, unit axis `U`, and
half-angle `θ` when:

1. `v = P − A` is on the **+axis side**: `along = dot(v, U) > 0`. A point at the
   apex (`v = 0`) or behind the apex plane is **outside** (a camera does not see
   behind itself; the apex direction is undefined and treated as outside).
2. the angle between `v` and `U` is within the half-angle:
   `acos(clamp(along / |v|, −1, 1)) ≤ θ`.

The reported `angle_deg` for a target is the **worst (largest)** angle over its
test points, so the failure output names how far outside the cone the target's
most-offending point is.

### Occlusion (approximation, documented)

The evaluator has `spatial.json` (bounding boxes) always, and per-object STEP
exports only sometimes (a synthetic publication has none). `interference` and
exact `clearance` degrade from BREP to AABB in exactly this situation. Rather
than split behavior, `view_cone` occlusion uses a **single, deterministic
`spatial.json`-only approximation**: a target test point is **occluded** when the
line segment from the apex to that point intersects the **axis-aligned bounding
box** of any occluder.

An occluder's AABB is a superset of its solid, so this test can only ever report
occlusion where the true solid would *not* block (a false "occluded"), never miss
a real block. For a "the tip **must** be visible" safety requirement that
directional error is the safe one: it fails loud rather than passing a blocked
sightline. The occluder's own points sitting on the segment endpoints are
ignored — an occluder does not occlude a point on its own surface — by requiring
a positive-length overlap strictly between the endpoints. The approximation is
recorded in the output as `occlusion_method: "aabb"` so a consumer knows the
fidelity. A target fails if **any** of its test points is outside the cone **or**
occluded.

Occlusion is **not** silently deferred: it is implemented (as the AABB
approximation) and always evaluated when `occluders` is present.

### Error handling (loud, never a silent pass)

Matching the error-on-unknown pattern of ADR 0049 (`frame:`) and the graceful
degradation of the assembly checks, a malformed or unresolvable configuration
fails **that one check** with a descriptive `error` naming the bad value, and
never aborts the run or passes silently:

- missing/zero `axis` → error;
- missing, negative, zero, or `>180` `half_angle_deg` → error;
- an `apex` reference that does not resolve to a 3-vector → error;
- a target or occluder reference to an unknown object, or an object with no
  `bbox` → error.

### Output shape

```json
{
  "id": "jaws_in_fov",
  "type": "view_cone",
  "status": "fail",
  "apex": [0, 0, 0],
  "axis": [0.0, 0.0, 1.0],
  "half_angle_deg": 30,
  "occlusion_method": "aabb",
  "targets": [
    {"target": "obj.left_jaw_tip", "status": "pass", "angle_deg": 8.1, "occluded": false},
    {"target": "obj.right_jaw_tip", "status": "fail", "angle_deg": 41.2,
     "occluded": false, "worst_point": [70, 5, 50], "reason": "angle_exceeds_half_angle"}
  ]
}
```

`reason` is one of `angle_exceeds_half_angle`, `behind_apex`, or `occluded`, so a
failing target is diagnosable without re-deriving the geometry.

## Alternatives considered

- **True frustum (rectangular pyramid) instead of a cone.** A real camera FOV is
  a rectangular pyramid, not a right circular cone. A cone is simpler to specify
  (one half-angle, no up-vector / aspect ratio) and is the conservative inner
  bound of a symmetric frustum, which suits a "tips must be inside" requirement.
  The suggested fix in D-027 explicitly asks for a "frustum-containment **or**
  vector-angle-bound check"; the vector-angle cone is the smaller, sufficient
  primitive. A rectangular frustum can be a later, additive extension.
- **Exact BREP occlusion (segment vs. imported STEP solid).** More faithful, but
  it requires STEP exports (absent for synthetic runs), is far slower, and its
  directional error (missing a thin blocker) is the *unsafe* one. The AABB
  approximation is deterministic from `spatial.json` alone and errs safe.
- **Testing only the target center.** Rejected: a target whose center is visible
  but whose extent pokes out of the cone would pass, defeating the check's
  purpose. Corners + center is the conservative choice.

## Success Criteria

Written so the new tests (`tests/test_view_cone.py`) fail before implementation
(unknown check type → `ValueError`) and pass after:

- `test_target_inside_cone_passes` — a box squarely within a 45° cone passes;
  the reported worst `angle_deg` is well under the limit.
- `test_axis_is_normalized` — a non-unit axis (`[0, 0, 7]`) gives the same
  verdict as the unit axis.
- `test_target_outside_cone_fails` — a box beyond the half-angle fails with
  `reason: angle_exceeds_half_angle` and `angle_deg` above the limit.
- `test_target_behind_apex_fails` — a box on the −axis side fails with
  `reason: behind_apex`.
- `test_apex_reference_resolves` — `apex: obj.cam.center` resolves to the object's
  bbox center and the check passes.
- `test_explicit_point_target` — an `[x, y, z]` target point is tested directly.
- `test_occluder_blocks_sightline_fails` — a plate between apex and an
  in-cone target fails with `reason: occluded`.
- `test_occluder_off_to_the_side_does_not_block` — an occluder that misses every
  sightline leaves the check passing.
- `test_missing_axis_is_a_clear_error`,
  `test_negative_half_angle_is_a_clear_error`,
  `test_unknown_target_is_a_clear_error` — malformed config fails loudly with the
  bad value named, never a silent pass.
- Existing ADR 0001–0051 tests pass unchanged (additive dispatch entry).

## Consequences

- The wrist group (and any FOV / sensor-coverage requirement) can express §5.4
  directly as a check instead of hand-rolled pytest over coordinates, and can
  gate on unobstructed sightlines for the first time.
- Occlusion fidelity is AABB-approximate and errs on the safe side; a future ADR
  can add exact BREP occlusion behind an opt-in `method: exact`, mirroring
  `clearance`, without changing the default.
- The change is confined to `evaluate.py` (one function + one dispatch line) and
  the README check list; the inspector, runner, and registry are untouched.

## After Action Report

**AAR: pending downstream verification.** _To be completed after the wrist group
consumes `view_cone` against their spec §5.4 geometry and confirms the
containment and occlusion verdicts match their hand-rolled assertions._

# ADR 0038: Export Mate Frames, Joint Axis, Zero-Pose Origin, and Root Identity Placement

## Status

Accepted for implementation.

## Context

Two downstream deficiencies (`docs/specs/arm-deficiencies.md`) block a machine
consumer — specifically a URDF generator — from reconstructing an assembly's
kinematics purely from `spatial.json`:

- **D-013 (MAJOR).** ADR 0024/0025 record a mate's *intent* on the spatial
  object as `objects[].mate`, but `_normalize_published` (runner.py) keeps only
  the JSON-safe identity/pose fields (`to`, `joint`, `target_joint`, `kind`,
  `angle`, `travel`, `angle_range`, `travel_range`). The **anchor** and
  **target** `Location`s that actually define the joint — the frames
  `_mate_placement` composes — are dropped. Only the *posed* child placement
  survives, which conflates the joint origin with the current pose. A URDF
  consumer therefore cannot recover the joint axis (URDF `<joint><axis>`) or the
  zero-pose parent→child transform (URDF `<origin>`); it must re-read the design
  source out of band. The downstream probe pins the gap with
  `assert "axis" not in arm["mate"]`.

- **D-014 (MINOR).** A root or otherwise unmated part is published with no
  `placement=`, so `_normalize_published` emits no `placement` key at all. A
  consumer reading "the world frame of the base link" gets nothing and must
  special-case a missing key as identity. When several parts are unplaced the
  absence is ambiguous.

Both are pure serialization gaps: the geometry math (ADR 0024's
`parent * target * anchor⁻¹` and ADR 0025's `parent * target * J(pose) *
anchor⁻¹`) is already correct and kernel-verified. This ADR widens what
`spatial.json` *records* about that math; it changes no placement, export, or
check result.

## Decision

### 1. Serialize the defining frames into the mate record (D-013)

`_normalize_published` gains three new keys inside `objects[].mate`, computed
where the placement itself is resolved (`_resolve_mates`, which is the one place
that holds both the child entry and its parent entry — the parent is needed to
read a `RigidJoint`-spelled `target` off the parent shape). A new helper,
`_mate_frame_export(entry, parent)`, produces:

- **`anchor`** and **`target`**: each a `{"position": [x,y,z], "orientation":
  [rx,ry,rz]}` record built with the existing `_placement_record` helper, so the
  serialization matches `placement` exactly (intrinsic XYZ Euler degrees, the
  ADR 0014 convention). These are the frames *as authored* — anchor in the
  child's local coordinates, target in the parent's local coordinates. For the
  native-joint spelling they are the resolved `RigidJoint.relative_location`s
  (via the existing `_mate_frames`). For synthetic dict publications they are
  the translation-only 3-sequences (orientation `[0,0,0]`).

- **`axis`** (kinematic kinds only — `revolute`, `prismatic`, `cylindrical`):
  the joint axis as a unit vector **expressed in the world/assembly frame** (the
  same frame as `placement`). Per the ADR 0025 convention the joint axis is the
  *target frame's local Z*. Expressed in world it is the image of the local unit
  Z under the world target frame `W = parent_placement * target`, minus that
  frame's origin, normalized:

  ```
  axis = normalize( (W * Location((0,0,1))).position − W.position )
  ```

  This is Location algebra identical to what `_mate_placement` composes, so the
  axis is guaranteed consistent with the posed placement. `rigid` mates (URDF
  *fixed* joints) have no joint axis, so the key is omitted for them. For
  synthetic (translation-only) prismatic frames the world axis is `[0,0,1]`
  by construction.

- **`origin`**: the **zero-pose origin** — the child's placement at joint value
  0 — as a `{"position", "orientation"}` record. The exact formula, verified
  against `_mate_placement`'s composition (which reduces to this when the pose
  `J` is the identity at `angle = travel = 0`):

  ```
  origin = parent_placement * target * anchor⁻¹
  ```

  expressed in the **world/assembly frame**. At pose 0, `origin` equals the
  posed `placement`; at any other pose the posed placement is
  `parent_placement * target * J(pose) * anchor⁻¹`, so a consumer that wants the
  URDF parent-*relative* joint origin recovers it as
  `parent_placement⁻¹ * origin = target * anchor⁻¹` — always possible now that
  the parent placement is itself always recorded (see D-014 below). For
  synthetic entries the origin is the translation-only
  `parent_position + target_position − anchor_position`.

The frame export is computed only for mates that resolve successfully. A
mate that degrades to `mate_unresolved` / `mate_failed` writes no frame export
(and, per D-014, no placement) — the warning is the record that the part could
not be placed.

Because `anchor`, `target`, and `parent_placement` (D-014) are now all present,
a URDF generator can reconstruct the posed placement at *any* joint value
without the design source: `parent * target * J(θ) * anchor⁻¹`. `axis` and
`origin` are conveniences derived from the same frames.

### 2. Normalize root/unmated parts to an explicit identity placement (D-014)

In `_normalize_published`, when a part has no resolved placement:

- **No `mate` declared** → the part is a root/base sitting at the assembly
  origin. Emit an explicit identity record
  `{"position": [0,0,0], "orientation": [0,0,0]}`.
- **A `mate` declared but unresolved** (unknown target, cycle, failed frame) →
  the part is *genuinely unplaced*. Keep the `placement` key absent so the
  warning-plus-absence contract from ADR 0024/0025 still flags it. Overwriting
  it with identity would silently seat a part that the resolver could not place
  — exactly the silent-misplacement class ADR 0024 exists to remove.

The identity is a record-level default only; it composes as a no-op translation,
so no bbox, mass, export, or check result changes.

### Schema-pinning tests updated (intentional, recorded here)

Three existing tests pin the *old* narrower schema and are updated to the new
one under this ADR's authority:

- `test_assembly_placement.py` (ADR 0014) asserted `"placement" not in
  objects["base"]` for an unmated root in two places. Under D-014 an unmated
  root now carries an identity placement, so both assertions become
  `objects["base"]["placement"] == {"position":[0,0,0],"orientation":[0,0,0]}`.
- The `objects[].mate == {...}` exact-equality assertions in
  `test_joint_placement.py` and `test_kinematic_joints.py` now also carry the
  additive `anchor`/`target`/`origin` (and `axis` for kinematic) keys. They are
  relaxed to assert the stable identity/pose fields plus the presence and shape
  of the new frame keys, so they keep pinning intent without duplicating the
  numeric frame values that ADR 0038's own tests pin exactly.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_revolute_mate_exports_frames_axis_and_origin` (kernel): a revolute mate
  with a **non-origin parent placement** and an **off-axis, rotated target**
  (`base` at `(100,0,0)`, `anchor=Location((-10,0,0))`,
  `target=Location((0,0,20),(0,90,0))`, `angle=30`) exports `anchor`/`target`
  records equal to the authored frames, `axis == [1,0,0]` (hand-computed world
  local-Z of the Y-90 target), and `origin` at `(100,0,10)` orientation
  `(0,90,0)` (hand-computed `parent*target*anchor⁻¹` at pose 0). Fails today:
  `"axis" not in mate`.
- `test_exported_frames_reconstruct_posed_placement` (kernel): rebuilding
  `parent * target * J(30°) * anchor⁻¹` purely from the exported
  `anchor`/`target`/parent `placement` reproduces the exported posed
  `placement` — the downstream URDF reconstruction. Fails today: frames absent.
- `test_prismatic_synthetic_mate_exports_frames` (kernel-free): a synthetic
  prismatic mate exports translation-only `anchor`/`target`/`origin` records and
  `axis == [0,0,1]`.
- `test_root_part_gets_identity_placement` (kernel-free): an unmated root part's
  `placement` is `{"position":[0,0,0],"orientation":[0,0,0]}`.
- `test_unresolved_mate_still_has_no_placement` (kernel-free): a part whose mate
  target is unknown keeps no `placement` key and no frame export (regression
  guard for the D-014 carve-out).
- Existing ADR 0001–0037 tests pass, with the three schema-pinning updates above.

## Consequences

- A URDF (or any kinematic) consumer reconstructs every joint — axis, zero-pose
  origin, and posed transform at arbitrary joint values — from `spatial.json`
  alone, closing D-013's out-of-band-source dependency. The downstream
  workaround (authoring-layer frame records) becomes redundant.
- Every part carries an unambiguous world frame; consumers need no
  missing-key/identity special-casing (D-014).
- The mate record schema grows additively. This ADR supersedes ADR 0025's
  "rigid records byte-identical to ADR 0024" property by design: rigid mates now
  also export `anchor`/`target`/`origin` (URDF fixed-joint data). The change is
  intent-preserving — no resolved placement or check verdict moves.

## After Action Report

All five success-criteria tests were red before implementation (the frame keys
and root identity placement absent) and green after. The implementation stayed
in the mate/placement normalization paths exactly as scoped: one new
`_mate_frame_export` helper plus a `_world_axis` helper in `runner.py`, an
atomic placement+export assignment in `_resolve_mates`, and the record-level
merge and D-14 identity default in `_normalize_published`. No export, check,
render, or resolved-placement result moved — this was purely additive
serialization, confirmed by the rest of the suite passing untouched.

The pre-implementation kernel probe (a translated parent + a Y-90 target)
de-risked the two non-obvious pieces: `Location * Vector` is unsupported for
transforming a point, so the world axis is derived by composing
`world_target * Location((0,0,1))` and subtracting the frame origin (verified
`[1,0,0]` for the Y-90 target); and the zero-pose origin
`parent * target * anchor⁻¹` was cross-checked to equal `_mate_placement` at
pose 0. The `test_exported_frames_reconstruct_posed_placement` test proves the
downstream URDF reconstruction round-trips: rebuilding
`parent * target * J(30°) * anchor⁻¹` from only the exported frames reproduces
the exported posed placement.

Three schema-pinning tests were updated under this ADR's authority as recorded
in the Decision: the two `test_assembly_placement.py` root-identity assertions
(D-14) and the five `objects[].mate == {...}` exact-equality assertions in
`test_joint_placement.py` / `test_kinematic_joints.py` (D-13), now asserting the
stable identity/pose fields plus the additive frame keys. Full suite: **171
passed** (166 prior + 5 new), no regressions.

D-14's carve-out held: a part that declares a mate but cannot resolve it keeps
no placement key (regression-guarded by `test_unresolved_mate_still_has_no_placement`),
so the warning-plus-absence "genuinely unplaced" signal from ADR 0024/0025 is
preserved while true roots gain their identity frame.

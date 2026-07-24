# ADR 0054: Emit the Resolved World Joint Frame per Mate

## Status

Accepted for implementation.

## Context

Deficiency **D-034** (`docs/specs/arm-deficiencies.md`): the mate record exports
axis *directions* and parent-relative *origins* (ADR 0038 world frames, ADR 0048
parent-relative frames), but no **world-frame point that lies on the joint axis**.
A URDF generator needs a concrete world point on the axis (plus the axis
direction) to place and reason about the joint; today it reconstructs that point
from datums and servo placements downstream.

The fields already exported are close but not this:

- `axis` / `axis_in_parent` — the joint *direction* (a unit vector; no point).
- `origin` / `origin_in_parent` — the **child link** zero-pose origin,
  `parent · target · anchor⁻¹`. This is where the *child* sits, not a point on
  the joint axis. For a revolute whose target frame is offset from the child
  anchor, the child origin is generally **not** on the axis.

The joint axis is, by ADR 0025, the **target frame's local Z**. A canonical world
point on that axis is therefore the world target frame's **origin**, and the axis
direction is that frame's world local-Z — exactly the `world_target` composition
`_mate_frame_export` already builds and `_world_axis` already reads. That point and
direction are what D-034 asks for, and they are currently thrown away after the
child origin is computed.

### Why "posed" and "zero" configurations both

The deficiency asks for the world joint frame "at the posed and zero
configurations". The joint frame at joint value 0 is `parent · target`; at the
posed value it is `parent · target · J(pose)`, where `J` is the ADR 0025 pose
`Location((0, 0, travel), (0, 0, angle))`.

- **Revolute** (`J` is a rotation about local Z): the origin and the axis are
  invariant under `J` (rotating a frame about its own Z moves neither its origin
  nor its Z), so the posed and zero frames coincide. Emitting both is still
  correct and lets a consumer treat every kind uniformly.
- **Prismatic** (`J` is a translation along local Z): the posed origin slides
  `travel` along the axis while the direction is unchanged. Here the two frames
  genuinely differ, and both are useful — the zero frame anchors the joint, the
  posed frame is where the axis point currently is.

Both are cheap to emit from data already in hand, and each is independently
hand-verifiable.

## Decision

`runner._mate_frame_export` gains two additive fields, emitted **only for the
kinematic kinds** (`revolute`, `prismatic`, `cylindrical`) that already carry an
`axis` — a `rigid` (URDF fixed) mate has no joint axis and stays byte-identical:

- `joint_world_zero` — the world joint frame at joint value 0:
  `{"origin": <point on the axis>, "axis": <unit direction>}` where
  `origin = (parent · target).position` and `axis = _world_axis(parent · target)`.
- `joint_world` — the world joint frame at the *posed* value:
  `{"origin": (parent · target · J(pose)).position, "axis": _world_axis(parent · target · J(pose))}`.

`origin` is a bare `[x, y, z]` world point (a point has no orientation); `axis` is
the existing world unit vector. Points get the full transform; axes transform
rotation-only, which `_world_axis` already enforces by construction (it is the
image of local +Z minus the frame origin, normalized).

### Synthetic (kernel-free) mates

Synthetic dict publications carry translation-only frames and support only rigid
and prismatic kinds. There `parent · target` is a pure translation
(`parent_position + target_position`) with axis `[0, 0, 1]`, and the pose adds
`travel` along Z:

- `joint_world_zero = {"origin": parent + target, "axis": [0, 0, 1]}`
- `joint_world = {"origin": parent + target + [0, 0, travel], "axis": [0, 0, 1]}`

This mirrors the existing synthetic branch so the machinery stays testable without
a CAD kernel, and makes the posed-vs-zero distinction visible (the origins differ
by `travel` along the axis).

The fields ride in the same `mate_export` block as the existing world and
parent-relative frames; an unresolved mate exports none of them (unchanged).

## Alternatives considered

- **Reuse the existing `origin` as the axis point.** Rejected: `origin` is the
  child link origin (`… · anchor⁻¹`), which is generally not on the joint axis.
- **Emit only one configuration.** Rejected: prismatic needs both; emitting both
  for every kind keeps the record uniform and matches the deficiency text.
- **Emit orientation on the joint point.** Unnecessary: the field is a *point on
  the axis*; the direction is the `axis` vector. Keeping origin a bare point makes
  the "lies on the axis" contract unambiguous.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_revolute_joint_world_frame` (kernel): the ADR 0038 translated-parent /
  Y-90-target revolute at angle 30 exports `joint_world_zero.origin ==
  [100, 0, 20]` (the world target origin, a point on the axis) and
  `joint_world_zero.axis == [1, 0, 0]`; `joint_world` equals `joint_world_zero`
  for the revolute (rotation about the axis moves neither). Both differ from the
  child `origin` (`[100, 0, 10]`), proving the joint point is not the child
  origin. Fails today: the keys are absent.
- `test_zero_joint_frame_matches_parent_target` (kernel): reconstructing
  `parent · target` from the exported parent placement and `target` record
  reproduces `joint_world_zero` (origin and axis) — the "matches the
  parent·target composition at angle 0" check.
- `test_posed_point_lies_on_joint_axis` (kernel-free, prismatic synthetic,
  travel 7): `joint_world.origin − joint_world_zero.origin` is parallel to the
  axis (`[0, 0, 7]` here) and both origins satisfy the line
  `origin_zero + t · axis`. Proves the posed point moved along the axis and still
  lies on it.
- `test_rigid_mate_has_no_joint_world` (kernel-free): a rigid mate exports
  neither `joint_world` nor `joint_world_zero`.
- Existing ADR 0038 / ADR 0048 mate-frame tests pass unchanged (additive keys).

## Consequences

- A URDF generator reads a concrete world point on the joint axis and its
  direction directly, at both the joint's zero and current pose, with no datum or
  servo-placement reconstruction.
- The mate record grows purely additively; no existing world, parent-relative, or
  posed-placement field moves. Rigid mates are byte-identical.
- Prismatic consumers gain a correct posed axis point for free; revolute
  consumers see the invariance stated explicitly by the two coinciding frames.

## After Action Report

_Pending downstream verification._

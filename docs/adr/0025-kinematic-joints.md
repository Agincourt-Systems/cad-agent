# ADR 0025: Kinematic Joint Types (Posed Mates and Motion-Envelope Checks)

## Status

Accepted for implementation.

## Context

ADR 0024's rigid mate covers bolted assemblies, but mechanisms have parts that
*move*: a hinged lid, a sliding carriage, a rotating-and-plunging piston. Two
things are missing:

1. **Posed placement.** A design cannot say "this flap hinges on that edge,
   currently open 30°" — it must bake the pose into a hand-built rigid frame,
   which is the transform arithmetic ADR 0024 exists to remove.
2. **Motion-envelope verification.** The dominant mechanism failure is not
   the resting pose but some pose *within travel*: the lid that collides at
   80°, the slide that overshoots its stop. The harness has no way to assert
   "no interference at any hinge angle in range".

A kernel probe (2026-07-03) settled the two design facts. First, the joint
variable composes as one Location: ``placement = parent * target *
J(pose) * anchor⁻¹`` with ``J = Location((0, 0, travel), (0, 0, angle))``
rotates/slides the child about the *target frame's local Z* exactly as a
hinge/slider should (and translation/rotation about the same axis commute, so
one literal covers the cylindrical case). Second, build123d's
``RevoluteJoint`` exposes only ``relative_axis`` — an axis is not a full
frame (the angle-zero reference is a separate convention) — so consuming
native kinematic joint objects is deferred; frames come from explicit
``Location``s or ``RigidJoint`` names exactly as in ADR 0024.

## Decision

### Pose-carrying mate kinds

``mate()`` gains ``kind`` plus pose variables and optional declared limits:

- ``kind="rigid"`` (default): exactly ADR 0024, no pose arguments allowed.
- ``kind="revolute"``: ``angle`` (degrees, default 0) rotates the child about
  the target frame's Z axis. Optional ``angle_range=(min, max)``.
- ``kind="prismatic"``: ``travel`` (mm, default 0) slides the child along the
  target frame's Z axis. Optional ``travel_range=(min, max)``.
- ``kind="cylindrical"``: both ``angle`` and ``travel`` about/along the same
  axis (drill press quill, piston).

Conventions: **the joint axis is the mated frame's local Z**, and the anchor
frame's X at ``angle=0`` aligns with the target frame's X — orient the target
frame to point the axis, which the ADR 0014 `Location` orientation already
expresses. Passing a pose argument foreign to the kind (``angle`` on rigid or
prismatic, ``travel`` on rigid or revolute, an unknown ``kind``) raises
``ValueError`` at design time, matching the placement/mate conflict rule.

Resolution seam, failure modes, chains, and the spatial record all carry over
from ADR 0024 unchanged; the record gains ``kind`` (only when not rigid),
``angle``/``travel``, and any declared ranges — so ADR 0024's pinned rigid
records stay byte-identical. A pose outside its declared range **places the
part as requested** and emits a ``mate_out_of_range`` warning: the geometry
stays honest to what was asked, the declared-limit violation is flagged, and
downstream checks still see the offending pose.

Synthetic dict publications support ``prismatic`` (their frames are
translation-only, so travel is a Z offset, matching ADR 0014's synthetic
placement semantics). ``revolute``/``cylindrical`` on a dict object degrade
to a ``mate_failed`` warning — rotating a synthetic bbox has no defined
meaning.

### Motion envelopes are parametric sweeps — no new evaluator machinery

The pose is ordinary design input: ``mate(..., angle=params.get("lid_angle",
0))``. That makes ADR 0020's ``parametric`` check the motion-envelope
verifier *as already shipped*: sweep the pose parameter, run ``interference``
/ ``clearance`` sub-checks per set, and the aggregate passes only when every
pose passes. This ADR adds no evaluator code — its flagship test and the
README recipe pin the composition instead, so the pattern is contract, not
coincidence.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_prismatic_mate_slides_synthetic_child`: a dict child with
  ``travel=7`` onto target ``[10, 0, 5]`` lands at ``[10, 0, 12]`` and its
  spatial mate record reads ``kind: "prismatic", travel: 7``. Fails today:
  ``mate()`` rejects ``kind``.
- `test_revolute_mate_on_synthetic_object_warns`: revolute on a dict object
  yields ``mate_failed``, an unplaced part, run ``ok``.
- `test_pose_outside_declared_range_warns`: ``travel=30`` against
  ``travel_range=(0, 20)`` places at 30 with a ``mate_out_of_range`` warning.
- `test_pose_argument_foreign_to_kind_is_an_authoring_error`: ``kind="rigid"``
  with ``angle=30`` fails the run with a message naming both.
- `test_revolute_mate_rotates_about_target_axis` (kernel): the probe geometry
  — flap anchored at its edge onto a hinge frame — read with the pose from
  ``params``: at 0° the flap spans x∈[50, 70]; at 90° it spans y∈[0, 20] with
  placement orientation (0, 0, 90). Two runs of one design differing only in
  ``params.yaml`` pin the params-driven pose pattern.
- `test_cylindrical_mate_combines_travel_and_angle` (kernel): travel and
  angle both applied about the same target-frame axis.
- `test_motion_envelope_sweep_catches_interference` (kernel, flagship): a
  swing arm whose ``arm_angle`` param feeds its revolute mate, swept by a
  ``parametric`` check over an interference sub-check — the clear pose
  passes, the colliding pose fails, and the aggregate reports per-set
  verdicts.
- Existing ADR 0001–0024 tests continue to pass unchanged (rigid mate spatial
  records byte-identical).

## Consequences

- Mechanism designs state "hinged here, posed at θ" in one declaration, and
  an agent sweeps θ through the documented parametric recipe to verify the
  whole travel — closing the collides-mid-swing failure class with zero new
  check types, CLI surface, or schema versions.
- Declared ranges make joint limits machine-visible: out-of-range poses are
  flagged at run time and recorded on the spatial object for downstream
  consumers.
- Deferred by decision: consuming native build123d ``RevoluteJoint``/
  ``angular_range`` objects (axis-only, no angle-zero reference — needs its
  own convention), ball joints (three pose angles, no single-axis
  convention), and any constraint *solving*. Each can layer on the same
  ``J(pose)`` seam later.

## After Action Report

All seven success-criteria tests were red before implementation and green
after with no design changes: the probe-first pattern (Location pose math and
the RevoluteJoint API surveyed before the ADR was written) again meant the
Decision section survived contact with the kernel intact.

The implementation is small because the ADR 0024 seam did the work: the pose
is one extra Location in `_mate_placement`, validation lives in `mate()`, and
range checking is a warning pass after resolution. The evaluator gained zero
code — the flagship test drives a revolute pose from `params` through an ADR
0020 `parametric` sweep with an `interference` sub-check, and the aggregate
correctly reports the 0° set passing and the 90° set failing with the
colliding pair named. Rigid-mate spatial records stayed byte-identical (ADR
0024's pinned equality tests pass unchanged) because pose keys are only
written for kinematic kinds.

Verified end-to-end beyond the suite on a post/wall/arm demo rendered at two
poses: at 0° the arm is clear; at 90° the shaded render shows it passing
through the wall, and the run emitted ``mate_out_of_range: angle 90 outside
declared angle_range [0, 75]`` while still placing the arm honestly at 90°.
Full suite `131 passed` (124 prior + 7 new), no regressions.

Deferred as decided: native ``RevoluteJoint``/``angular_range`` consumption,
ball joints, and constraint solving. One observation for a future ADR: the
`parametric` sweep re-runs the whole design per pose (~seconds each); a
mechanism with fine-grained sweeps would benefit from a pose-only re-place
path that skips rebuilding unchanged geometry.

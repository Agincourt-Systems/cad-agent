# ADR 0024: Joint-Driven Placement (Declarative Mates)

## Status

Accepted for implementation.

## Context

ADR 0014 established the assembly frame: `publish(label, obj,
placement=Location(...))` positions each part, and every derived fact
(bboxes, exports, cross-part checks, ADR 0023's combined assembly) observes
the placed geometry. But the *author* must hand-compute each `Location` in
global coordinates. For a bolted bracket that means mentally composing "the
base's top face is at z=3, the bracket's mounting face is 15 below its
centroid, so placement is (20, 5, 18)" — exactly the arithmetic that produces
the misaligned-bolt-pattern failures the checks exist to catch. Mating intent
("this face sits on that face, these holes coaxial") is stated nowhere; only
its numeric consequence is.

build123d ships a joint system (`RigidJoint`, `connect_to`) that solves this
for interactive use, but `connect_to` *mutates* the child shape's location,
leaving the harness with no placement record and no declared relationship in
`spatial.json`. A kernel probe (2026-07-03) confirmed the underlying math is
simple and stable: for joints ``anchor`` (on the child) and ``target`` (on
the parent), ``target * anchor.inverse()`` reproduces ``connect_to``'s
placement exactly, `Shape.joints`/`Joint.relative_location` are public API,
and `Location` composition/inverse behave as expected.

## Decision

### One primitive: a rigid mate that resolves to a placement

A new authoring helper, exported from `cadx`:

```python
mate(to, *, anchor=None, target=None, joint=None, target_joint=None)
```

declares that this part's ``anchor`` frame coincides with the ``target``
frame on the already-published part ``to``. `publish` gains a ``mate=``
keyword, mutually exclusive with ``placement=`` (both supplied raises
`ValueError` at design time — an immediate, attributable authoring error).

Two spellings of the same primitive:

- **Explicit frames**: ``anchor``/``target`` are build123d `Location`s (or
  bare 3-sequences for pure translation). The child's placement is
  ``parent_placement * target * anchor⁻¹``.
- **Native joints**: ``joint``/``target_joint`` name `RigidJoint`s created on
  the shapes by the design; the resolver reads their ``relative_location``s
  as the anchor/target frames. ``target_joint`` defaults to ``joint``. This
  consumes build123d's joint vocabulary without adopting ``connect_to``'s
  mutate-in-place semantics.

Crucially, the mate **resolves to the existing ``placement`` field** before
normalization and export. Placement remains the single source of truth;
every downstream stage — spatial facts, STEP/STL/GLB/DXF exports, clearance
/ interference / alignment checks, assembly center of mass, and the ADR 0023
combined assembly — works unchanged. The declared relationship is recorded
on the spatial object as an additive ``mate`` key (``{"to", "joint",
"target_joint"}``, JSON-safe fields only) so agents can see the intent, not
just the resolved transform.

### Chains, order, and failure modes

- Mates may chain (sensor → bracket → base). Resolution iterates to a
  fixpoint: a mate resolves once its parent is placement-final, so publish
  order does not matter.
- A mate naming an unpublished label degrades to a ``mate_unresolved``
  warning; the part stays unplaced and the run stays ``ok`` (matching the
  house graceful-degradation rule). Mate cycles leave every member unplaced
  with a warning naming the cycle's labels. A resolution failure (e.g. a
  missing joint name) becomes a ``mate_failed`` warning.
- Synthetic dict publications support the translation-only path:
  anchors/targets as 3-sequences, positions composed with plain arithmetic —
  so the evaluator/CLI test tier stays kernel-free, exactly like ADR 0014's
  synthetic placements.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_synthetic_mate_translates_child_bbox`: a dict-published child mated
  ``anchor=[0,0,0] → target=[30,0,10]`` lands its bbox at the target with a
  recorded ``placement`` and ``mate`` on the spatial object. Fails today:
  ``publish`` rejects the ``mate`` keyword.
- `test_mate_chain_resolves_regardless_of_publish_order`: C→B→A published in
  reverse order still composes positions ([0,0,20] for C).
- `test_unknown_mate_target_warns`: mate to a ghost label yields a
  ``mate_unresolved`` warning, an unplaced part, and run status ``ok``.
- `test_mate_cycle_warns`: A↔B leaves both unplaced with warnings, run ``ok``.
- `test_mate_with_placement_is_an_authoring_error`: supplying both fails the
  run with a message naming the conflict (not a generic TypeError).
- `test_location_mate_places_real_part` (kernel): the probe geometry — tower
  ``anchor=(0,0,-15)`` onto base ``target=(20,5,3)`` — lands at placement
  position (20,5,18) with bbox top at z=33; a rotated mate (target
  orientation (0,90,0)) swaps the fin's bbox extents; the ADR 0023 combined
  assembly export includes the mated parts.
- `test_native_joint_mate_matches_connect_to` (kernel): `RigidJoint`-spelled
  mates land where ``connect_to`` would (verified value from the probe).
- Existing ADR 0001–0023 tests continue to pass unchanged.

## Consequences

- Authors (human or agent) state mating intent once, in local part
  coordinates, and the harness computes the global transform — eliminating
  the hand-composed-Location class of assembly errors while keeping
  `feature_alignment`/`interference` as independent verification that the
  intent actually holds in geometry.
- ``spatial.json`` now records *why* a part sits where it does (``mate``)
  alongside *where* (``placement``), giving agents a structural handle for
  edits ("re-mate the bracket to the second hole") instead of transform
  surgery.
- The single-primitive scope is deliberate: no kinematic joints (revolute,
  slider) and no constraint solving — a rigid mate covers the bolted-bracket
  assemblies this harness targets, and richer joint types can layer on the
  same resolve-to-placement seam in a future ADR if needed.

## After Action Report

All seven success-criteria tests were confirmed red before implementation and
green after, with no code changes needed between first green and final: the
pre-ADR kernel probe (``target * anchor.inverse()`` cross-checked against
``connect_to``) had already de-risked the one piece of nonobvious math, and
the resolve-to-placement seam meant no downstream stage needed touching —
`_placed_object`, exports, checks, and the ADR 0023 assembly all consumed the
derived placements unmodified.

The resolver gained one failure mode beyond the ADR's list during
implementation: a mate whose *parent* ended up unplaced (unknown target or
failed resolution upstream) now warns and stays unplaced rather than silently
mating against the origin — the silent-misplacement variant would have been
exactly the failure class this ADR exists to remove.

Verified end-to-end beyond the suite on a three-part demo (base + tower via
`RigidJoint` names, cap chained onto the tower via explicit frames): spatial
records show `mate` intent alongside resolved placements ((20,5,18) and
(20,5,35)), the cap's bottom lands exactly on the tower top through the
*derived* parent placement, and the shaded assembly render shows all three
parts seated correctly. Full suite `119 passed` (112 prior + 7 new), no
regressions. README gained an Assemblies section documenting both mate
spellings.

Deliberately deferred, unchanged from the Decision: kinematic joint types and
constraint solving; both can layer on the same resolve-to-placement seam.

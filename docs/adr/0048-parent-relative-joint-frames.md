# ADR 0048: Parent-Relative Joint Frames

## Status

Accepted for implementation.

## Context

Deficiency **D-018** (`docs/specs/arm-deficiencies.md`) is a papercut left by
ADR 0038. That ADR closed the big gap (D-013): the mate record now exports the
defining `anchor`/`target` frames, the joint `axis`, and the zero-pose `origin`
— but every one of those derived fields is expressed in the **world/assembly
frame**. URDF, the consumer these exports exist for, does not want world frames.
A URDF `<joint>` places its `<origin>` and `<axis>` in the **parent link's
frame**: the origin is the parent→child transform, and the axis is a direction
in the parent's coordinates.

The conversion is a one-liner — `parent_placement⁻¹ · world` for the origin, and
the inverse parent rotation applied to the axis — and ADR 0038 even documents it
in passing. But "derivable" is not "derived": every consumer must re-implement
that composition, re-read the parent's placement, and get the axis case right
(the axis is a *direction*, so only the rotation applies — translating it is a
classic bug). Emitting the parent-relative forms once, correctly, removes a
whole class of downstream errors, exactly as ADR 0038 did for the world forms.

Since ADR 0038 (D-014) every part carries a placement (roots an explicit
identity), so a parent frame always exists; there is never a missing-parent case
to special-case.

## Decision

`runner._mate_frame_export` gains two parent-relative siblings **alongside** the
existing world-frame fields (nothing is renamed or removed):

### 1. `origin_in_parent` — the parent→child zero-pose transform

The zero-pose `origin` already exported is the world transform
`parent_placement · target · anchor⁻¹`. Expressed in the parent link's frame it
is simply the world origin pre-multiplied by the inverse parent placement:

```
origin_in_parent = parent_placement⁻¹ · origin_world
```

serialized as a `{"position", "orientation"}` record in the exact style of
`placement` and `origin` (ADR 0014 intrinsic-XYZ-degrees). A URDF generator drops
this straight into `<joint><origin>`. The round trip is exact by construction:
`parent_placement · origin_in_parent == origin_world`.

### 2. `axis_in_parent` — the joint axis in the parent frame (kinematic kinds only)

The world `axis` already exported is `R_parent · R_target · ẑ`. Expressed in the
parent frame the parent rotation drops away:

```
axis_in_parent = R_parentᵀ · axis_world          (rotation only — NO translation)
```

An axis is a **direction**, so only the parent's rotation is inverted; its
translation must never touch the vector. `R_parent` is recovered from the parent
placement with the same build123d-derived rotation matrix helper ADR 0047
introduced (`_placement_rotation`), so the convention cannot drift. `axis_in_parent`
is emitted only for the kinematic kinds that carry an `axis` (`revolute`,
`prismatic`, `cylindrical`); a `rigid` (URDF fixed) mate has neither.

### 3. Root-parented mates: parent-relative == world

When the parent is a root at identity placement, `parent_placement⁻¹` is the
identity, so `origin_in_parent == origin_world` and `axis_in_parent == axis_world`.
This is not special-cased in code — the identity placement composes as a no-op —
but it is pinned by a test so the equivalence is a guaranteed contract.

### 4. Synthetic (kernel-free) mates

Synthetic dict publications carry translation-only frames. The parent placement
is then a pure translation, so `origin_in_parent = origin_world − parent_position`
(which equals `target − anchor`), and `axis_in_parent == axis_world == [0,0,1]`
(no rotation to remove). This mirrors the existing synthetic branch so the mate
machinery stays testable without a CAD kernel.

The parent-relative fields are computed only for mates that resolve
successfully, riding in the same `mate_export` block as the world fields; an
unresolved mate exports neither (unchanged from ADR 0038).

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_elbow_parent_relative_origin_and_axis` (kernel): a two-link elbow whose
  parent is published with a nontrivial rotation **and** translation
  (`Location((100,0,0),(0,0,90))`) and a Y-90 revolute target exports
  `origin_in_parent` at position `(0,0,10)` orientation `(0,90,0)` and
  `axis_in_parent == [1,0,0]` (hand-computed), while the world `axis` is
  `[0,1,0]` — proving the parent rotation was removed. Fails today:
  `origin_in_parent`/`axis_in_parent` absent.
- `test_parent_relative_origin_reconstructs_world` (kernel): rebuilding
  `parent_placement · origin_in_parent` from the exported parent placement and
  the exported `origin_in_parent` reproduces the exported world `origin` — the
  URDF-style round trip.
- `test_axis_in_parent_is_rotation_only` (kernel): `axis_in_parent` is a unit
  vector and equals `R_parentᵀ · axis_world`; the parent's translation does not
  shift it (an elbow with the same rotation but a different parent translation
  exports the identical `axis_in_parent`).
- `test_root_parented_mate_parent_equals_world` (kernel-free): a mate to a root
  parent at identity exports `origin_in_parent == origin` and
  `axis_in_parent == axis`.
- `test_synthetic_parent_relative_frames` (kernel-free): a synthetic prismatic
  mate to a translated parent exports `origin_in_parent` at `target − anchor` and
  `axis_in_parent == [0,0,1]`.
- Existing ADR 0038 mate-frame tests pass unchanged (the additive keys ride
  beside the world fields the presence-checking tests already tolerate).

## Consequences

- A URDF generator reads `<joint><origin>` from `origin_in_parent` and
  `<joint><axis>` from `axis_in_parent` with zero re-derivation, and the
  world forms remain for consumers that want the assembly frame.
- The axis rotation-only rule is enforced once, in cadx, removing the most common
  frame-conversion bug from every consumer.
- The mate record grows purely additively; no resolved placement, world frame, or
  check verdict moves. ADR 0038's reconstruction contract is unchanged and now has
  a parent-relative twin.

## After Action Report

_To be completed after implementation and one downstream consumption cycle._

# ADR 0049: Part-Frame Dimension Checks

## Status

Accepted for implementation.

## Context

Deficiency **D-025** (`docs/specs/arm-deficiencies.md`): a `dimension` check
resolves its target through `spatial.json`, and for size assertions that target
is `obj.<label>.bbox.size.<axis>` — a **world-frame axis-aligned bounding box**.
That AABB is measured on the *placed* geometry (ADR 0015 poses every part before
observing it), so for any rotated part it reports the bounding box of the
rotation, not of the part. A 60 mm square platform posed by a revolute joint at
45° measures `60·√2 ≈ 84.85 mm` on both planar axes. A designer who wants to
assert "this platform is 60 mm wide" therefore *cannot* — the only pose at which
the world AABB equals the part's true extent is the zero pose. Any design that
articulates a joint before checking a dimension is stuck.

The world AABB is the right default: interference, clearance, and packaging
reason in the assembly frame, and those must not change. What is missing is an
**opt-in** way to ask the same question in the part's own frame — the bounding
box with the placement transform removed.

## Decision

### 1. Record the part-frame bounding box (`bbox_local`)

`runner._normalize_published` already computes the world bbox from the *placed*
object (`_placed_object(entry)`). It now also records **`bbox_local`**: the
bounding box of the part **before** the placement transform is applied.

- **Real geometry:** `bbox_local` is the bounding box of the unplaced
  `entry["object"]` — the geometry exactly as authored, at identity placement.
  This is the robust path: build123d gives the true oriented extent of the
  authored solid directly, with no need to un-rotate a world AABB (un-rotating an
  axis-aligned box is *not* invertible — the world AABB has already lost the
  part's orientation, so it cannot be recovered from the world AABB alone). Any
  rotation baked into the *authored* geometry stays in `bbox_local`, because it
  is part of the part's own definition; only the *placement* (mate/`placement=`)
  transform is removed.
- **Synthetic dict publications:** `bbox_local` is the record's original `bbox`,
  captured **before** `_translate_bbox` shifts it into the assembly frame.

`bbox_local` is emitted whenever a `bbox` is (additive; nothing else changes).
For an identity-placed part `bbox_local` equals `bbox` exactly.

### 2. Opt-in `frame` on dimension checks

`dimension` checks accept a new optional `frame` field:

- `frame: "world"` (the default, and the value for every existing check) —
  byte-identical behavior: the target resolves against `bbox` as before.
- `frame: "part"` — the check resolves against `bbox_local` instead. Concretely,
  the resolver rewrites the `bbox` segment of the target path to `bbox_local`, so
  `obj.platform.bbox.size.x` is measured on `bbox_local.size.x`. Everything else
  about the check (tolerance, equals/min/max clauses, output shape) is unchanged.
- Any other `frame` value fails that one check with a descriptive error
  (`unsupported frame ...`), matching the evaluator's graceful-degradation
  contract (a bad selector fails one check, never aborts the run). Silence — a
  typo'd frame quietly measured in world — is the exact failure class this ADR
  removes, so it must be loud.

The default is chosen so no existing requirements file changes verdict; `frame`
is purely additive to the check schema.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_world_frame_dimension_fails_on_posed_platform` (kernel): a 60 mm platform
  posed by a revolute joint at 45° has `obj.platform.bbox.size.x ≈ 84.85`, so a
  `dimension equals 60 tolerance 1` check **fails** (the status-quo trap,
  regression-pinned).
- `test_part_frame_dimension_passes_on_posed_platform` (kernel): the same check
  with `frame: part` reads `60.0` and **passes**. Fails today: `bbox_local`
  absent, so the target cannot resolve.
- `test_part_and_world_frames_agree_at_identity` (kernel): an unplaced/identity
  part gives an identical observed value in both frames.
- `test_unknown_frame_is_a_clear_error` (kernel-free): `frame: diagonal` fails
  the check with an error naming the bad frame, not silently.
- `test_bbox_local_recorded_for_synthetic` (kernel-free): a translated synthetic
  publication records `bbox_local` equal to its authored (untranslated) bbox,
  while `bbox` is the translated world box.
- Existing `dimension`/`topology` checks and all ADR 0001–0048 tests pass
  unchanged (default `frame: world`).

## Consequences

- A design can assert a part's true dimensions at any pose by adding
  `frame: part`, closing D-025, while world-frame checks (the default) and every
  assembly-frame check keep their exact behavior.
- `bbox_local` is one extra bounding-box evaluation per part at publish time
  (real geometry) or a dict copy (synthetic) — negligible, and it makes the
  part's own extent a first-class, machine-readable fact for any future consumer.
- The change is confined to `runner._normalize_published` (the field) and
  `evaluate._check_scalar_target` (the opt-in); the inspector, which copies the
  published record verbatim into `spatial.json`, carries `bbox_local` through
  with no edit.

## After Action Report

_To be completed after implementation and one downstream consumption cycle._

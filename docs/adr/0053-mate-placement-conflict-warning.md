# ADR 0053: Warn When a Mate Discards a Part's Own Placement

## Status

Accepted for implementation.

## Context

Deficiency **D-028** (`docs/specs/arm-deficiencies.md`): a part can be built with
its own transform baked into the shape — the common spelling is
`Pos(x, y, z) * Box(...)`, which sets the build123d object's `.location` to a
non-identity value — and *also* published with a `mate=`. When the mate resolves,
the runner stores the mate placement on the entry and later moves the shape with
`obj.located(placement)`. build123d `located()` sets the shape's location
**absolutely**: it replaces whatever `.location` the shape already carried. So the
part's own `Pos(...)` transform is silently thrown away and the part lands where
the mate says, not where the author's own transform plus the mate would put it.

Nothing today records that this happened. The author sees a part in an unexpected
place with no warning, no error, and no clue that two transforms competed and one
was dropped.

### Design constraint

The downstream has shipping designs that rely on the current
"mate wins, own placement is discarded" semantics. We must **not** change the
positioning behavior by default. The complete, safe fix is therefore a structured
**diagnostics warning**, following the `material_unresolved` pattern from ADR 0045
(a dict with `type`/`label`/`message`, folded into the run's `warnings` channel by
the worker). Composition of the two transforms is explicitly out of scope for this
ADR; if it is ever offered it must be strictly opt-in.

### The detection question: "non-identity own placement"

The runner poses a part in two independent ways:

1. **`publish(..., placement=...)`** — recorded on the entry as
   `entry["placement"]`. `publish()` already forbids `placement=` together with
   `mate=` (they are mutually exclusive), so this channel never collides with a
   mate.
2. **A transform baked into the shape itself** — `Pos(...) * shape`,
   `shape.move(...)`, `shape.located(...)`, etc. This lives on the build123d
   object as `shape.location` and is *invisible* to `publish()`. This is the
   channel D-028 is about, and the only one that can silently collide with a mate.

The detection rule is therefore: **a mated part whose build123d object carries a
non-identity `.location`.** "Non-identity" means any component of the location's
position or orientation exceeds a small tolerance (`1e-9`); build123d spells the
identity orientation as `(-0.0, 0.0, -0.0)`, so the check works on absolute
values. Synthetic (kernel-free dict) publications have no `.location` and no own
transform concept, so they never trigger the warning.

This rule catches the exact case in the deficiency (`Pos(...)` before the mate).
It does not catch a transform folded into vertex coordinates while `.location`
stays identity (e.g. geometry authored directly at an offset), because such a
shape has no *separable* own placement to discard — the mate does not throw
anything away, so there is nothing to warn about. The rule is thus both sound
(no false positives) and targeted at the reported failure.

## Decision

`runner._resolve_mates` — the one pass that turns a mate spec into
`entry["placement"]` and therefore the point at which the shape's own location is
about to be superseded — gains one check. Immediately after a mate resolves
successfully for a **real** (non-dict) object, if that object's `.location` is
non-identity, append a warning:

```python
{
    "type": "placement_overridden_by_mate",
    "label": <part label>,
    "message": "<label> carries its own placement (Pos/move/located) which the "
               "mate on <to> discards; the part is posed by the mate only",
}
```

A small helper `_own_placement_is_nonidentity(obj)` reads `obj.location`,
tolerantly compares its position and orientation components against zero, and
returns `False` for any object without a readable `.location` (dicts, kernel-free
stubs, anything unexpected) so the check is total and never raises.

The warning is emitted only on the success path (a mate that fails to resolve is
already reported as `mate_failed` and the shape keeps its own placement, so there
is nothing overridden). Positioning is unchanged: the part is still posed exactly
where the mate resolves it. The warning does not change the run status.

## Alternatives considered

- **Compose the two transforms** (`located(placement) → moved(own)` or similar).
  Rejected as the default: it changes shipping geometry, which the design
  constraint forbids. Left as possible future opt-in work.
- **Raise an error.** Rejected: too strong for a MINOR, and it would break every
  shipping design that already relies on mate-wins semantics.
- **Detect via `entry["placement"]` instead of the shape location.** Impossible:
  `placement=` and `mate=` are already mutually exclusive at publish time, so the
  colliding transform is never on `entry["placement"]` — it is only ever baked
  into the shape.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_own_placement_nonidentity_helper` (kernel-free): `_own_placement_is_nonidentity`
  returns `True` for a stub whose `.location` has a non-zero position or
  orientation, and `False` for an identity location, a `None` location, and a
  bare object. Fails today: the helper does not exist.
- `test_mate_overriding_own_placement_warns` (kernel): a part published as
  `Pos(50, 0, 0) * Box(...)` **and** mated to a base emits exactly one
  `placement_overridden_by_mate` warning naming that part; the run still succeeds.
  Fails today: no such warning type exists.
- `test_mate_without_own_placement_is_quiet` (kernel): a part published as a plain
  `Box(...)` (identity `.location`) with the same mate emits **no**
  `placement_overridden_by_mate` warning. Guards against false positives.
- Existing mate/joint tests pass unchanged (the check is additive and only fires
  in the new case).

## Consequences

- An author who accidentally combines an own transform with a mate now gets a
  machine-visible, greppable warning instead of a silently misplaced part.
- Positioning semantics are untouched; every shipping design behaves exactly as
  before. Consumers that gate on `warnings` gain a new signal; consumers that do
  not are unaffected because the run status does not change.
- The warning is emitted once per offending part, on the mate-resolution success
  path, alongside the existing `mate_out_of_range` and `mate_failed` records.

## After Action Report

_Pending downstream verification._

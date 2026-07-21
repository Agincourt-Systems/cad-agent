# ADR 0041: Placement / Mate Passthrough on `publish_sheet_metal` (D-020)

## Status

Accepted for implementation.

## Context

Deficiency **D-020** (`docs/specs/arm-deficiencies.md`, MAJOR) observes that
`publish_sheet_metal` accepts **neither `placement` nor `mate`**. Its published
record is hard-coded to `"placement": None`, so a folded sheet part is always
identity-placed and can never be a revolute-mate child through the public API.

This is not an edge case. A **clevis** — the folded part at every pitch joint of
the arm this harness targets — *is* a bent sheet part that must hang off its
parent through a revolute mate. Plain `publish()` has taken `placement=` and
`mate=` since ADR 0024, and the runner already resolves mates and normalizes
placements uniformly (ADR 0038 semantics). But because the sheet-metal entry
point drops both arguments, downstream teams were forced to reach in and **mutate
registry dicts** by hand to seat a folded part — brittle, and exactly the kind of
private-API dependence the public contract exists to prevent.

The runner needs nothing new: `_resolve_mates`, `_mate_placement`,
`_normalize_published`, and `snapshot_registry` all already read a `placement`
and a `mate` off *any* published entry (the sheet entry's `object` is a real
build123d solid — `part.folded` — so revolute resolution over real geometry works
identically to a plain part). The only gap is that `publish_sheet_metal` never
puts those keys on the entry.

## Decision

Give `publish_sheet_metal(label, part, *, layer, role, placement=None,
mate=None, **metadata)` the **same** `placement` / `mate` contract as `publish()`,
and record them on the published entry so the runner treats a sheet part
identically to a plain part:

- Mirror `publish()`'s mutual-exclusion guard exactly: `placement is not None and
  mate is not None` raises `ValueError("publish_sheet_metal() accepts either
  placement or mate, not both")` — a part cannot carry both a hand-computed and a
  derived transform.
- Set `"placement": placement` on the entry (replacing the hard-coded `None`), and
  attach `"mate": dict(mate)` when a mate is given — the same shape `publish()`
  produces, which `snapshot_registry` already copies through for any entry and
  `_resolve_mates` already resolves.
- The internal `"flat"` key (flat profile, bend lines, bend table) and the emitted
  `kind="bend"` / `kind="cylindrical_hole"` features (ADR 0033 / 0040) are
  **unchanged**. They live in the **flat-pattern frame**, which is a press-brake /
  laser quantity independent of where the folded part is assembled. Placement
  moves only the folded solid (its bounding box, mass properties, and 3-D
  exports); the flat DXF, `bends.json`, and the bend/hole spatial features stay in
  flat coordinates. This is the correct separation — a bend line does not move
  because the part was mated into an assembly — and it is pinned by a test.

### Frame consistency with plain publish

Because the sheet entry's `object` is `part.folded` (a real solid) and the
placement/mate keys are recorded in the same shape as `publish()`, the resolved
placement of a folded part mated to a parent is **identical** to the resolved
placement of that same solid published plainly with the same mate. The success
criteria assert this equality directly, so the passthrough cannot silently
diverge from the plain path.

### Default behavior unchanged

With neither `placement` nor `mate` supplied, the entry's `placement` is `None`
exactly as before, and no `mate` key is attached — so `_normalize_published`
seats the part at the ADR 0038 identity (`{position:[0,0,0],
orientation:[0,0,0]}`) just as it did pre-ADR. A run that publishes sheet parts
without placement is byte-identical to before this ADR.

## Success Criteria

Written so the new tests in `tests/test_sheet_metal_placement.py` fail before and
pass after implementation:

- `test_sheet_metal_revolute_mate_matches_plain`: a base part plus the *same*
  folded solid published twice — once via `publish(..., mate=revolute)` and once
  via `publish_sheet_metal(..., mate=revolute)` with identical anchor/target/angle
  — resolve to the **same** placement (position and orientation) in
  `spatial.json`. Before: `publish_sheet_metal` rejects the `mate` keyword
  (`TypeError`). After: equal placements.
- `test_sheet_metal_placement_only_lands_where_placed`: a sheet part published
  with `placement=Location((5, 10, 2))` reports that translation in
  `spatial.json`.
- `test_placement_leaves_flat_frame_unchanged`: a placed sheet part's
  `bends.json` line, its `kind="bend"` feature `line`, and its DXF bend-layer
  entity x-coordinate are all the flat-pattern values (equal to the un-placed
  run), proving placement does not leak into the flat pattern.
- `test_sheet_metal_default_placement_is_identity`: `publish_sheet_metal(label,
  part)` with neither argument records the identity placement and no `mate` key —
  unchanged from before.
- `test_sheet_metal_placement_and_mate_conflict`: supplying both raises
  `ValueError`, mirroring `publish()`.
- Existing sheet-metal / DXF / assembly tests continue to pass.

## Consequences

- A folded sheet part (a clevis) is now a first-class placed / mated part through
  the public API; the registry-dict mutation workaround is retired.
- The sheet and plain publish paths share one placement/mate contract, so they
  cannot drift.
- The flat pattern, bend table, and bend/hole features remain in flat-pattern
  coordinates regardless of assembly placement — the fabrication truth is
  independent of the assembled pose.

## After Action Report

The red state confirmed the deficiency: with `publish_sheet_metal` taking only
`**metadata`, a `mate=` / `placement=` keyword was silently swallowed into
metadata, never reaching the entry's `mate` / `placement` keys — so the mated
sheet child stayed identity-placed (its resolved position diverged from the plain
child), the placement-only part did not move, and the conflict guard did not
fire. Four of the five tests failed for exactly those reasons; the fifth
(default-identity) passed before and after as the byte-identical baseline guard.

The fix needed nothing in the runner: recording `placement` / `mate` on the entry
in the same shape `publish()` produces was sufficient — `_resolve_mates`,
`_mate_placement`, `_normalize_published`, and `snapshot_registry` already handle
any entry generically, and the sheet entry's `object` is the real `part.folded`
solid, so revolute resolution over real geometry matched the plain path to
floating-point tolerance. The mated folded child and the same solid published
plainly resolve to identical position and orientation.

`test_placement_leaves_flat_frame_unchanged` pins the separation: with the folded
part placed at `(100, 50, 20)`, the `bends.json` line, the `kind="bend"` feature
`line`, and the DXF bend-layer entity x-coordinate are all the flat-pattern
developed x (`FLANGE_A + BA/2`), identical to the un-placed run — placement never
leaks into the flat pattern. The full sheet-metal, DXF, and kinematic-joint
suites pass unchanged.

# ADR 0021: Assembly Mass Aggregation and Graceful Target Resolution

## Status

Accepted for implementation.

## Context

The holistic end-to-end acceptance review of the sheet-metal track (ADRs
0013–0020, deficiencies D1–D9) found two robustness issues that the per-ADR
reviews missed because they only surface in a realistic multi-part assembly:

1. **Assembly center of mass dropped physical parts.**
   `inspector._assembly_center_of_mass` (ADR 0015) aggregated only objects with
   `role == "part"`. But the idiomatic convention across the codebase publishes
   the primary/base part with `role == "final"` — the `cadx init` starter design,
   `runner`'s auto-publish, and most tests do. So in an assembly authored that way
   the base plate is silently excluded from the aggregate center of mass, and the
   `center_of_mass`/`stability` checks (which consume the assembly aggregate) can
   pass on a center that omits the heaviest part. The acceptance smoke reproduced
   this: a 3-part assembly reported `part_count: 2` and an assembly CoM `z ≈ 32`
   versus the true `z ≈ 18` when the base was published as `role="final"`. This
   directly undermines D5's stated motivation — locating a load cell under the
   assembly center of gravity.

2. **A mistyped requirement target crashed the whole evaluation.** A `dimension`
   or `topology` check with a malformed target such as `obj.plate.solids`
   (missing the `.topology` segment) raised an uncaught `KeyError` from
   `_resolve_dimension`, aborting the entire `evaluate` run with a non-zero exit
   and stack trace — unlike the assembly checks (`feature_alignment`,
   `interference`), which return a descriptive failed-check record on a bad
   selector. A mistyped target is a common authoring error and should fail one
   check, not the command.

## Decision

1. `_assembly_center_of_mass` aggregates every **physical** part. The exclusion
   becomes a denylist of non-physical roles
   (`{"fixture", "reference", "datum", "keepout"}`) rather than an allowlist of
   `"part"`. So `"part"`, `"final"`, and any future physical role contribute,
   while reference/keep-out geometry does not. Per-part center of mass is
   unaffected (it was always role-independent).

2. `dimension` and `topology` checks resolve their target through a new
   `_resolve_dimension_or_error` helper that catches resolution errors
   (`KeyError`/`ValueError`/`IndexError`/`TypeError`) and returns a graceful
   `status: "fail"` record carrying a descriptive `error`, matching the assembly
   checks' behavior on bad selectors.

Both changes are additive and behavior-preserving for valid inputs.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_assembly_com_includes_final_role_and_excludes_fixtures`: an assembly with
  a `role="final"` base, a `role="part"` plate, and a `role="fixture"` jig reports
  `part_count == 2` and a centroid that is the midpoint of base+plate (the fixture
  excluded). Fails today: the `final` base is dropped, giving `part_count == 1`.
- `test_dimension_bad_target_fails_gracefully`: a `dimension` check with a mistyped
  target returns `status: "fail"` with an `error` and a zero exit, rather than
  crashing `evaluate`. Fails today: `evaluate` exits non-zero with a `KeyError`.
- Existing ADR 0001–0020 tests continue to pass (per-part CoM, the role-`part`
  assembly aggregation tests, and the valid dimension/topology checks are
  unchanged).

## Consequences

- The assembly center of mass and the `stability`/`center_of_mass` checks now
  reflect every physical part regardless of whether it was published as `"part"`
  or `"final"`, so a load-cell/tip-over check is trustworthy for assemblies
  authored either way. Fixtures and reference geometry are correctly excluded.
- `evaluate` is robust to malformed `dimension`/`topology` targets: one check
  fails with a clear message instead of the whole run aborting, which is friendlier
  for an autonomous agent iterating on requirements.

## After Action Report

The red state was confirmed directly: a `role="final"` base produced
`part_count: 1` with the base dropped, and the mistyped-target evaluate exited
non-zero with `KeyError: 'solids'`. After the fix the assembly aggregates base +
plate (excluding the fixture) for `part_count: 2` and a centroid at the midpoint,
and the mistyped target yields a graceful failed check. The role exclusion uses a
denylist so future physical roles contribute by default, and the dimension/topology
resolver mirrors the assembly checks' descriptive-error pattern. The full suite
passed with 98 tests (96 prior + 2 new), no regressions. This closes the two
should-fix items from the final acceptance review, leaving the D1–D9 sheet-metal
track at a clean "go".

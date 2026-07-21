# ADR 0039: Enforceable Joint Limits in the Motion-Envelope Sweep

## Status

Accepted for implementation.

## Context

Deficiency D-009 (`docs/specs/arm-deficiencies.md`, MINOR): a mate driven
outside its declared `angle_range` / `travel_range` emits only a
`mate_out_of_range` warning (ADR 0025, `_pose_range_warnings` in runner.py). A
warning never fails a check, so declared joint limits are **unenforceable in a
gate**. A downstream consumer that wants "fail if any joint is commanded past
its stop" has to scrape `diagnostics.json` warnings in its own gate script — the
exact workaround D-009 records ("treat warnings as failures in our gate
scripts").

The motion envelope is already verified by ADR 0025's pattern: an ADR 0020
`parametric` check sweeps a pose parameter (e.g. `arm_angle`) and runs
`interference` / `clearance` sub-checks per set, passing only when every swept
pose passes. Each swept set re-runs the design into its own run directory whose
`diagnostics.json` records that pose's `mate_out_of_range` warnings, if any.
So the sweep already *produces* the limit-violation facts per pose; it just
never lets them fail the aggregate.

## Decision

### `fail_on_range_violation` on the `parametric` check

The `parametric` check gains an opt-in boolean option
`fail_on_range_violation` (default `false`). This is the natural configuration
point because the `parametric` sweep *is* the motion-envelope verifier (ADR
0025): a machine caller enforcing a range of motion already declares the sweep
there, and per-joint limits are a property of that same swept motion. It is a
declarative requirement (lives in `requirements.yaml`), so it is per-check and
composes with the existing sub-checks, rather than a global CLI flag that would
apply indiscriminately to every run.

When `fail_on_range_violation: true`, each swept parameter set additionally
inspects its own run's `diagnostics.json` for `mate_out_of_range` warnings.
Any such warning fails that set — recorded as `range_violations` on the set,
carrying each warning's mate `label` and its message (which already names the
pose value and the declared range, e.g. `travel 30 outside declared
travel_range [0, 20] on 'slider'`). Because the aggregate `parametric` check
passes only when every set passes, one out-of-range pose fails the whole check
with a message naming the offending mate and value.

When the option is absent or `false`, behavior is byte-for-byte unchanged:
`mate_out_of_range` stays a warning, the set passes on its sub-checks alone, and
the geometry is still placed honestly at the requested pose (ADR 0025). This
preserves backward compatibility for every existing sweep.

A set whose design run failed outright (`run_status != "ok"`) is already a
failed set; the range inspection only applies to sets that ran successfully and
therefore could emit the warning.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_range_violation_warns_but_passes_by_default` (kernel-free): a prismatic
  mate with `travel_range=(0, 20)` swept to `travel=30` in a `parametric` check
  *without* the option. The aggregate **passes** (its sub-check passes), and a
  direct run at the out-of-range pose confirms the `mate_out_of_range` warning
  is present — pinning the unchanged warn-only default.
- `test_fail_on_range_violation_fails_and_names_mate` (kernel-free): the same
  sweep with `fail_on_range_violation: true`. The aggregate **fails**; the
  in-range set passes and the out-of-range set fails with a `range_violations`
  entry naming the mate (`slider`), the value (`30`), and the declared range.
- Existing ADR 0001–0038 tests pass unchanged (the option defaults off).

## Consequences

- A machine caller enforces per-joint range of motion in the same
  `parametric` sweep it already uses for interference, with one boolean —
  closing D-009's "warnings never fail" gap without a new check type, CLI
  surface, or schema version bump.
- The failure is self-describing: `range_violations` names the mate and the
  violating pose, so a gate report points straight at the joint that overshot.
- Deliberately unchanged and still deferred (D-009's second half): continuous /
  swept-volume interference between samples. This ADR enforces *declared limits*
  at the swept poses; collision strictly between sampled angles remains a
  sampling-density choice for the caller, as ADR 0025 noted.

## After Action Report

Both success-criteria tests were red/green as designed: the default-off test was
already green (proving the warn-only default is untouched — the opt-in adds no
behavior when absent), and the enforcement test was red before and green after.
The implementation is a single opt-in read: a `_range_violations` helper that
scans a swept set's `diagnostics.json` for the `mate_out_of_range` warnings ADR
0025 already emits, plus one `and not range_violations` term in the set verdict
and a `range_violations` field on the failing set. No new check type, CLI flag,
or schema version — the `parametric` sweep the caller already writes for the
motion envelope now enforces per-joint limits with one boolean.

Design confirmed: the sweep re-runs the design per param set, so each set's run
directory carries that pose's warnings; reading them there is exactly where the
limit facts live. The failing set's `range_violations` reuses ADR 0025's warning
message verbatim (`travel 30 outside declared travel_range [0, 20] on
'slider'`), so the gate report names the mate and the violating value without
new formatting. Full suite: **173 passed** (171 prior + 2 new), no regressions.

Deferred as decided (D-009's second half): swept-volume / between-sample
interference remains a sampling-density choice for the caller. This ADR closes
only the "declared limits never fail a gate" half, which was the enforceable
part.

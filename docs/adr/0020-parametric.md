# ADR 0020: Parametric Sweeps and Documented Check Types

## Status

Accepted for implementation.

## Context

Deficiency D8 in `docs/ca-sheet-metal-fixes.md` observes that the specification's
Requirement Schema lists `symmetry`, `visual`, `parametric`, and
`manufacturability` check types, but the evaluator routes only the original five
(`manufacturability` lands in ADR 0018). The most useful of the missing types is
`parametric` — running the same checks across multiple parameter sets — which is
exactly what a tolerance or stack-up study on a test stand needs. The remaining
gaps are a documentation problem: a design author has no list of which check types
are actually supported and is surprised by a raised error.

## Decision

Implement `parametric` and document the supported check surface.

- A `parametric` check re-runs the design under each parameter set and aggregates
  ordinary spatial sub-checks. `evaluate._check_parametric` re-runs the run's own
  `source_snapshot.py` (so the swept geometry matches the evaluated run) via the
  existing `runner.run_design` into `<run_dir>/sweeps/<check id>/NNNN`, evaluates
  the listed sub-checks against each set's `spatial.json`, and passes only when
  every set passes. The sweep directory is cleared each evaluation so re-running
  is idempotent. The record is `{id, type:"parametric", status, passed, total,
  sets:[{params, status, checks:[...sub-records...], run_status?}]}`; a set whose
  re-run failed carries `run_status` and no sub-checks instead of crashing the
  evaluation.

  ```yaml
  - id: width_stackup
    type: parametric
    params: [{width: 38}, {width: 42}]
    checks:
      - {id: w, type: dimension, target: obj.plate.bbox.size.x, min: 36, max: 44}
  ```

- Because the per-set runs need a timeout, an additive `timeout=30.0` keyword is
  threaded through `_evaluate_check` and `evaluate_run` (every existing caller
  uses the old positional arity, so none break). A new `evaluate.sweep_run` and a
  `cadx sweep <run_dir>` subcommand evaluate only the parametric checks for a
  focused view; `cadx evaluate` also gains an optional `--timeout-seconds`.

- The README gains a "Requirement check types" section listing every supported
  type and naming `symmetry`/`visual` as not yet implemented. Unknown types
  continue to raise a `ValueError` naming the type — the documented failure mode.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_parametric_sweep_all_sets_pass`: two in-range parameter sets both pass and
  aggregate to `passed == total == 2`, each set recording its params and sub-check
  observations. Fails today: `parametric` raises.
- `test_parametric_sweep_out_of_range_fails`: an out-of-range set fails the
  aggregate (`passed == 1`, `total == 2`).
- `test_sweep_subcommand`: `cadx sweep` returns the parametric verdicts. Fails
  today: no `sweep` subcommand.
- `test_unknown_check_type_errors`: an unknown type exits non-zero with the type
  name in stderr (the unchanged failure mode).
- `test_readme_lists_supported_check_types`: the README lists the supported types
  and names `symmetry`/`visual` as unsupported.
- Existing ADR 0001–0019 tests continue to pass; the signature changes are
  additive keywords and the `sweeps/` tree is additive.

## Consequences

- An agent can run tolerance/stack-up studies declaratively, and `checks.json`
  carries a full per-set record an agent can reason from.
- `sweeps/<id>/NNNN` holds one full run artifact set per parameter set, cleared
  and regenerated each evaluation; it never collides with the top-level
  `artifacts/runs` sequence.
- The supported check-type surface is now documented, so an author knows what is
  available and that `symmetry`/`visual` are not yet implemented.

## After Action Report

The red state failed as predicted: `parametric` raised
`unsupported check type`, `cadx sweep` was an unrecognized subcommand, and the
README listed no check types.

An adversarial review of the diff confirmed the load-bearing properties and found
no blockers:

- **Signature back-compat:** every existing caller of `_evaluate_check`
  (9 + 3 in the assembly/CoM tests) and `evaluate_run` (`loop.py`) uses the old
  positional arity and is unaffected by the additive `timeout` keyword.
- **Timeout threads end-to-end:** a 1-second timeout against a sleeping design
  records `run_status: "timeout"` per set without crashing — confirming
  `evaluate_run → _evaluate_check → _check_parametric → _run_param_set →
  run_design`.
- **Determinism:** re-running `cadx evaluate` produces a byte-identical
  `checks.json` (the sweep dir is cleared so nested run ids restart at `0001`).
- **`report.md` renders** for a failing parametric check (the generic failed-check
  block tolerates the absent `observed`/`expected`/`tolerance`).
- **Edge cases handled:** empty `params` aggregates to `fail` (not a vacuous
  pass), a nested parametric sub-check terminates, and `cadx sweep` over a file
  with no parametric checks returns `pass` with an empty `sweeps` list.

The failed-set `run_status` branch is covered by
`test_parametric_set_run_failure_is_recorded` (a parameter set missing `width`
makes the design raise). The three NITs the review noted (stale `sweeps/` dirs
when switching requirement files on one run dir; the empty-`params`-fails vs
no-parametric-sweep-passes asymmetry; missing required keys raising `KeyError` as
every sibling evaluator does) are documented, harmless, and consistent with the
existing codebase. The full suite passed with 96 tests (90 prior + 6 new), no
regressions.

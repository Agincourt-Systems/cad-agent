# ADR 0022: Graceful Evaluation, Loop Timeout Passthrough, and Robust Artifact Ingestion

## Status

Accepted for implementation.

## Context

A full-repository review (2026-07-02) found three robustness defects. All three
share a failure mode the harness explicitly promises to avoid: a recoverable
per-item problem aborts an entire agent-facing command with a raw traceback
instead of degrading into a structured, descriptive record. ADR 0021 fixed this
for *unresolvable* `dimension`/`topology` targets; the review found the same
crash family survives in the value-handling code paths around that fix.

1. **The evaluator still crashes on plausible authoring errors.** All confirmed
   by direct reproduction against a synthetic `spatial.json`; each aborts the
   whole `cadx evaluate`, so `checks.json` and `report.md` are never written:

   - A `dimension`/`topology` target that resolves to a **non-scalar** — e.g.
     `obj.plate.bbox.size` (author forgot `.x`) — reaches `float(observed)` and
     raises `TypeError`. The ADR 0021 guard covers resolution only, not the
     numeric coercion after it.
   - A target that resolves to **`None`** — e.g. `topology.faces` on a
     publication whose object exposes no `faces()` selector — raises the same
     `TypeError`.
   - A `feature_dimension` check whose `property` is **absent from a matched
     feature** calls `float(None)` and crashes.
   - A `clearance` check with `method: exact` naming a label with **no STEP
     export** (synthetic publication, failed export, or typo) raises a bare
     `KeyError` — unlike `interference`, which already degrades gracefully in
     exactly this situation.
   - `_resolve_center_of_mass` catches only `(KeyError, ValueError)`, while
     path resolution can also raise `TypeError`/`IndexError` (e.g. the target
     `obj.plate.bbox.size.x.y` indexes into a float).

2. **`cadx loop` drops `--timeout-seconds` on the evaluate leg.**
   `loop_until_done` forwards `timeout_seconds` to `run_design` but calls
   `evaluate_run(run_dir, requirements)` without it, so a `parametric` check
   inside a loop silently reverts to the 30-second default.

3. **One unreadable STEP export aborts inspection and rendering.**
   `inspector._auto_detect_features` and `renderer._render_step_artifacts` call
   `import_step` with no per-export guard. A corrupt or truncated STEP file
   crashes `cadx inspect`/`cadx render`; worse, because the worker runs
   `inspect_run` inside its main try-block, it converts an otherwise-successful
   build into a `status: "error"` run. Everywhere else a bad artifact degrades
   to a warning; these ingestion points should match.

## Decision

1. Evaluator checks degrade instead of raising, matching the ADR 0021 pattern:

   - `dimension`/`topology` coerce the resolved value through a guarded helper;
     a non-numeric observation returns a `status: "fail"` record with a
     descriptive `error` naming the target and the observed value.
   - `feature_dimension` fails with a descriptive `error` when any selected
     feature lacks the property or carries a non-numeric value, reporting the
     raw observed list so the agent can see which feature is deficient.
   - Exact `clearance` verifies both labels have STEP exports (and that
     `diagnostics.json` is readable) before importing; a missing export yields
     a failed-check record carrying `method: "exact"` and an `error`.
   - `_resolve_center_of_mass` catches `(KeyError, ValueError, IndexError,
     TypeError)`, aligned with `_resolve_dimension_or_error`.

2. `loop_until_done` forwards its `timeout_seconds` to `evaluate_run`.

3. Artifact ingestion guards each STEP export individually:

   - `inspector._auto_detect_features` wraps per-export import/detection; a
     failing export contributes a `feature_detection_failed` warning instead of
     aborting. `inspect_run` surfaces collected warnings in `spatial.json`
     under a `warnings` key (only when non-empty; additive, schema-compatible).
   - `renderer._render_step_artifacts` wraps the import; on failure it returns
     no projected views plus a `render_step_failed` warning. `render_run`
     records warnings in `render_manifest.json` and still produces the contact
     sheet from `spatial.json`.

All changes are behavior-preserving for valid inputs: passing checks, healthy
exports, and default timeouts produce byte-identical records (modulo the new,
normally absent `warnings` keys).

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_dimension_non_scalar_target_fails_gracefully`: `target:
  obj.plate.bbox.size` yields `status: "fail"` with an `error`, and the other
  checks in the same file still evaluate. Fails today: `TypeError` aborts
  `evaluate_run`.
- `test_topology_none_value_fails_gracefully`: a topology count of `None`
  yields a failed check, not a crash.
- `test_feature_dimension_missing_property_fails_gracefully`: a matched
  feature without the requested property yields a failed check carrying an
  `error`. Fails today: `float(None)` raises.
- `test_exact_clearance_without_step_export_fails_gracefully`: `method: exact`
  between labels with no STEP exports yields a failed check with `method:
  "exact"` and an `error`. Fails today: bare `KeyError`.
- `test_center_of_mass_scalar_index_target_fails_gracefully`: target
  `obj.plate.bbox.size.x.y` yields a failed check. Fails today: `TypeError`.
- `test_loop_forwards_timeout_to_evaluate`: `loop_until_done` passes its
  `timeout_seconds` through to `evaluate_run`. Fails today: default `30.0`
  reaches the evaluator regardless of the flag.
- `test_inspect_survives_unreadable_step_export`: `inspect_run` over a run
  whose diagnostics reference a garbage `.step` file completes, writes
  `spatial.json`, and records a `feature_detection_failed` warning. Fails
  today: `import_step` raises out of `inspect_run`.
- `test_render_survives_unreadable_step_export`: `render_run` on the same run
  completes with the contact sheet written and a `render_step_failed` warning
  in the manifest. Fails today: `import_step` raises out of `render_run`.
- Existing ADR 0001–0021 tests continue to pass unchanged.

## Consequences

- `cadx evaluate` now upholds its documented contract for the whole check
  vocabulary: any single malformed check fails alone with a descriptive error
  while the rest of the evaluation, `checks.json`, and `report.md` survive.
  This is the difference between an autonomous agent reading a structured
  failure and an autonomous agent parsing a traceback.
- Parametric sweeps inside `cadx loop` honor the caller's timeout budget.
- A corrupt export degrades detection/rendering for that object only, and the
  worker no longer reports a successful build as a failed run because feature
  detection choked on one artifact. Agents see the cause in `spatial.json` /
  `render_manifest.json` warnings.

## After Action Report

All eight success-criteria tests were confirmed red before implementation
(`8 failed`), each for the predicted reason, with one instructive surprise: on
this OCCT/build123d version, `import_step` on a garbage file does **not**
raise — it prints an OCCT parse error to stdout and returns an *empty* shape.
The inspector then silently detected nothing (no crash, but no warning either)
and the renderer crashed much later at `project_to_viewport` ("Can't project
empty edge/wire"). The guards therefore treat an import that yields no faces as
the same failure as a raising import, so both OCCT behaviors degrade
identically to a warning.

Implementation notes: `_check_dimension` and `_check_topology` were byte-for-
byte identical apart from the hardcoded type string, and the fix would have had
to be applied twice, so they collapsed into one `_check_scalar_target` (the
record's `type` comes from the check, which the router already guarantees is
`dimension` or `topology`). Exact clearance failures route through one
`_exact_clearance_failure` builder mirroring `interference`'s degradation. The
loop fix is a one-line positional passthrough pinned by a stubbed-stage
plumbing test.

The full suite passed with `106 passed` (98 prior + 8 new), no regressions —
the ADR 0021 tests covering the resolution-side guard were untouched and still
pass alongside the new value-side guards. `checks.json` / `report.md` now
survive every malformed-check shape found in the 2026-07-02 review, and a
corrupt STEP export no longer converts a successful build into a failed run.

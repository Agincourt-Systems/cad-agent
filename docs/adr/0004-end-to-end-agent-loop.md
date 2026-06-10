# ADR 0004: End-to-End Agent Loop

## Status

Accepted for implementation.

## Context

ADRs 0001 through 0003 covered separate parts of the harness: the CLI contract,
real `build123d` execution and exports, and STEP-backed view rendering. Those
tests prove important slices, but an agent needs the full loop to work without
hand stitching artifacts together.

The missing behavior is an end-to-end run where a real CAD model fails a
requirement, the harness returns enough report context for the agent to make a
specific edit, the corrected model passes, and the two runs can be compared.

## Decision

Add an end-to-end integration test that drives:

1. `cadx run` on a real parameterized `build123d` model.
2. `cadx render` to produce projection and section artifacts.
3. `cadx evaluate` to produce `checks.json` and a human/agent-readable
   `report.md`.
4. A parameter correction and second `cadx run`.
5. A passing evaluation.
6. `cadx compare` between the failing and passing runs.

Extend evaluation so every evaluation writes `report.md` with failed checks and
artifact references. The report is not a replacement for JSON; it is a compact
agent-facing summary that links failures to spatial and visual artifacts.

## Success Criteria

- The E2E test fails before implementation because evaluation has no report
  artifact.
- The E2E test passes after implementation with a real `build123d` model.
- Failed reports include the failed check id, observed value, expected value,
  `spatial.json`, `checks.json`, `views/contact.png`, and
  `views/render_manifest.json` when those artifacts exist.
- Existing ADR 0001 through ADR 0003 tests continue to pass.

## Consequences

- Agents can use `evaluate` as the primary convergence feedback command after
  rendering.
- `report.md` creates a stable place for concise human-readable observations
  without bloating stdout.
- The JSON artifacts remain the source of truth for programmatic automation.

## After Action Report

The red-state E2E test failed after the first evaluation because `cadx evaluate`
did not return a `report_path` and did not write `report.md`. The implementation
added report generation while preserving `checks.json` as the programmatic
source of truth.

The final E2E flow uses a real parameterized `build123d` box, fails the first
width requirement, renders real projection and section artifacts, verifies that
the report references failed checks plus spatial and visual artifacts, fixes the
parameter, passes evaluation, and compares the two run bounding boxes. The
focused E2E test passed, and the full suite passed with 6 tests.

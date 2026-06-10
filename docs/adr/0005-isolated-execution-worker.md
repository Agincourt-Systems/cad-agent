# ADR 0005: Isolated Execution Worker

## Status

Accepted for implementation.

## Context

`cadx run` currently executes design Python in the harness process. That is
too fragile for agentic CAD work: a design script can hang, mutate module-level
state, print noisy diagnostics, or raise exceptions that should be captured as
run artifacts rather than destabilizing the harness.

The harness already treats CAD source as executable workspace code. The next
step is to put an explicit process boundary around that execution.

## Decision

Move design execution into a subprocess worker invoked by `cadx run`. The
parent process remains responsible for creating the run directory, snapshotting
source and parameters, enforcing timeout, and returning compact JSON to the
agent. The worker executes the design, exports CAD artifacts, writes
`diagnostics.json`, and writes `spatial.json` for successful runs.

`diagnostics.json` will include captured stdout and stderr for both successful
and failed runs. Timeout failures are represented as structured diagnostics with
status `timeout`.

## Success Criteria

- Tests fail before implementation because a hanging design is not timed out.
- Successful real `build123d` runs still export and inspect correctly.
- Runtime errors produce structured diagnostics.
- Timeouts produce structured diagnostics without hanging the test suite.
- stdout and stderr from design code are captured in `diagnostics.json`.

## Consequences

- `cadx run` becomes safer for autonomous loops.
- The worker module uses internal runner helpers for normalization and export
  until those helpers are split into a dedicated execution module.
- This is process isolation, not a full security sandbox. Filesystem and
  network restrictions remain host responsibilities.

## After Action Report

The red-state worker tests exposed both target issues: design stdout polluted
the CLI JSON stream, and `cadx run` had no `--timeout-seconds` option. The
implementation added `cadx.worker`, made `cadx run` invoke it in a subprocess,
captured stdout/stderr into `diagnostics.json`, and wrote structured timeout
diagnostics from the parent process.

The focused ADR 0005 tests passed, and the full suite passed with 8 tests.

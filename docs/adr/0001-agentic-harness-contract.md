# ADR 0001: Agentic Harness Contract

## Status

Accepted for initial implementation.

## Context

Coding agents need a deterministic CAD loop: edit source, execute it, receive
actionable observations, revise the model, and stop when checks pass. For CAD,
visual feedback alone is insufficient because raster images lose exact
dimensions and topology. The harness must therefore produce both viewable
artifacts and structured spatial data.

`build123d` model source should remain normal Python so agents can use existing
coding tools. The harness should not require a particular agent host, so the
first interface should be a CLI. MCP can be layered on top later.

## Decision

Implement a CLI named `cadx` with these commands:

- `init`: create starter `design.py`, `params.yaml`, and `requirements.yaml`.
- `run`: execute a design source and create a numbered run directory.
- `inspect`: create `spatial.json` from a run artifact directory.
- `render`: create a deterministic visual contact sheet.
- `evaluate`: compare `spatial.json` against `requirements.yaml`.
- `compare`: report run-to-run metric changes.

Each command writes explicit artifacts and prints compact JSON suitable for a
coding agent to parse.

## Success Criteria

- Tests fail before implementation and pass after the CLI and artifact logic
  are added.
- The core commands work without `build123d` installed for tests that use
  synthetic published objects.
- `cadx run` reports a clear dependency/runtime error instead of crashing when
  a design cannot execute.
- `cadx evaluate` gives concrete failed observations an agent can act on.

## Consequences

- The MVP emphasizes deterministic local artifacts before interactive viewing.
- Some CAD-specific exports are optional until `build123d` is installed.
- Explicit feature publishing is preferred over unreliable automatic detection
  in the first feature branch.

## After Action Report

Implemented the initial CLI-first harness with project initialization,
execution, inspection, rendering, evaluation, and comparison commands. The
red-state run failed because `cadx` did not exist; after implementation, the
focused remote suite passed with 3 tests.

The environment did not have `build123d` installed, so CAD kernel exports are
implemented as optional hooks and documented as a coverage gap. Synthetic
published objects are sufficient to verify the agent-facing loop without
requiring Open Cascade during unit tests.

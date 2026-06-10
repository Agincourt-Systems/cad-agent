# ADR 0002: Real build123d Integration

## Status

Accepted for implementation.

## Context

ADR 0001 established the agent-facing artifact loop with synthetic published
objects. That proved the CLI contract, but it did not verify that a real
`build123d` model can be executed, exported, and inspected without extra human
steps.

For the harness to be useful in CAD work, a successful `cadx run` must produce
the exact CAD artifacts and spatial facts the agent needs immediately. Requiring
a second command before the agent sees dimensions or topology introduces an
avoidable loop failure mode.

## Decision

Extend `cadx run` so real `build123d` objects produce:

- STEP, STL, and GLB exports when exporters are available.
- `spatial.json` in the same run directory.
- Runtime metadata that records the `build123d` version used for the run.
- Export warnings without discarding successful exports or spatial facts.

The implementation continues to support synthetic dictionary publications so
the fast contract tests remain lightweight.

## Success Criteria

- A test using an actual `build123d` `Box` fails before implementation and
  passes after implementation.
- The test verifies exported CAD files, immediate `spatial.json`, nonzero
  topology counts, and recorded `build123d` version metadata.
- Existing ADR 0001 contract tests continue to pass.

## Consequences

- `cadx run` becomes the primary observation command for agents.
- `cadx inspect` remains useful for recomputing or repairing `spatial.json`.
- Environments without `build123d` can still run synthetic tests, but real CAD
  integration requires installing the `cad` extra.

## After Action Report

Installed the `cad`, `render`, and `test` extras into the Python 3.10 user
site on `fjord` because `python3-venv` is unavailable there. The red-state
integration test failed because `cadx run` produced STEP/STL/GLB exports but
did not write `spatial.json` immediately.

The implementation now records runtime metadata, including the `build123d`
version, and calls the inspector during successful runs. The focused ADR 2 test
passes, and the full test suite passed with 4 tests.

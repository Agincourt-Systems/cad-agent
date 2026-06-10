# ADR 0008: Loop Orchestration

## Status

Accepted for implementation.

## Context

The harness can now run, render, evaluate, report, compare, isolate execution,
and detect common features. Agents still need to call those commands manually.
The original system goal calls for a low-human-input loop that lets an external
agent revise the project after failures.

## Decision

Add `cadx loop`, a bounded orchestrator that:

1. Runs the design.
2. Renders visual artifacts.
3. Evaluates requirements.
4. Stops on pass.
5. Invokes an external agent command after failures.
6. Repeats until pass or max iterations.

The loop writes `artifacts/loop.json` with iteration history and returns compact
JSON on stdout. The external command receives environment variables pointing to
the latest run directory, report, checks, and iteration number.

## Success Criteria

- Tests fail before implementation because `cadx loop` does not exist.
- A test loop fails once, invokes a fixer command, reruns, and passes.
- A max-iteration test returns structured failure without hanging.
- Existing ADR 0001 through ADR 0007 tests continue to pass.

## Consequences

- Codex or another coding agent can be wrapped as the external command.
- The harness remains agent-agnostic; it does not embed provider-specific APIs.
- Shell execution of the external agent command is intentional and must be
  treated as trusted workspace execution.

## After Action Report

The red-state tests failed because `cadx loop` was not a recognized command.
The implementation added a loop orchestrator that composes `run`, `render`, and
`evaluate`, invokes a trusted external command after failed evaluations, writes
`artifacts/loop.json`, and returns structured pass/fail JSON.

The focused ADR 0008 tests passed, and the full suite passed with 13 tests.

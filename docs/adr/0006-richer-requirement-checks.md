# ADR 0006: Richer Requirement Checks

## Status

Accepted for implementation.

## Context

The evaluator currently supports exact scalar dimensions, feature counts, and
feature dimensions. That is enough for early loop testing, but CAD requirements
often need ranges, topology assertions, and simple clearances between parts.

These checks can be evaluated from `spatial.json`, so they should not require
reloading CAD geometry.

## Decision

Extend `cadx evaluate` with:

- Dimension range checks using `min` and/or `max` in addition to `equals`.
- Topology checks against paths such as `obj.box.topology.faces`.
- Axis-aligned bounding-box clearance checks between two objects.

The evaluator will keep returning structured `checks.json` and will include
observed/expected facts in `report.md`.

## Success Criteria

- Tests fail before implementation because topology and clearance checks are
  unsupported.
- Passing tests verify dimension ranges, topology, and clearance.
- Failing tests verify failed ids and report text for dimension and clearance
  failures.
- Existing ADR 0001 through ADR 0005 tests continue to pass.

## Consequences

- Agents can express more useful mechanical constraints without custom test
  code.
- Clearance is an AABB approximation, not exact shape distance. Exact distance
  checks can be added later from CAD geometry if needed.

## After Action Report

The red-state tests failed because dimension range checks required `equals`,
and `topology`/`clearance` check types were unsupported. The implementation
added a shared numeric comparator for `equals`, `min`, and `max`, topology
target-path checks, and AABB clearance checks between two published objects.

The focused ADR 0006 tests passed, and the full suite passed with 10 tests.

# ADR 0009: Exact Geometric Clearance

## Status

Accepted for implementation.

## Context

ADR 0006 added AABB clearance checks because they are cheap and available from
`spatial.json`. That approximation can be too conservative: two round parts can
have overlapping bounding boxes while their actual BREP geometry is separated.

The harness already exports each published object to STEP. Those exact
artifacts should be used when a requirement asks for exact clearance.

## Decision

Extend `clearance` checks with `method: exact`. Exact clearance loads the two
published STEP exports with `build123d.import_step` and evaluates
`Shape.distance()` between the two BREP shapes.

The existing AABB clearance behavior remains the default. Exact clearance
requires STEP exports and therefore applies to real CAD publications rather than
synthetic dictionary-only objects.

## Success Criteria

- Tests fail before implementation because exact clearance still uses AABB
  behavior.
- A diagonal-cylinder case with overlapping AABBs passes an exact minimum
  clearance check.
- A failing exact clearance check records observed BREP distance and report
  details.
- Existing ADR 0001 through ADR 0008 tests continue to pass.

## Consequences

- Agents can specify exact clearance where conservative AABB checks are too
  coarse.
- Exact checks require CAD dependencies and STEP artifacts, so they are slower
  than AABB checks.

## After Action Report

The red-state tests failed because `method: exact` still used the existing AABB
clearance result, reporting `0` for diagonal cylinders whose bounding boxes
overlap. The implementation now loads STEP exports for the referenced objects
and evaluates `Shape.distance()` for exact BREP clearance.

The focused ADR 0009 tests passed, and the full suite passed with 15 tests.

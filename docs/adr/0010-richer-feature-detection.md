# ADR 0010: Richer Feature Detection

## Status

Accepted for implementation.

## Context

ADR 0007 detects cylindrical through holes. Useful CAD inspection needs more
than holes: agents also need to reason about datum planes, protruding bosses,
and simple slotted cutouts. These can be detected from STEP topology without
requiring design scripts to call `publish_feature()`.

## Decision

Extend automatic feature detection with:

- `planar_datum` features for planar faces.
- `cylindrical_boss` features for full cylindrical protrusions.
- `slot` features for simple through obround slots detected from paired
  partial cylindrical faces.

The detector remains conservative and records `detected: true` plus a source
object reference on all automatic features.

## Success Criteria

- Tests fail before implementation because only cylindrical holes are detected.
- A real model with a slot, boss, and planar faces produces those feature kinds.
- Existing feature-count and feature-dimension requirements can evaluate the
  detected slot and boss.
- Existing ADR 0001 through ADR 0009 tests continue to pass.

## Consequences

- Agents can validate common mechanical features without manual publication.
- Slot detection is limited to simple obround through-slots with paired
  cylindrical ends.

## After Action Report

The red-state test failed because no `planar_datum`, `cylindrical_boss`, or
`slot` features were detected. The implementation now emits planar datum
features from planar faces, classifies non-through full cylindrical faces as
bosses, and detects simple obround slots from paired partial cylindrical faces.

The focused ADR 0010 test passed, and the full suite passed with 16 tests.

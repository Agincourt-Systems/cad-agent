# ADR 0007: Automatic Feature Detection

## Status

Accepted for implementation.

## Context

The harness supports explicit `publish_feature()` calls, which are reliable and
agent-controllable. However, requiring every feature to be explicitly published
limits the usefulness of imported or generated CAD artifacts. Common features
such as cylindrical through holes should be detected automatically when a STEP
export is available.

## Decision

Extend inspection to load STEP exports and detect cylindrical faces as
`cylindrical_hole` features. Each detected feature records center, axis,
diameter, through classification, source object label, and `detected: true`.

Explicitly published features remain supported and are preserved. Automatic
detection augments them rather than replacing them.

## Success Criteria

- Tests fail before implementation because a real plate with holes has no
  automatically detected features.
- Inspection detects two cylindrical holes from a real `build123d` STEP export.
- Existing feature count and feature dimension checks pass using detected
  features.
- Existing ADR 0001 through ADR 0006 tests continue to pass.

## Consequences

- Agents can evaluate common hole requirements without hand-authored feature
  metadata.
- Initial detection is intentionally conservative and focused on cylindrical
  through holes. Slots, bosses, and richer face classification can follow later.

## After Action Report

The red-state test failed because the real plate with two cylindrical cutouts
produced zero detected features. The implementation now loads STEP exports
during inspection, scans cylindrical faces, and records detected
`cylindrical_hole` features with center, axis, diameter, through status, source
object, and stable generated ids.

The focused ADR 0007 test passed, and the full suite passed with 11 tests.

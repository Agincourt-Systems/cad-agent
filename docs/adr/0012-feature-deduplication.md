# ADR 0012: Deduplicate Explicit and Detected Features

## Status

Accepted for implementation.

## Context

ADR 0007 introduced automatic feature detection from STEP exports and stated
that detection "augments" explicit `publish_feature()` calls "rather than
replacing them". In practice the two channels were concatenated with no
reconciliation, so a design that both publishes a feature explicitly and
exports STEP geometry reports the same physical feature twice in
`spatial.json`.

This is not hypothetical: the starter project written by `cadx init` publishes
its two mount holes explicitly, automatic detection finds the same two holes
in the STEP export, and the starter `mount_holes` feature-count check observes
4 holes on a 2-hole plate. The out-of-the-box project fails its own starter
requirements, which violates the harness acceptance criterion that an agent
can converge from the starter without human help, and it corrupts
`feature_count` semantics for any design that follows the spec's recommended
explicit-publication pattern.

The system spec already defines the precedence: automatic detection is
advisory, and explicit publication is the source of truth for critical
dimensions.

## Decision

Reconcile the two feature channels during inspection:

- Explicitly published features are always preserved unchanged and keep their
  stable agent-facing ids.
- An automatically detected feature is suppressed when it duplicates an
  explicit feature: same `kind`, any size properties present on both sides
  (`diameter`, `width`, `length`) agree within a 0.05 mm tolerance, and the
  explicit center lies on the detected geometry within the same tolerance.
- For cylindrical kinds the center comparison measures radial distance from
  the explicit center to the detected axis line, because publication
  conventions legitimately place a hole "center" anywhere along its axis.
  Other kinds compare Euclidean center distance.
- When a detected feature is suppressed as a duplicate, the surviving explicit
  feature gains `confirmed_by_detection: true` so agents can tell which
  publications are corroborated by real geometry.
- Explicit features with no matching geometry, and detected features with no
  matching publication, pass through untouched. Matching is deliberately
  conservative: an explicit feature without a 3-component `center` is never
  merged, so under-specified publications can at worst double-count, never
  silently swallow a real detection.

## Success Criteria

- Tests fail before implementation because the `cadx init` starter project
  fails its own requirements with 4 observed cylindrical holes.
- After implementation the full `init` → `run` → `evaluate` starter flow
  passes all starter checks, and `spatial.json` contains exactly the two
  explicit mount-hole features, both marked `confirmed_by_detection`.
- An explicit feature published at a location with no corresponding geometry
  is preserved alongside detected features and is not marked confirmed.
- Existing ADR 0001 through ADR 0011 tests continue to pass.

## Consequences

- `feature_count` and `feature_dimension` checks observe each physical feature
  exactly once regardless of how the design mixes explicit publication and
  automatic detection.
- Agents gain a corroboration signal (`confirmed_by_detection`) that
  distinguishes verified publications from unverified intent.
- The 0.05 mm match tolerance is a fixed policy. Publications that are more
  than 0.05 mm off from the real geometry still double-count; surfacing that
  discrepancy is preferable to guessing.

## After Action Report

The red-state test failed as predicted: the starter project evaluation
returned `fail` with `mount_holes` observing 4 cylindrical holes. The
implementation merges detected features into explicit ones inside
`inspect_run` using kind, size, and axis-aware center matching.

After implementation the starter flow passes 3/3 checks, the phantom-feature
guard test confirms unmatched explicit features are preserved without a
confirmation flag, and the full suite passed with 19 tests. A live smoke test
of `cadx init` → `run` → `evaluate` in a scratch directory passed end to end,
and `cadx loop` now converges on the starter project in one iteration.

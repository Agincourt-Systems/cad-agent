# ADR 0056: One Documented "Use This for Robotics" Inertia Field

## Status

Accepted for implementation.

## Context

Deficiency **D-033** (`docs/specs/arm-deficiencies.md`): `spatial.json` ships
**three** inertia tensors per part, each honest about itself but collectively a
consumer trap:

- `matrix_of_inertia` — **world-geometric**: unit-density (mm⁵), about the part
  centroid, in **world axes at the placed pose** (ADR 0037). A rotated part shows
  pose-artifact off-diagonals.
- `inertia_link_frame` — **link-frame-geometric**: unit-density (mm⁵), about the
  centroid, rotated into the **body frame** (ADR 0047). Off-diagonals reflect real
  geometry, not pose.
- `inertia_link_frame_mass` — **link-frame-mass**: mass-scaled (g·mm²), about the
  centroid, in the **body frame** (ADR 0047).

Each carries a truthful `*_semantics` record, but a robotics consumer must still
*know* that a URDF `<inertial>` wants the **body-frame, mass-scaled** tensor —
`inertia_link_frame_mass` — and that using the world-geometric tensor in a
world-aligned frame yields wrong cross-terms for rotated parts. That knowledge
lives only in the deficiency notes and the probe suite, not in the artifact or the
docs. A consumer that grabs the first inertia-looking field ships wrong dynamics.

## Decision

Point robotics consumers at the one correct field, in two places — the artifact
itself and a worked document — and cross-link both from the README.

### (a) A self-describing pointer in the JSON

`runner._apply_link_frame_inertia_mass` adds one key,
**`recommended_use`**, to the `inertia_link_frame_mass_semantics` record it
already emits:

```
"recommended_use": "URDF <inertial>: use this tensor (body-frame, mass-scaled,
                    g*mm^2) with the part center of mass as <origin>. See
                    docs/inertia-consumers.md."
```

This is a one-line, self-contained pointer that travels with the data: a consumer
reading `spatial.json` alone learns which of the three tensors to use and where to
read the full recipe. It is emitted exactly where the mass tensor is (only when a
density resolved), so a density-free run is unchanged.

### (b) A worked document — `docs/inertia-consumers.md`

A single 30-degree-rotated part, all three tensors shown with their distinguishing
values, and the exact URDF `<inertial>` recipe: which field feeds `<inertia>`,
which frame it is in, what the `<origin>` (the CoM) is, the g·mm² → kg·m² unit
conversion, and the density-scaling story (per ADR 0055, `density` is g/mm³, so the
mass tensor's `g*mm^2` label is only correct when the density unit contract is
honored). ASD-STE100 style: short sentences, one instruction each.

### (c) README cross-link

The mass/density/inertia section links to `docs/inertia-consumers.md` and names
`inertia_link_frame_mass` as the robotics field.

## Alternatives considered

- **Remove or rename a tensor.** Rejected: all three have shipping consumers and
  truthful semantics; the problem is guidance, not correctness.
- **Doc only, no JSON key.** Rejected: a consumer reading the artifact
  programmatically never opens the doc. The self-describing key is what makes the
  guidance travel with the data.
- **A separate top-level "robotics" block.** Rejected as heavier than needed; the
  pointer belongs beside the tensor it recommends.

## Success Criteria

Written so the new test fails before implementation and passes after:

- `test_recommended_use_points_at_link_frame_mass` (kernel): a part with a
  resolved density emits `inertia_link_frame_mass_semantics.recommended_use`, a
  non-empty string that names the doc (`inertia-consumers.md`) and the URDF use.
  Fails today: the key is absent.
- `test_no_recommended_use_without_density` (kernel): a density-free part emits no
  `inertia_link_frame_mass_semantics` at all (hence no `recommended_use`),
  confirming the density-free run stays unchanged.
- The existing `test_link_frame_mass_when_density_resolves` semantics-equality
  assertion is updated to include the new key (the record grew by one documented
  field); all other ADR 0047 inertia tests pass unchanged.
- `docs/inertia-consumers.md` exists and the README links to it.

## Consequences

- A robotics consumer reading `spatial.json` is told, in the record itself, which
  tensor to use and where the full recipe is; the trap of picking the
  world-geometric tensor for a rotated part is closed by guidance rather than by
  removing a field.
- The `inertia_link_frame_mass_semantics` record grows by one key; every other
  emitted value is unchanged, and a density-free run is byte-identical.

## After Action Report

_Pending downstream verification._

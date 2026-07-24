# ADR 0055: Declare and Enforce the `density=` Unit Contract

## Status

Accepted for implementation.

## Context

Deficiency **D-031** (`docs/specs/arm-deficiencies.md`): every mass and
mass-inertia label the pipeline emits is hardcoded in **grams** /
**g·mm²** — `mass_properties.mass` is grams, and
`inertia_link_frame_mass_semantics.units` is `"g*mm^2"` (ADR 0047). Those labels
are only correct if the author-supplied `density` is in **g/mm³**. Nothing states
that contract, and nothing enforces it.

An author who intends `density` in **kg/mm³** — e.g. `density=2.7e-6` for aluminum
in kg/mm³ — gets `mass = volume × 2.7e-6` labeled "grams", which is wrong by a
factor of 1000, with no error and no warning. This is the silent-wrongness class:
the number looks plausible and the label lies.

The built-in material table (ADR 0035, `cadx.density`) is unaffected by this bug —
it already returns g/mm³ and writes `metadata.density` itself, never going through
the `density=` keyword — so the fix must be confined to the explicit `density=`
path and must not perturb the material-implied path.

## Decision

Make the unit contract **explicit** and give it an **escape hatch**, entirely
within `registry.publish()`:

### (a) Document the contract

`publish()`'s docstring and the README state plainly: **`density` is in g/mm³**.
That is the unit every emitted mass / mass-inertia label already assumes, so a
g/mm³ density makes every label true with no scaling.

### (b) An explicit `density_unit=` parameter

`publish()` gains an optional keyword `density_unit: str = "g/mm^3"`. Accepted
values and their factor to g/mm³:

| `density_unit` | factor |
| --- | --- |
| `"g/mm^3"` (default) | 1.0 |
| `"kg/mm^3"` | 1000.0 |

When a non-default unit is given **and** an explicit numeric `density` is present,
`publish()` normalizes the stored density to g/mm³ at publish time
(`density_kg/mm³ × 1000 = density_g/mm³`, since 1 kg = 1000 g) and records the
author's declared unit as `metadata.density_unit_declared`. Because the stored
`metadata.density` is now always g/mm³, every downstream label
(`mass` grams, `inertia_link_frame_mass` g·mm²) stays universally true without any
consumer-side guessing. `density_source` continues to read `"explicit"` — the
value is still author-supplied; only its unit was normalized.

Example (the deficiency's repro, now correct):

```python
publish("bracket", part, density=2.7e-6, density_unit="kg/mm^3")
# stored metadata.density == 2.7e-3 g/mm^3
# metadata.density_unit_declared == "kg/mm^3"
# mass == volume × 2.7e-3  (correct grams)
```

### (c) Unknown unit → loud `ValueError`

An unrecognized `density_unit` string raises `ValueError` **at publish time**,
naming the accepted values, regardless of whether a density was supplied — so a
typo (`"kg/m^3"`, `"kg/mm3"`) fails loudly at the design line that made it rather
than shipping a mislabeled number.

### Scope guards

- The default (`"g/mm^3"`) path is **byte-identical** to before this ADR: factor
  1.0, no scaling, and no `density_unit_declared` key is added. Runs that never
  mention `density_unit` are unchanged.
- `density_unit_declared` is recorded only when a non-default unit actually scaled
  a present density, so it appears exactly when it is meaningful.
- The material-implied path (`_apply_material_density`, ADR 0035) is untouched: it
  never consumes `density=` and always writes g/mm³. Only an explicit `density=`
  is scaled.
- The edit is confined to `publish()`'s signature, docstring, and normalization
  region; `publish_sheet_metal()` is left as-is (see Consequences).

## Alternatives considered

- **Scale the labels from the declared unit instead of the density.** Rejected:
  it would require every emitter (mass, per-part inertia, assembly inertia) to
  learn the unit and stay in sync. Normalizing the density once, at the source,
  keeps every existing label correct with no downstream change.
- **Infer the unit from the magnitude.** Rejected: guessing units from numeric
  size is exactly the silent-wrongness the deficiency warns against.
- **Only document, do not validate.** Rejected: documentation alone does not stop
  the 1000× error; the `ValueError` and normalization are what make it safe.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_kg_per_mm3_normalizes_to_grams` (kernel-free, synthetic volume): a part
  published with `density=2.7e-6, density_unit="kg/mm^3"` and a synthetic volume
  of 1000 mm³ carries `metadata.density == 2.7e-3`, `metadata.density_unit_declared
  == "kg/mm^3"`, `metadata.density_source == "explicit"`, and
  `mass_properties.mass == 2.7` grams. Fails today: `density_unit` is not a
  parameter (`TypeError`).
- `test_default_unit_is_byte_identical` (kernel-free): a part with `density=2.7e-3`
  and no `density_unit` carries `metadata.density == 2.7e-3`, `mass == 2.7`, and
  **no** `density_unit_declared` key. Guards the byte-identical default.
- `test_unknown_density_unit_raises` (kernel-free, direct `publish` call): a bad
  `density_unit` string raises `ValueError` naming the accepted units, even with
  no density supplied.
- `test_material_path_unaffected` (kernel-free): a part with `material="6061-T6"`
  and no explicit density still resolves via the table to `metadata.density ==
  0.0027` with `density_source == "material:6061-t6"` and no
  `density_unit_declared`. Guards the ADR 0035 path.
- Existing ADR 0035 / ADR 0047 material and inertia tests pass unchanged.

## Consequences

- The 1000× mislabel is eliminated for the explicit-density path, and the accepted
  units are self-describing (`density_unit_declared`) whenever a conversion
  happened.
- `publish_sheet_metal()` does not yet accept `density_unit`; a folded sheet part
  that needs a non-g/mm³ density must pre-convert. Recorded as a residual; the
  same normalization could be lifted into a shared helper if that need appears.
- The contract is now stated in three places that a reader will hit: the
  `publish()` docstring, the README mass/density section, and the emitted
  `density_unit_declared` field.

## After Action Report

_Pending downstream verification._

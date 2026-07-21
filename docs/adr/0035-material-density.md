# ADR 0035: Material-Implied Density and Per-Part Mass

## Status

Accepted for implementation.

## Context

Deficiency **D-008** (`docs/specs/arm-deficiencies.md`) observes that a declared
material name carries no physics: `publish_part_meta(material="6061-T6")` records
the string for the BOM but never yields a density, and no part record carries a
mass. Density flows into cadx *only* as the explicit `publish(..., density=<g/mm³>)`
keyword, which ADR 0015 already consumes to mass-weight an assembly center of mass.
An agent that knows the alloy still has to carry its own material→density table and
re-pass `density=` on every publish, and it can never read a per-part mass back out
of `spatial.json`.

This blocks the downstream arm project's URDF/mass-budget work: a link's mass is the
first thing a `<inertial>` block needs, and the arm is specified in named alloys
(6061-T6 extrusions, 5052-H32 sheet, 304 hardware, Ti-6Al-4V where mass matters), not
in bare densities.

Two independent facts already exist in the pipeline but are never joined:

1. **Material name.** Declared either as `publish(..., material="6061-T6")` metadata
   (lands in the published entry's `metadata` dict, surfaced on each `spatial.json`
   object as `metadata.material`) *or* as `publish_part_meta(label, material=...)`
   (lands in the separate `_PART_META` list, surfaced in `diagnostics.json`
   `part_meta` and joined by label only in `bom.py`).
2. **Volume.** Computed for every real solid in `mass_properties.volume`
   (`runner._mass_properties`).

Mass is simply `volume × density`. The missing piece is a name→density table and the
join that applies it.

`src/cadx/materials.py` already exists but is strictly a *rendering* concern — it maps
material strings to shading presets (ADR 0027). Entangling physical density with
appearance would couple two unrelated axes (a red-anodized 6061 bracket and a bare
6061 bracket have identical density but different looks, and vice-versa). Density
therefore lives in its own module.

## Decision

1. **New module `src/cadx/density.py`.** A curated built-in table keyed by the common
   alloy/polymer names the arm project uses, values in **g/mm³** (the exact unit the
   existing `publish(..., density=)` and ADR 0015 aggregation already assume, so a
   looked-up density is drop-in interchangeable with an explicit one). Seeded set:

   | material | g/cm³ | g/mm³ |
   |----------|-------|-------|
   | 6061-T6 aluminum | 2.70 | 0.00270 |
   | 5052-H32 aluminum | 2.68 | 0.00268 |
   | 304 stainless | 8.00 | 0.00800 |
   | 1018 / mild steel | 7.87 | 0.00787 |
   | brass (C260/C360) | 8.50 | 0.00850 |
   | ABS | 1.04 | 0.00104 |
   | PLA | 1.24 | 0.00124 |
   | PETG | 1.27 | 0.00127 |
   | Ti-6Al-4V | 4.43 | 0.00443 |

   `resolve_density(name)` normalizes case and punctuation and matches by **exact
   collapsed alias** (`"6061-T6"`, `"ti6al4v"`) *or* **whole-token alias**
   (`"304"` inside `"304 Stainless"`), returning a `(canonical_key, density)` pair or
   `None`. Token/collapsed matching (rather than the render module's loose substring
   hints) avoids false hits such as `"plastic"` matching `"pla"`. Matching is a curated
   ordered scan so a name that could touch two entries resolves deterministically.

2. **Join in the runner: `_apply_material_density(published, part_meta)`.** Runs in the
   worker after `_normalize_published` (so it sees the finished per-object records) and
   before diagnostics is written and `inspect_run` aggregates the assembly (so implied
   densities reach the ADR 0015 CoM weighting). For each object:
   - Resolve the material from the object's own `metadata.material` first, else the
     `part_meta` record joined by label.
   - **Explicit `density` always wins.** If `metadata.density` is a positive number it
     is used untouched and tagged `metadata.density_source = "explicit"`.
   - Else, if the material resolves, write the implied density to `metadata.density`
     and tag `metadata.density_source = "material:<canonical>"`. A known material now
     *implies* density with no `density=` needed — the D-008 fix.
   - Else, if a material was declared but is unknown, **guess nothing** (behave as
     today) and record `metadata.density_resolved = false` so the unresolved state is a
     machine-visible fact rather than a silent gap.
   - Whenever a density is resolved and a positive `volume` is present, write
     `mass_properties.mass = volume × density` (grams). Missing volume (e.g. a
     kernel-free dict publication without one) simply omits `mass`.

   The pass mutates the normalized records in place; it is pure Python and adds no keys
   when nothing resolves, so existing runs are byte-stable.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_resolve_known_materials`: `resolve_density` returns the tabulated g/mm³ for
  each seeded alloy across spelling variants (`"6061-T6"`, `"Aluminum 6061-T6"`,
  `"ti6al4v"`, `"304 Stainless"`, `"mild steel"`); an unknown name returns `None`;
  `"plastic"` does **not** resolve to PLA.
- `test_material_implies_density_and_mass` (real geometry): a `Box` published with
  `material="6061-T6"` and no `density=` reports `metadata.density == 0.00270`,
  `metadata.density_source == "material:6061-T6"`, and
  `mass_properties.mass ≈ volume × 0.00270` — all absent today.
- `test_explicit_density_overrides_material`: a part with both `material=` and an
  explicit `density=` keeps the explicit value and is tagged `density_source ==
  "explicit"`.
- `test_part_meta_material_implies_density`: material declared via
  `publish_part_meta` (not `publish`) still yields an implied density and mass on the
  spatial object, and drives the assembly aggregation to `weighting == "mass"`.
- `test_unknown_material_records_unresolved`: an unknown material sets
  `density_resolved == false`, adds no `density`, and adds no `mass` (no guess).
- Existing ADR 0001–0034 tests continue to pass; the new keys are additive.

## Consequences

- A named alloy is now sufficient to get a per-part mass; the arm project deletes its
  private material table and its blanket `density=` boilerplate.
- Because implied density lands in `metadata.density`, the ADR 0015 assembly CoM
  aggregation upgrades from volume-weighting to true mass-weighting for free whenever
  every part names a known material — no change to `_assembly_center_of_mass`.
- `density_source` and `density_resolved` make provenance explicit: an agent can tell a
  looked-up density from an author-supplied one, and can see when a declared material
  was not understood rather than silently getting no mass.
- The table is intentionally small and hand-curated. Adding an alloy is one row plus
  its aliases. It is *not* a metallurgical database; tempered vs annealed density
  differences are within its rounding and out of scope.

## After Action Report

The red state failed as predicted: `cadx.density` did not exist and
`runner._apply_material_density` was undefined, so every new test errored on
import before any behavior ran.

Implementation landed as designed: a standalone `src/cadx/density.py` table with
`resolve_density`, and a `runner._apply_material_density(published, part_meta)`
join wired into the worker between `_normalize_published` and the diagnostics
write, so implied densities reach `inspect_run`'s assembly aggregation with no
change to `_assembly_center_of_mass`.

One correctness fix during implementation: the first cut called
`record.setdefault("metadata", {})` unconditionally, which synthesized an empty
`metadata: {}` on every part that declared neither a material nor a density —
a silent shape regression on `spatial.json` objects. The final version computes
the updates first and only touches `metadata` when there is something to write,
keeping material-free runs byte-identical to before this ADR.

Lookup strictness earned its keep: the whole-token / whole-collapsed matching
rule (rather than the render module's substring hints) is what makes
`resolve_density("plastic")` return `None` instead of falsely matching `"pla"`,
verified by `test_resolve_known_materials`.

Coverage: a pure-table test pins every seeded density and the non-match paths;
unit tests pin the explicit-override, unknown-material, and `publish_part_meta`
join branches of `_apply_material_density`; real-geometry end-to-end tests pin
the implied density + computed mass on a `spatial.json` object and the upgrade
of the assembly aggregation to mass-weighting when material is named only via
`publish_part_meta`. Full suite: 172 passed (166 prior + 6 new), no regressions.

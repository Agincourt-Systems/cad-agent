# ADR 0045: Warn on Unresolved Material

## Status

Accepted for implementation.

## Context

Deficiency **D-016** (`docs/specs/arm-deficiencies.md`) observes that ADR 0035
turned a declared material name into an implied density and a per-part mass, but
handled the *miss* silently. When a part declares a material that the built-in
table does not recognize — a typo (`"unobtanium"`), or an alloy the seeded table
does not yet carry — and supplies no explicit `density=`, the runner records only
`metadata.density_resolved == false` on that object and writes **nothing** to the
run's `warnings` array.

That is a machine-visible fact *per object*, but it is not *surfaced*. A
downstream gate that watches the `diagnostics.json` `warnings` channel (the
idiomatic place a run advertises "something is off but the run still completed")
sees an empty list. The part silently carries no `mass`, and a stability or joint-
torque check built on the assembly mass rollup then ships a zero-mass link into
the torque math without any signal that a material was misdeclared. The failure is
wrong-but-plausible in exactly the way the deficiency protocol exists to catch:
the run is `status:"ok"`, the numbers merely undercount.

The pipeline already emits this class of "run completed, but note this" signal
through structured warning objects. `_pose_range_warnings` in `runner.py` produces
a `mate_out_of_range` warning — `{"type", "label", "message"}` — for a joint posed
outside its declared limits; `_resolve_mates` emits `mate_unresolved` /
`mate_failed` the same way. The worker collects every such warning into the
`warnings` list that is written into `diagnostics.json`. An unresolved material is
the identical shape of event (the geometry is honest to what was asked; the
warning is the record that something the author declared could not be honored), so
it should travel the same channel with the same record shape.

## Decision

Emit a structured `material_unresolved` warning from the one pass that already
makes the resolve/miss decision: `runner._apply_material_density`.

1. **`_apply_material_density` returns its warnings.** Its signature changes from
   returning `None` to returning `list[dict[str, Any]]`. The pass already walks
   every published object and, in its existing "declared but unrecognized" branch
   (the branch that sets `metadata.density_resolved = false`), it now *also*
   appends one warning:

   ```json
   {
     "type": "material_unresolved",
     "label": "<part label>",
     "material": "<the declared material string>",
     "message": "material '<m>' on '<label>' did not resolve to a density; part has no mass"
   }
   ```

   The `"type"`/`"label"`/`"message"` triple mirrors `mate_out_of_range` exactly
   (existing warnings key the discriminator `"type"`, not `"kind"`), plus the
   offending `material` string so a gate can report it without re-reading the
   object metadata.

2. **The worker collects it.** `worker.execute_worker` captures the returned list
   and extends the same `warnings` list it already builds from the mate resolver,
   so the material warnings land in `diagnostics.json` alongside every other
   warning with no new plumbing or schema key.

**Emitted in exactly one case, and never in the other three.** The warning rides
the *existing* unresolved branch, so its firing conditions are inherited, not
re-derived:

* **Material resolves** → the density is written, no warning. (The resolve branch
  never touches the miss branch.)
* **Explicit `density=` supplied** (even alongside an unknown material name) →
  the explicit branch wins first and tags `density_source = "explicit"`; the
  material is never consulted, so no warning. Explicit intent is not second-
  guessed.
* **No material declared at all** → neither branch runs; no warning.
* **Material declared, unrecognized, and no explicit density** → the one case
  D-016 is about: `density_resolved = false` *and* a `material_unresolved`
  warning.

**This is a warning, not a failure.** Exit codes and run status are untouched: the
run is still `status:"ok"`. The value is that a gate can now *choose* to treat the
warning as fatal (`any(w["type"] == "material_unresolved" for w in warnings)`)
without cadx imposing that policy. This matches ADR 0009's stance that limit
violations warn rather than fail, leaving enforcement to the consumer's gate.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_unresolved_material_emits_warning` (unit, no kernel):
  `_apply_material_density` on a record with `material="unobtanium"` and no
  density returns a list containing one `material_unresolved` warning naming the
  part label and the material string; the object still gets
  `density_resolved == false` and no `mass` (ADR 0035 behavior preserved).
- `test_resolved_material_emits_no_warning`: a record with `material="6061-T6"`
  resolves and returns an **empty** warning list.
- `test_explicit_density_with_unknown_material_no_warning`: a record with
  `material="unobtanium"` **and** an explicit positive `density=` keeps the
  explicit density, tags `density_source == "explicit"`, and returns an empty
  warning list — explicit intent suppresses the miss warning.
- `test_no_material_declared_no_warning`: a record that declares neither material
  nor density returns an empty warning list and is byte-unchanged.
- `test_unresolved_material_warning_reaches_diagnostics` (end-to-end through
  `cadx run`, real geometry): a `Box` published with `material="unobtanium"` and
  no density yields `status:"ok"` and a `diagnostics.json` `warnings` entry with
  `type == "material_unresolved"` and the correct label/material.
- Existing ADR 0035 material-density tests continue to pass (the warning is
  additive; densities, masses, and `density_source`/`density_resolved` tags are
  unchanged).

## Consequences

- A misdeclared alloy is now a first-class, gateable signal on the warnings
  channel instead of a per-object fact a consumer must remember to poll. The
  arm project's mass-budget gate asserts on `material_unresolved` and fails a run
  that would otherwise ship a massless link.
- No schema key is added and no run status changes; a run that declares only
  known materials (or explicit densities, or no materials) produces a
  byte-identical `diagnostics.json` to before this ADR.
- The warning names the material string, so a triage reading only the warnings
  array sees *which* name failed without cross-referencing object metadata.

## After Action Report

The red state failed as predicted: `_apply_material_density` returned `None`, so
the four unit tests could not obtain a warning list from it and the end-to-end
test found an empty `warnings` array — all 5 new tests failed before the change.

Implementation landed exactly as designed and stayed minimal. The warning is
emitted from the *one* pre-existing "declared but unrecognized" branch of
`_apply_material_density`, so the three silent cases (resolving material, explicit
density, no material) required no new conditionals — they simply never reach that
branch. The signature changed from `-> None` to `-> list[dict]`, and the only
other edit was the worker folding the returned list into the warnings channel it
already builds. No schema key was added and run status is unchanged.

Two design points earned their keep. First, keying the warning `"type"` (not
`"kind"`) to mirror `mate_out_of_range` means a gate can filter every run warning
uniformly. Second, letting the *explicit-density* branch win first is what makes
`material="unobtanium"` + `density=0.004` stay silent — the explicit-intent test
pins that an author who supplied a density is never nagged about an unrecognized
name.

Coverage: unit tests pin all four cases (warn / resolve-silent / explicit-silent /
no-material-silent) plus preservation of the ADR 0035 `density_resolved`/`mass`
behavior; an end-to-end `cadx run` test confirms the warning reaches
`diagnostics.json` with `status:"ok"` unchanged. All 5 new tests pass and the 6
ADR 0035 material-density tests remain green. The full suite is green end-to-end
at the top of the stacked branch (through ADR 0046): 212 passed, no regressions.

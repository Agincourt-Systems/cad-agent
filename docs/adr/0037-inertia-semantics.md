# ADR 0037: Machine-Readable Inertia Semantics

## Status

Accepted for implementation.

## Context

Deficiency **D-007** (`docs/specs/arm-deficiencies.md`) names the sharpest trap in
cadx's mass properties: `mass_properties.matrix_of_inertia` *looks* like a mass
moment of inertia but is a **unit-density geometric second moment in mm⁵**, taken
**about the part centroid** and expressed in **world axes at the placed pose**. A
naive consumer that treats it as kg·m² is wrong by the density factor **and** by
`10¹⁵` (mm⁵→m⁵ scaling combined with the missing density), and — for a rotated part —
by the body-frame rotation it never applied. The value is *correct once understood*
(ρ·MoI matched a hand-calculated plate inertia to <0.5%), but nothing in the JSON
states the units, the density basis, the reference point, or the axes.

ADR 0036 then introduced a **second** tensor, `assembly.inertia.tensor`, whose units
switch between g·mm² (mass-weighted) and mm⁵ (volume-weighted) depending on the
`weighting` field. That is another number whose meaning a reader must infer.

The field name `matrix_of_inertia` cannot be renamed or removed: the arm project's
probe suite deliberately pins it (`probes/assembly/probe_06*`) to guard the semantics
against silent change. The fix must therefore be **purely additive** — describe the
existing numbers rather than alter them.

## Decision

Emit machine-readable semantics *alongside* each inertia tensor. Nothing existing is
renamed, moved, or re-valued.

1. **Per-part semantics (new sibling key).** Wherever `runner._mass_properties`
   successfully produces `matrix_of_inertia`, it also emits a sibling
   `matrix_of_inertia_semantics`:

   ```json
   "matrix_of_inertia_semantics": {
     "units": "mm^5",
     "density": "unit (geometric)",
     "about": "part centroid",
     "axes": "world at placed pose"
   }
   ```

   The base tensor stays a bare 3×3 list so the pinned probe is untouched; the
   semantics ride beside it. The two keys are emitted together (same guard), so a
   consumer either gets both or neither.

2. **Assembly semantics (fields on the existing block).** The ADR 0036 `inertia`
   block gains the two fields that make it self-describing, chosen from the same
   `weighting` decision that already governs the tensor:

   * mass-weighted → `"units": "g*mm^2"`, `"density": "mass-weighted"`;
   * volume-weighted → `"units": "mm^5"`, `"density": "unit (geometric)"`.

   The block already carries `about` and `axes` from ADR 0036, so the final shape is

   ```json
   "inertia": {
     "tensor": [[...],[...],[...]],
     "units": "g*mm^2",
     "density": "mass-weighted",
     "about": "assembly center of mass",
     "axes": "world"
   }
   ```

   **Why the two shapes differ.** The per-part field is a pre-existing *bare* 3×3
   list that downstream probes pin, so its semantics must live in a *separate*
   sibling key; the assembly `inertia` is a rich object introduced only one ADR
   earlier, so its semantics are folded directly into it. Both are fully machine
   readable; the asymmetry is a compatibility constraint, not a modeling choice.

3. **Documentation.** `docs/coverage.md` (the mass-properties coverage note) and the
   README mass-properties mention are updated to state the unit trap and point at the
   semantics records, so the mm⁵/unit-density fact is discoverable without reading a
   probe.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_part_inertia_semantics` (real geometry): a published `Box` reports a
  `matrix_of_inertia_semantics` equal to
  `{"units": "mm^5", "density": "unit (geometric)", "about": "part centroid",
  "axes": "world at placed pose"}`, sitting beside an **unchanged** bare-list
  `matrix_of_inertia`.
- `test_mass_properties_semantics_paired`: `runner._mass_properties` emits the
  semantics key **iff** it emits `matrix_of_inertia`; a kernel-free object gets
  neither (extends the ADR 0015 no-kernel guarantee).
- `test_assembly_inertia_semantics_mass`: a density-weighted two-part assembly's
  `inertia` block reports `units == "g*mm^2"` and `density == "mass-weighted"`.
- `test_assembly_inertia_semantics_volume`: the same geometry without densities
  reports `units == "mm^5"` and `density == "unit (geometric)"`.
- Existing ADR 0015/0035/0036 tests continue to pass; `matrix_of_inertia` and
  `inertia.tensor` values are byte-for-byte unchanged (additive keys only).

## Consequences

- The mm⁵ / unit-density trap is now self-documenting in the JSON: a consumer reads
  `units`/`density`/`about`/`axes` instead of hard-coding tribal knowledge, and a URDF
  generator can assert the axes/units it expects before converting.
- `matrix_of_inertia` and `inertia.tensor` are untouched, so the arm project's pinning
  probes keep passing; the semantics are strictly new information.
- The per-part `axes: "world at placed pose"` makes the D-007 addendum (a rotated part
  yields off-diagonals in world axes, not body axes) explicit, so a consumer knows it
  must rotate the tensor into the link body frame itself.
- Semantics are static descriptors of a fixed convention, not computed per run, so they
  add negligible cost and cannot drift from the numbers they describe.

## After Action Report

The red state failed as predicted: `matrix_of_inertia_semantics` and the assembly
`inertia.units`/`density` fields were absent, so the three positive tests raised
`KeyError`, while the pairing/no-kernel test passed trivially (asserting absence).

Implementation was purely additive, as designed. `runner._mass_properties` emits the
semantics sibling inside the same `try` guard that produces `matrix_of_inertia`, so
the two keys are always emitted together or not at all — the kernel-free path still
yields exactly `{volume, area}`. `inspector._assembly_center_of_mass` folds
`units`/`density` into the existing ADR 0036 `inertia` block from the same
`all_have_density` decision that produced the tensor. The base `matrix_of_inertia`
list and `inertia.tensor` values are byte-for-byte unchanged, verified by the full
ADR 0015/0035/0036 suite passing untouched (the arm project's pinning probes rely on
exactly this).

Documentation landed in `docs/coverage.md` (coverage note for ADRs 0035–0037,
including the D-007 trap statement) and a new README "Mass properties, density, and
inertia" section that states the mm⁵ / unit-density / world-axes convention and points
at the semantics records.

No design changes were needed during implementation. Full suite: 181 passed
(177 prior + 4 new), no regressions.

# ADR 0036: Assembly-Level Inertia Aggregation

## Status

Accepted for implementation.

## Context

Deficiency **D-006** (`docs/specs/arm-deficiencies.md`) observes that
`spatial["assembly"]` carries only `{center_of_mass, mass, weighting, part_count}`
(ADR 0015). Per-part inertia and per-part placements are present, but there is no
aggregate inertia tensor for the whole assembly. The downstream arm project needs
one per link for URDF `<inertial>` blocks; today it must recompose the tensor
itself with the parallel-axis theorem.

The raw material to aggregate already lives on each `spatial.json` object:

* `mass_properties.matrix_of_inertia` — a 3×3 tensor from build123d. **Critically
  (D-007):** it is a *unit-density geometric* second-moment in **mm⁵**, taken about
  the **part centroid**, and expressed in **world axes at the placed pose** (ADR
  0015 computes it on the *placed* object, so no residual body-frame rotation is
  hidden in it).
* `mass_properties.center_of_mass` — the part centroid in world coordinates.
* `mass_properties.volume` and, after ADR 0035, `metadata.density` — the weights.

Because the per-part tensors are already in a common world frame, aggregating them
needs **no rotation** — only scaling by density and a parallel-axis *translation* to
the assembly center of mass. This is the key simplification and the reason the
aggregation is exact and pure-Python (the evaluator/inspector never touch a CAD
kernel).

## Decision

Extend `inspector._assembly_center_of_mass` (already the sole owner of the
`assembly` record and of the `weighting` decision) to also compose an aggregate
inertia tensor about the assembly center of mass, reusing the *same* qualifying
part list so the inertia and the center of mass are always mutually consistent.

**Per-part contribution.** For each qualifying part with geometric tensor `G_i`
(mm⁵, about its centroid, world axes), centroid `c_i`, volume `V_i`, and optional
density `ρ_i`, let `d_i = c_i − com` be the offset from the assembly center of mass.
The contribution to the aggregate tensor about the assembly CoM is

```
  I_i  =  w_i · G_i  +  m_i · ( |d_i|² · E₃  −  d_i ⊗ d_i )
```

where `E₃` is the 3×3 identity and `d_i ⊗ d_i` the outer product. The first term is
the part's own spin inertia scaled to physical units; the second is the standard
parallel-axis transfer of a point of "mass" `m_i` from `c_i` to the assembly CoM. No
rotation appears because every `G_i` is already in world axes.

**Weighting, consistent with ADR 0015's `weighting` field.**

* When **every** contributing part has a positive density (`weighting == "mass"`):
  `w_i = ρ_i` and `m_i = ρ_i · V_i`. Units: mm⁵ · g/mm³ = **g·mm²** — a true mass
  moment of inertia.
* Otherwise (`weighting == "volume"`): `w_i = 1` and `m_i = V_i`. The result is a
  **unit-density geometric** second moment of *volume* about the volume centroid, in
  **mm⁵** — the exact inertial analogue of the volume-weighted center of mass, so the
  two degrade together and never mix units.

**Completeness guard.** Every contributing part must expose a valid 3×3
`matrix_of_inertia`. If any qualifying part lacks one, the aggregate would silently
omit that part's spin term and under-report, so the `inertia` block is **omitted**
entirely (the center-of-mass record is still emitted). This never fails the run;
it just declines to publish a number it cannot compute correctly.

**Record shape.** When computable, the `assembly` record gains:

```json
"inertia": {
  "tensor": [[Ixx, Ixy, Ixz], [Ixy, Iyy, Iyz], [Ixz, Iyz, Izz]],
  "about": "assembly center of mass",
  "axes": "world"
}
```

The tensor is symmetric by construction. Its **units** are deliberately left to the
sibling ADR 0037 semantics record (`g·mm²` vs `mm⁵`) so this ADR introduces the
number and 0037 makes it self-describing; until then the `weighting` field already
tells a reader which regime produced it.

## Success Criteria

Written so the new tests fail before implementation and pass after. All use
hand-computable geometry with closed-form answers, not shape checks.

- `test_assembly_inertia_two_boxes_mass`: two identical 10×10×10 boxes
  (`V = 1000`, geometric `G = V/12·(100+100) = 16666.67` on each diagonal),
  density `2.0`, centroids at `x = ±20`, so the assembly CoM is the origin. The
  aggregate must match the closed form
  `Ixx = 2·ρ·G = 66666.67`,
  `Iyy = Izz = 2·(ρ·G + m·d²) = 2·(33333.33 + 2000·400) = 1,666,666.67` g·mm²,
  with zero off-diagonals, to a tight float tolerance. `weighting == "mass"`.
- `test_assembly_inertia_volume_weighted_units`: the same geometry with **no**
  densities yields the geometric aggregate (`Ixx = 2·G = 33333.33`,
  `Iyy = Izz = 2·(G + V·d²) = 2·(16666.67 + 1000·400) = 833333.33` mm⁵) and
  `weighting == "volume"`.
- `test_assembly_inertia_parallel_axis_offset`: with the CoM **not** at the origin
  (asymmetric placement) the parallel-axis math still matches the hand computation
  about the true assembly CoM — pins that the transfer uses `c_i − com`, not `c_i`.
- `test_assembly_inertia_omitted_without_part_tensors`: if a contributing part has
  no `matrix_of_inertia`, the `assembly` record still has a `center_of_mass` but no
  `inertia` key.
- `test_assembly_inertia_real_geometry` (build123d): a real two-`Box` assembly emits
  a symmetric positive-definite `inertia.tensor` whose diagonal matches the box
  closed form within a small relative tolerance — guards the world-axes /
  about-centroid assumption against real kernel output.
- Existing ADR 0015/0035 assembly tests continue to pass (the CoM record is only
  added to).

## Consequences

- A multi-part run now advertises a ready-to-use assembly inertia tensor; the arm's
  URDF layer reads it directly instead of re-deriving parallel-axis transfers.
- The aggregate inherits ADR 0015's weighting semantics exactly, so `weighting`
  simultaneously labels the center of mass *and* the inertia regime — an agent never
  has to guess whether the tensor is a mass moment (g·mm²) or a geometric one (mm⁵).
- The completeness guard means the field's presence is itself information: an
  `inertia` block is always a fully-composed tensor, never a partial sum.
- Rotation is intentionally absent. This is correct only because per-part tensors are
  in world axes (ADR 0015 computes them on the placed object). ADR 0037 records that
  invariant in machine-readable form so a future change to the per-part frame cannot
  silently invalidate this aggregation.

## After Action Report

The red state failed as predicted: the `assembly` record had no `inertia` key, so
every tensor test raised `KeyError: 'inertia'`.

Implementation extended `inspector._assembly_center_of_mass` in place: the
qualifying-part tuple grew a fourth slot for the (coerced, optional)
`matrix_of_inertia`, and a new pure helper `_aggregate_inertia` composes the tensor
by density-scaling plus parallel-axis translation to the assembly CoM. The
completeness guard (`all(inertia is not None ...)`) keeps the field's presence
meaningful — an `inertia` block is always a complete sum, never a partial one.

The closed-form tests pinned the arithmetic hard: two 10×10×10 boxes at density 2.0,
centroids ±20, gave exactly `Ixx = 66666.67`, `Iyy = Izz = 1,666,666.67` g·mm² with
zero off-diagonals; the volume-weighted variant gave the mm⁵ geometric aggregate;
the offset test confirmed the transfer uses `c_i − com` (boxes at x=0 and x=40 about
a CoM at x=20 reproduce the symmetric numbers); and the real-`Box` test matched the
10×20×30 closed form within 1e-3 relative — confirming the world-axes /
about-centroid assumption holds against live kernel output with no rotation applied.

No design changes were needed during implementation. Full suite: 177 passed
(172 prior + 5 new), no regressions.

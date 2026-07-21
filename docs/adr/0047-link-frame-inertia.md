# ADR 0047: Link-Frame Inertia Emission

## Status

Accepted for implementation.

## Context

Deficiency **D-017** (`docs/specs/arm-deficiencies.md`) is the sharp edge left
behind by ADR 0037. That ADR made the per-part `matrix_of_inertia` *honest*: its
sibling `matrix_of_inertia_semantics` now declares the tensor to be a
**unit-density geometric second moment in mm⁵**, taken **about the part
centroid**, and expressed in **world axes at the placed pose**. Honest, but still
inconvenient for the one consumer that matters here — a URDF generator. URDF's
`<inertial>` block wants a **mass** moment of inertia, in a **link (body) frame**,
about the part's own origin/centroid. To get there from what cadx emits a
consumer must do *two* independent corrections:

1. **Density scaling** — multiply the mm⁵ geometric tensor by the material
   density to reach mass units (this fact is already surfaced: ADR 0035/0008 emit
   the resolved density on `metadata.density`, and a resolved `mass_properties.mass`).
2. **Frame rotation** — rotate the world-axes tensor back into the part's local
   body frame with `Rᵀ · I · R`, where `R` is the placement orientation matrix.
   A part published with a 30° rotation about Z shows off-diagonal products of
   inertia in world axes that are pure artifacts of the pose, not the geometry.

Two manual steps means two chances to get it wrong, and the rotation step in
particular is easy to invert the wrong way. The rotation is also the step a
consumer *cannot* cheaply re-derive without re-reading the placement and
composing a rotation matrix — exactly the kind of tribal-knowledge derivation
ADR 0037 set out to remove.

`matrix_of_inertia` itself cannot be renamed, re-valued, or rotated in place: the
arm project's probe suite pins it (ADR 0037 records this), and ADR 0037's
world-axes semantics are now a load-bearing contract. The fix must therefore be
**purely additive** — a *new sibling* tensor already rotated into the link frame,
riding beside the existing one with its own semantics record.

## Decision

`runner._mass_properties` gains, alongside `matrix_of_inertia` and its semantics,
a new sibling block **`inertia_link_frame`**: the same second-moment tensor
rotated into the part's **local body (link) frame**, still about the part
centroid, still unit-density mm⁵. Nothing existing is renamed, moved, or
re-valued.

### 1. The link-frame tensor (always, for real geometry)

Let `I` be `matrix_of_inertia` (world axes at the placed pose, about centroid)
and `R` be the **body→world rotation** of the part's placement (its columns are
the part's local basis vectors expressed in world coordinates). Because
`matrix_of_inertia` is computed on the *placed* object, it already equals
`R · I_body · Rᵀ`. Inverting that rotation recovers the body-frame tensor:

```
inertia_link_frame = Rᵀ · I · R
```

- For an **identity-placed** part `R` is the identity, so
  `inertia_link_frame == matrix_of_inertia` exactly.
- For a part published with a pure rotation, `inertia_link_frame` equals the
  tensor of the *identity-placed twin* — the pose-induced off-diagonals vanish
  (rotation invariance of the body-frame inertia).
- **Translation never affects either tensor**: both are taken about the part
  centroid, so a pure translation leaves `R` at identity and moves nothing.

`R` is extracted from the placement `Location` with build123d itself — a
rotation-only `Location((0,0,0), placement.orientation)` maps each local unit
basis vector to its world image, and those images are the columns of `R`. This
reuses the same Euler-XYZ-degrees convention (ADR 0014) the rest of the pipeline
uses, so no independent rotation-matrix math can drift from build123d's.

### 2. Its own semantics sibling

Mirroring ADR 0037, the tensor rides beside a machine-readable descriptor:

```json
"inertia_link_frame_semantics": {
  "units": "mm^5",
  "density": "unit (geometric)",
  "about": "part centroid",
  "axes": "link (body) frame"
}
```

The only field that differs from `matrix_of_inertia_semantics` is
`axes: "link (body) frame"` (versus `"world at placed pose"`) — precisely the
correction this ADR performs. The two keys are emitted together under the same
guard, so a consumer gets both the tensor and its declared frame or neither.

### 3. Mass-scaled variant when density resolves (`inertia_link_frame_mass`)

When ADR 0035 resolves a density for the part (a `metadata.density` plus a
positive `volume`, yielding `mass_properties.mass`), cadx **also** emits the
mass-weighted link-frame tensor, so a URDF consumer needs *no* manual correction
at all:

```
inertia_link_frame_mass = density · inertia_link_frame     # g·mm²
```

with semantics `{"units": "g*mm^2", "density": "mass-weighted",
"about": "part centroid", "axes": "link (body) frame"}`. Units check:
`(g/mm³) · mm⁵ = g·mm²`.

**Where it is computed — and why not in `matrix_of_inertia`'s guard.** Density is
resolved *after* `_mass_properties`, in ADR 0035's `_apply_material_density`
pass, so the mass variant cannot be produced where `inertia_link_frame` is. It is
produced by a **new, dedicated** `_apply_link_frame_inertia_mass(published)` pass
that runs immediately after `_apply_material_density` in the worker. This keeps
the edit off ADR 0035's function entirely (a deliberate separation of concerns:
one pass owns density resolution, the other owns the derived mass tensor) and
leaves runs that declare no material **byte-identical** to before this ADR — no
density means no `inertia_link_frame_mass` key, exactly as no density already
means no `mass`.

### Schema-pinning tests

No existing test asserts an exact key *set* over `mass_properties` (verified by
survey), so the additive siblings break nothing. Tests that read
`matrix_of_inertia` still find the unchanged bare 3×3 list beside the new keys.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_link_frame_equals_world_at_identity` (kernel): an identity-placed `Box`
  reports `inertia_link_frame == matrix_of_inertia` element-wise, with
  `inertia_link_frame_semantics.axes == "link (body) frame"`.
- `test_link_frame_rotation_invariance` (kernel): a `Box` published with a 30°
  rotation about Z has `inertia_link_frame` equal (to ~1e-6) to the tensor of its
  identity-placed twin, and its off-diagonal terms are ~0, even though
  `matrix_of_inertia` (world axes) shows nonzero products of inertia.
- `test_link_frame_translation_invariance` (kernel): a purely translated `Box`
  has both `matrix_of_inertia` and `inertia_link_frame` equal to the untranslated
  twin (both about centroid).
- `test_link_frame_semantics_paired` (kernel-free): `_mass_properties(object())`
  emits neither `inertia_link_frame` nor its semantics (extends the ADR 0015/0037
  no-kernel guarantee); a real tensor emits both together.
- `test_link_frame_mass_when_density_resolves` (kernel): a `Box` with a resolved
  density emits `inertia_link_frame_mass == density · inertia_link_frame` in
  `g*mm^2` with mass-weighted semantics; a part with no density emits no such key.
- Existing ADR 0015/0035/0036/0037 tests pass unchanged: `matrix_of_inertia`,
  its semantics, `inertia.tensor`, and `mass` are byte-for-byte identical.

## Consequences

- A URDF generator reads `inertia_link_frame_mass` directly into
  `<inertial><inertia>` with zero manual correction; a density-free run reads
  `inertia_link_frame` and applies one scalar density multiply (no rotation). The
  D-017 two-step trap collapses to at most one obvious multiply.
- The rotation is performed once, by cadx, with build123d's own convention, so it
  cannot be inverted the wrong way downstream.
- All ADR 0037 pinned fields are untouched; the world-axes tensor and its
  semantics remain the exact contract the arm probes pin.
- `inertia_link_frame` costs one 3×3×3 multiply per part; `inertia_link_frame_mass`
  costs a scalar scale only when a density already resolved. Negligible.

## After Action Report

_To be completed after implementation and one downstream consumption cycle._

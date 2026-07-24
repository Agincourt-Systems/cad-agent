# Inertia for robotics consumers

`spatial.json` ships **three** inertia tensors per part. Each is honest about
itself, but only one is correct for a URDF `<inertial>`. This page names that
field and shows the exact recipe. Read it before you feed any tensor into a
dynamics or URDF tool.

**Use `inertia_link_frame_mass`.** It is body-frame, mass-scaled, in `g*mm^2`,
about the part center of mass. The other two are geometric (unit-density) and one
of them is in world axes at the placed pose — using that one for a rotated part
gives wrong cross-terms. See "The trap" below.

## The three tensors

| Field | Density | Units | Frame | About |
| --- | --- | --- | --- | --- |
| `matrix_of_inertia` | unit (geometric) | `mm^5` | **world axes, placed pose** | centroid |
| `inertia_link_frame` | unit (geometric) | `mm^5` | link (body) frame | centroid |
| `inertia_link_frame_mass` | mass-weighted | `g*mm^2` | link (body) frame | centroid |

Each tensor carries a sibling `*_semantics` record that states these facts.
`inertia_link_frame_mass_semantics` also carries a `recommended_use` pointer back
to this page.

`inertia_link_frame_mass` is emitted only when a density resolved — either from an
explicit `density=` or from a material name (see the mass/density section of the
README). Without a density, only the two geometric tensors ship.

## Worked example — a 30-degree-rotated part

Publish a `Box(10, 20, 30)` (mm), aluminum, rotated 30 degrees about world Z:

```python
from build123d import Box, Location
from cadx import publish

def build(params):
    publish("link", Box(10, 20, 30), role="final",
            density=0.0027,  # g/mm^3 (6061-T6); see the density unit contract
            placement=Location((0, 0, 0), (0, 0, 30)))
```

The part's mass is `volume x density = 6000 x 0.0027 = 16.2 g`. The three tensors
come out as follows.

`matrix_of_inertia` — world axes at the placed pose. The 30-degree rotation leaks
into off-diagonal products of inertia. **These are a pose artifact, not geometry.**

```
[ 612500.0   64951.9        0.0 ]
[  64951.9  537500.0        0.0 ]   (mm^5, unit density)
[      0.0       0.0   250000.0 ]
```

`inertia_link_frame` — the same geometry rotated back into the body frame. The
off-diagonals vanish because the box is symmetric in its own frame.

```
[ 650000.0        0.0        0.0 ]
[      0.0   500000.0        0.0 ]   (mm^5, unit density)
[      0.0        0.0   250000.0 ]
```

`inertia_link_frame_mass` — the body-frame tensor scaled by density
(`0.0027 x inertia_link_frame`). **This is the robotics field.**

```
[ 1755.0     0.0     0.0 ]
[    0.0  1350.0     0.0 ]   (g*mm^2)
[    0.0     0.0   675.0 ]
```

Check the diagonal by hand: `Ixx = (mass/12)(y^2 + z^2) = (16.2/12)(20^2 + 30^2)
= 1.35 x 1300 = 1755 g*mm^2`. It matches.

## The trap

For an **axis-aligned** part all three tensors have the same off-diagonal
structure, so a careless consumer never notices which one it grabbed. For a
**rotated** part `matrix_of_inertia` grows off-diagonal terms (`64951.9` above)
that describe the *pose*, not the body. Drop that tensor into a URDF `<inertial>`
— whose inertia is expressed about the CoM in the link frame — and the link
tumbles wrong. `inertia_link_frame_mass` has already removed the pose rotation, so
it is safe.

## URDF `<inertial>` recipe

1. **`<mass>`** — read `mass_properties.mass` (grams). Convert to kilograms:
   `kg = g / 1000`. Here `16.2 g = 0.0162 kg`.
2. **`<origin>`** — set `xyz` to the part center of mass
   (`mass_properties.center_of_mass`), expressed in the link frame, and `rpy` to
   `0 0 0`. The tensor is already about the centroid in the body frame, so no
   extra rotation is needed. In this example the CoM is the box center.
3. **`<inertia>`** — read `inertia_link_frame_mass`. Convert `g*mm^2` to
   `kg*m^2`: multiply by `1e-9` (`1 g = 1e-3 kg`, `1 mm^2 = 1e-6 m^2`). Here
   `ixx = 1755 g*mm^2 = 1.755e-6 kg*m^2`, `iyy = 1.350e-6`, `izz = 6.75e-7`, and
   `ixy = ixz = iyz = 0`.

```xml
<inertial>
  <origin xyz="0 0 0" rpy="0 0 0"/>
  <mass value="0.0162"/>
  <inertia ixx="1.755e-6" ixy="0" ixz="0"
           iyy="1.350e-6" iyz="0" izz="6.75e-7"/>
</inertial>
```

## The density-scaling story

The `g*mm^2` label on `inertia_link_frame_mass` is only correct when the density
that scaled it was in **g/mm^3**. That is the cadx contract: an explicit
`density=` is g/mm^3, and the built-in material table resolves to g/mm^3. If your
density is in another unit, declare it with `publish(..., density_unit="kg/mm^3")`
so cadx normalizes it to g/mm^3 before scaling — otherwise every mass and
mass-inertia label is wrong by the unit ratio (a silent 1000x for kg/mm^3). See
the density unit contract in the README and ADR 0055.

# Bug report — `cadx render` crashes on any ≥2-part assembly (ADR-0023 regression path)

*Reported 2026-07-03 while rendering the Chupacabra v2 airframe
(`../chupacabra-configuration`, 7 published solids) with the ADR-0023
combined-assembly build.  cad-agent HEAD `21541d9`, build123d 0.10,
OCP/OCCT via the standard wheel.*

## Symptom

`cadx render <run_dir>` aborts with an OCP exception for a run that has
two or more real published parts:

```
File ".../cadx/renderer.py", line 435, in _render_step_artifacts
    _write_projection_svg(section_shape, target, origin)
File ".../cadx/renderer.py", line 365, in _write_projection_svg
    visible, hidden = item.project_to_viewport(viewport_origin)
File ".../build123d/topology/one_d.py", line 1315, in project_to_viewport
    camera_coordinate_system.SetYDirection(viewport_up.to_dir())
OCP.OCP.Standard.Standard_ConstructionError: gp_Dir::Crossed() - result vector has zero norm
```

Because `_render_step_artifacts` runs *before* `_render_raster_artifacts`
in `render_run`, the whole command dies here: **no contact sheet, no
ortho SVGs, no sections, no shaded iso** are written.  A single-part run
renders fine (the pre-ADR-0023 behaviour), so this only bites the exact
case ADR-0023 set out to support.

## Reproduction

```bash
cd ../chupacabra-configuration
PYTHONPATH=../cad-agent/src python -m cadx.cli run design.py --params params.yaml   # 7 solids, ok
PYTHONPATH=../cad-agent/src python -m cadx.cli render artifacts/runs/<id>            # crashes
```

Minimal isolation against the exported assembly:

```python
from build123d import import_step, Plane
shp = import_step("artifacts/runs/<id>/assembly.step")   # a Compound of 7 solids

sec = shp.intersect(Plane.XY)
print(len(list(sec.faces())))        # -> 0   (EMPTY, despite 5 children crossing XY)

# every child *does* section correctly:
for s in shp.solids():
    si = s.intersect(Plane.XY)
    print(len(list(si.faces())) if si is not None else None)
# -> 1,1,1,1,None,1,None   (body + 2 wings + 2 horizontal fins; 2 vertical fins correctly empty)

sec.project_to_viewport((0, 0, 100)) # -> Standard_ConstructionError: gp_Dir::Crossed() zero norm
```

## Root cause — two layered defects

**Defect A (primary): `Compound.intersect(Plane)` returns an empty shape
for this STEP-imported multi-solid assembly**, even though the per-solid
intersections are non-empty (table above: 5 of 7 children yield a face,
the compound yields an empty — non-`None` — shape with zero faces/edges
and a zero bbox).  `_render_step_artifacts` feeds the ADR-0023 combined
`assembly.step` (a `Compound`) straight into `shape.intersect(plane)`, so
the section views of this assembly come back empty and then crash (Defect
B).

The trigger is geometry-dependent, **not** "all compounds": minimal
2-box compounds — in-memory, STEP round-tripped, and mixed
intersecting/non-intersecting — all section correctly via
`Compound.intersect`.  Only the real 7-solid airframe (thin swept plate
solids + an ogive solid of revolution, some children in the section
plane and some not) reproduces the empty result.  So this reads as an
OCCT/build123d `Compound.intersect(Plane)` robustness issue on certain
multi-solid STEP compounds rather than a blanket failure.  Either way the
fix is to **not depend on `Compound.intersect`** for sectioning — fold
over `shape.solids()` and recombine, which is correct for every case and
sidesteps the quirk.

**Defect B (crash trigger): `_write_projection_svg` is not empty-safe.**
`project_to_viewport(viewport_origin)` uses the default
`viewport_up=(0,0,1)`.  When the shape is empty its look point collapses
to the origin, so for the two viewports whose origin lies on the +Z axis —
`top` and `section_xy`, both `(0,0,100)` — the look direction is
`(0,0,-1)`, exactly (anti)parallel to `viewport_up`, and OCCT's
`gp_Dir::Crossed()` throws "zero norm".  A non-empty shape dodges this
because its off-axis geometric center tilts the look vector; an empty
shape does not.  The existing `if section_shape is None: continue` guard
in the section loop does **not** catch this, because build123d returns a
non-`None` *empty* compound, not `None`.  (The `section_yz` view at
`(100,0,0)` is also empty but does *not* crash — its up vector is not
collinear with the X-axis camera — which is why only `section_xy` throws.)

## Impact

- `cadx render` is unusable for any assembly (≥2 real parts) — the
  headline ADR-0023 use case.
- The combined **export** half of ADR-0023 works: `assembly.step/stl/glb`
  are written correctly, and `_render_raster_artifacts` shades the
  assembly STL into a correct multi-part `shaded_iso.png` when invoked
  directly (verified — full airframe with wings + cruciform fins renders).
  Only the SVG-projection stage is broken, and it happens to run first.

## Suggested fixes (either of A-fix + the B-fix guard)

1. **Section per-solid, not per-compound** (fixes the empty sections):
   in `_render_step_artifacts`, replace `shape.intersect(plane)` with a
   fold over `shape.solids()`:
   ```python
   pieces = [s.intersect(plane) for s in shape.solids()]
   pieces = [p for p in pieces if p is not None and (p.faces() or p.edges())]
   section_shape = Compound(children=pieces) if pieces else None
   ```
   (Applies equally if `import_step` yields a single solid — `.solids()`
   returns the one solid.)

2. **Make `_write_projection_svg` empty-safe** (removes the crash class
   regardless of A): skip shapes with no faces *and* no edges, e.g.
   ```python
   items = [shape] if hasattr(shape, "project_to_viewport") else list(shape)
   items = [it for it in items if it is not None and (it.faces() or it.edges())]
   if not items:
       return   # nothing to project; don't emit an empty/invalid SVG
   ```
   and tighten the section-loop guard from `is None` to also treat an
   empty section as "skip".

3. **(Defensive) choose a non-collinear `viewport_up` per viewport** so a
   top/`section_xy` camera on +Z passes e.g. `viewport_up=(0,1,0)` to
   `project_to_viewport`. This hardens against any future empty/degenerate
   shape at the Z-axis cameras even if A and B regress.

A regression test should assert that `render_run` on a ≥2-solid run writes
a non-empty contact sheet, a `shaded_iso.png`, and section SVGs (or
cleanly omits empty sections) without raising.

## Evidence artifacts

- `../chupacabra-configuration/artifacts/runs/<id>/assembly.step` — the
  7-solid compound that reproduces Defect A.
- `../chupacabra-configuration/artifacts/runs/<id>/views/shaded_iso.png` —
  proof the raster/export half of ADR-0023 is correct (produced by calling
  `_render_raster_artifacts` directly, bypassing the crashing SVG stage).

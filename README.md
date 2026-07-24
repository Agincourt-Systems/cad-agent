# CAD Agent Harness

`cadx` is a local CAD-as-code harness for coding agents. It lets an agent edit
ordinary `build123d` Python files, run them, collect CAD artifacts, inspect
spatial facts, render deterministic visual summaries, and evaluate requirement
checks with minimal human input.

The first implementation is intentionally CLI-first. MCP and richer browser
viewer integrations can wrap the same run artifacts once the local contract is
stable.

![Shaded render of a four-part mated assembly: base plate, tower, revolute-posed swing arm, and pivot boss](docs/images/cad-output.png)

![Materials showcase: aluminum plate, carbon-fiber frame, steel barrel with translucent glass lens, black-oxide bolts, and brass knob](docs/images/materials-output.png)

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[cad,render,test]
cadx init
cadx run design.py --params params.yaml
cadx inspect artifacts/runs/0001
cadx render artifacts/runs/0001
cadx shots artifacts/runs/0001 --views iso,side,top,front
cadx evaluate artifacts/runs/0001 --requirements requirements.yaml
cadx loop design.py --params params.yaml --requirements requirements.yaml --agent-command "<agent command>"
```

`inspect` is optional before `evaluate`: `run` already writes `spatial.json`
with the detected features, and `evaluate` reads it directly. Run `inspect`
when you want to (re)write and read `spatial.json` yourself — for direct
viewing or before `compare`, which consumes the inspected form.

`--artifact-root` names the directory that *holds* the numbered run
directories; each run is written directly under it as a zero-padded number.
`--artifact-root out` therefore produces `out/0001/`, `out/0002/`, … (not
`out/runs/NNNN/`). The default root is `artifacts/runs`, so the default run
directory is `artifacts/runs/0001`. In all cases the `artifact_dir` field in
the `run` JSON is the authoritative path — read it rather than reconstructing
the layout.

`cadx shots` renders shaded PNG screenshots of the run's primary STL (the
combined `assembly.stl` when a run has ≥2 parts, else the single part) from
several named cameras — `iso`, `top`, `side`, `front`, `rear` — writing
`shaded_<camera>.png` (default views `iso,side,top`; `--out DIR` to redirect).
It is the shared, on-demand version of a multi-angle screenshot script;
`render` still produces only the isometric shaded view in its contact sheet.

If `build123d` is not installed, `cadx run` still starts and reports a clear
dependency error when the design source imports `build123d`.

## Artifact Contract

Each successful run creates:

- `source_snapshot.py`
- `params.resolved.yaml`
- CAD exports when the runtime supports them
- `spatial.json`
- `diagnostics.json`
- `checks.json` after evaluation
- `report.md` after evaluation
- `views/contact.png` after rendering
- `views/shaded_iso.png` after rendering
- `artifacts/loop.json` after loop orchestration

The CAD export set varies with the run's part count and geometry, so predict
it from the run rather than assuming a fixed list:

- A **combined `assembly.step`/`.stl`/`.glb`** is emitted only when a run has
  **≥2 real parts**; a single-part run exports just that one part.
- A **lone constant-thickness box auto-flattens to a DXF** flat pattern even
  when it was not published as a sheet-metal part (this is the ADR 0013
  flat-pattern path recognizing a plate-like solid). Multi-part and
  non-plate runs do not.

`diagnostics.json` lists every export the run actually wrote; treat it as the
authoritative artifact index.

The harness is designed so text-only agents can reason from JSON and
multimodal agents can inspect the rendered contact sheet and shaded CAD image.

## Requirement check types

`requirements.yaml` drives autonomous convergence. `cadx evaluate` supports these
check `type`s:

- `dimension` — a scalar bounding box / size / distance against `equals` or a
  `min`/`max` range with `tolerance`.
- `topology` — expected solids/faces/edges/vertices counts.
- `clearance` — minimum gap between two objects; AABB by default, exact BREP with
  `method: exact`.
- `feature_count` — number of features of a `kind` (e.g. `cylindrical_hole`).
- `feature_dimension` — a property across features selected by kind.
- `feature_alignment` — two features (holes) are coaxial and diameter-compatible.
- `interference` — no pair of solids overlaps (BREP intersection volume).
- `center_of_mass` — a part or assembly center of mass at a target point/region.
- `stability` — the projected center of mass lies inside a support polygon.
- `bend` — sheet-metal bend count/angle/radius/direction from `bends.json`.
- `manufacturability` — laser/sheet DFM rules (min hole diameter, slot width,
  web, hole-to-edge, bend radius, hole-to-bend, min flange) parameterized by
  thickness. Each rule's limit is an absolute `min` (mm) or `factor * thickness`.
  - `min_bend_radius` defaults to a **conservative** `1.0 * thickness` floor. A
    fab house often forms tighter than that generic floor (e.g. SendCutSend
    verifies a 0.81 mm inside radius on 2.29 mm 5052, ~0.35 t). A design working
    to a shop's **published radius table** must pass an explicit `min` to admit
    the verified tighter radius (`min: 0` disables the rule). The default is
    never silently weakened, so a design that declares no verified radius stays
    protected.
  - `min_flange` checks every flange of a bent part (each outer leg and each
    interior web of a U-channel) against a minimum formable length, defaulting
    to `4.0 * thickness`. It reads the published `bend` features (flat-pattern
    frame) and the developed `blank_length`; without a blank length only the
    interior webs are checked.
  - Parts published through `publish_sheet_metal` carry a `sheet` metadata
    block (`blank_length`, `blank_width`, `thickness`), so `min_flange`,
    `hole_to_edge` with `frame: flat`, and every thickness-relative limit
    resolve those facts automatically; explicit check/rule parameters always
    win. Hand-published features on non-sheet parts still need the explicit
    parameters.
  - Holes authored via the `holes=` API compose cleanly with the sheet DFM
    rules. The authored hole is bored out of the folded solid, so STEP
    auto-detection would otherwise re-observe it as a phantom folded-frame
    `cylindrical_boss` and false-positive `hole_to_bend` / `hole_to_edge`; the
    inspector now suppresses that duplicate re-detection (marking the authored
    hole `confirmed_by_detection`), so `hole_to_bend` runs coherently on a
    hole-bearing bent part with no extra scoping. A check may also set
    `exclude_detected: true` to drop every auto-detected feature and gate only
    authored/published geometry.
- `parametric` — re-run the design across multiple parameter sets and aggregate
  ordinary sub-checks (tolerance/stack-up studies); see `cadx sweep`.

`symmetry` and `visual` are listed in the specification but are **not yet
implemented**; a design that uses them will get a clear `ValueError`. Any other
unknown `type` likewise raises a `ValueError` naming the type rather than silently
passing.

A `parametric` example:

```yaml
checks:
  - id: width_stackup
    type: parametric
    params:
      - {width: 38}
      - {width: 42}
    checks:
      - id: width_in_range
        type: dimension
        target: obj.plate.bbox.size.x
        min: 36
        max: 44
```

## Manufacturing package

`publish_flat` / `publish_sheet_metal` emit SendCutSend-clean DXF flat patterns
(with bend lines on a `bend` layer and a `bends.json` table), `publish_part_meta`
plus `cadx bom <run_dir>` produce a deterministic `bom.csv`/`bom.json`, and every
export record carries explicit millimeter units.

### Sheet-metal parts (`bend` / `bend_chain`)

A bent bracket is described once and yields a folded 3-D solid plus a single flat
blank. Holes and cutouts are placed in **flange-local** frames and unfolded into
the blank, so they reach the DXF cut layer, the folded solid, and the DFM
features together:

```python
from cadx.sheetmetal import bend
from cadx import publish_sheet_metal, mate
from build123d import Location

part = bend(40, 25, angle_deg=90, inside_radius=3, k_factor=0.44,
            thickness=3, width=30, direction="up",
            holes=[{"flange": 0, "u": 20, "v": 0, "diameter": 6}])  # round hole on flange 0
# A folded part is a normal placed/mated part (a clevis is folded sheet):
publish_sheet_metal("clevis", part,
                    mate=mate(to="base", kind="revolute",
                              anchor=Location((0, 0, 0)), target=Location((30, 0, 6)), angle=35))
```

A hole `u` runs from its flange's leading edge; `v` runs across the width from the
centreline. A hole that would straddle a bend line, or run off the blank, is a
clear error.

**Coordinate frames.** The flat pattern is width-centred (`x in [0, developed]`,
`y in [-width/2, +width/2]`). The folded solid puts the base flange's lower fibre
on `z = 0`, the length along `+x`, and by default is extruded across its width to
`y in [-width, 0]` (to one side of `y = 0`, *not* centred). Pass
`center_width=True` to `bend` / `bend_chain` to shift only the folded solid to the
width-centred `y in [-width/2, +width/2]` frame. `placement` / `mate` move the
folded solid but never the flat pattern, bend table, or bend/hole features — those
stay in flat-pattern coordinates.

## Assemblies

Position parts in a shared frame with `publish(label, obj,
placement=Location(...))`, or state the mating intent and let the harness
derive the transform:

```python
publish("base", base, role="final")
publish("tower", tower,
        mate=mate(to="base", anchor=Location((0, 0, -15)), target=Location((20, 5, 3))))
# or, with build123d RigidJoints defined on the shapes:
publish("tower", tower, mate=mate(to="base", joint="plug", target_joint="socket"))
```

Mates resolve to ordinary placements (chains allowed, any publish order), the
declared relationship is recorded on the spatial object, and cross-part checks
(`clearance`, `interference`, `feature_alignment`, `center_of_mass`,
`stability`) verify the assembled geometry. Multi-part runs additionally export
a combined `assembly.step`/`.stl`/`.glb` and render the whole assembly on the
contact sheet.

Kinematic kinds pose the mate about the target frame's local Z axis:
`kind="revolute"` (`angle` degrees), `kind="prismatic"` (`travel` mm), or
`kind="cylindrical"` (both), with optional `angle_range`/`travel_range` limits
that flag out-of-range poses. Feed the pose from `params` and sweep it with a
`parametric` check to verify the whole motion envelope:

```python
publish("lid", lid, mate=mate(to="box", kind="revolute",
        anchor=Location((0, -40, 0)), target=Location((0, 40, 20), (90, 0, 0)),
        angle=params.get("lid_angle", 0), angle_range=(0, 110)))
```

```yaml
- id: lid_swing_clear
  type: parametric
  params: [{lid_angle: 0}, {lid_angle: 55}, {lid_angle: 110}]
  checks:
    - id: no_collision
      type: interference
      tolerance: 0.001
```

## Materials (screenshot appearance)

Shaded renders (`cadx render`, `cadx shots`) color each part by its declared
appearance — looks only, no simulation. Declare it where the part is
published, or let BOM metadata imply it:

```python
publish("frame", frame, appearance="carbon_fiber")
publish("bolt", bolt, appearance="black_oxide")
publish("lens", lens, appearance="glass")          # translucent
publish("mount", mount, appearance="#ff8800")      # any hex color
publish_part_meta("plate", material="6061-T6 Aluminum")  # implies "aluminum"
```

Presets live in `cadx.materials.MATERIALS`: steel, stainless_steel, aluminum,
titanium, brass, copper, gold, zinc_plated, black_oxide,
anodized_black/red/blue, carbon_fiber (two-tone weave), glass (translucent),
rubber, and plastic_&lt;color&gt; variants. Undeclared parts cycle a distinct
default palette so bare assemblies still render with distinguishable parts;
unknown names fall back to the palette with an `appearance_unknown` warning in
the render manifest.

## Mass properties, density, and inertia

A declared `material` also implies **physics** (independent of appearance). A known
alloy name — via `publish(..., material="6061-T6")` or `publish_part_meta` — resolves
to a density from the built-in table in `cadx.density` (g/mm³: 6061-T6, 5052-H32, 304
stainless, 1018/mild steel, brass, ABS, PLA, PETG, Ti-6Al-4V), and each part's
`mass_properties` then carries `mass = volume × density` (grams). An explicit
`publish(..., density=<g/mm³>)` always overrides the table; an unknown material sets
`metadata.density_resolved = false` and guesses nothing. `metadata.density_source`
records whether a density was author-supplied (`"explicit"`) or looked up
(`"material:<name>"`).

Inertia carries a **unit trap** worth stating plainly, so each tensor ships a
semantics record beside it:

- Per part, `mass_properties.matrix_of_inertia` is a **unit-density geometric** second
  moment in **mm⁵**, about the **part centroid**, in **world axes at the placed pose**
  (density is *not* applied; a rotated part shows world-axis off-diagonals). The
  sibling `matrix_of_inertia_semantics` states exactly this. To get a mass moment,
  multiply by density and convert units.
- Per assembly (≥1 qualifying part), `spatial.json` `assembly.inertia` aggregates the
  per-part tensors about the assembly center of mass by the parallel-axis theorem (no
  rotation needed — the parts are already in world axes). Its `units`/`density` fields
  are `g*mm^2` / `mass-weighted` when every part has a density, else `mm^5` /
  `unit (geometric)`, matching the `assembly.weighting` field.

Screenshot lighting is steerable per `cadx shots` invocation:
`--light camera` front-lights each view with its own camera (the one-flag fix
for dark side/rear views), and `--light X,Y,Z` sets an explicit direction —
slightly off the camera axis (e.g. `0.3,1,0.5` for the side view) gives a
softer look than pure front light, which maximizes specular on camera-facing
faces. The resolved light vector is recorded on every shot for
reproducibility; `cadx render` keeps the fixed legacy light so diagnostic
contact sheets stay comparable across runs.

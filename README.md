# CAD Agent Harness

`cadx` is a local CAD-as-code harness for coding agents. It lets an agent edit
ordinary `build123d` Python files, run them, collect CAD artifacts, inspect
spatial facts, render deterministic visual summaries, and evaluate requirement
checks with minimal human input.

The first implementation is intentionally CLI-first. MCP and richer browser
viewer integrations can wrap the same run artifacts once the local contract is
stable.

![Shaded CAD output](docs/images/cad-output.png)

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[cad,render,test]
cadx init
cadx run design.py --params params.yaml
cadx inspect artifacts/runs/0001
cadx render artifacts/runs/0001
cadx evaluate artifacts/runs/0001 --requirements requirements.yaml
cadx loop design.py --params params.yaml --requirements requirements.yaml --agent-command "<agent command>"
```

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
  web, hole-to-edge, bend radius, hole-to-bend) parameterized by thickness.
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

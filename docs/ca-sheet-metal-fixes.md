# `cad-agent` (`cadx`) — Deficiency Spec & Fix Plan

**Audience:** a follow-up coding agent tasked with improving
[`torchhound/cad-agent`](https://github.com/torchhound/cad-agent) so it fully supports a
**laser-cut sheet-metal → SendCutSend** workflow (and multi-part assemblies) like this
SRM test stand.

**Harness reviewed:** commit **`b5d4a0a7e98b41ed014bfdc333b778ad16e55d79`**
("Implement ADR 0012 feature deduplication", 2026-06-09), `build123d 0.11.0`.

**How to use this doc:** each deficiency has an ID, severity, file:line **evidence**,
**why it matters**, a concrete **proposed fix** (with `build123d` API pointers), and
**acceptance criteria** written as TDD-style tests. Implement in `cad-agent`'s own repo,
following *its* `AGENTS.md` (strict red-green TDD, ADR-per-feature, total coverage).
Suggested ADR order mirrors the priority list in §1.

> Scope note: `cadx` is a genuinely capable `build123d` harness (run/inspect/render/
> evaluate/compare/loop; STEP/STL/GLB export; dimension/topology/clearance/feature
> checks; automatic feature detection). These are *additive* gaps for sheet-metal +
> assembly + manufacturing-package use, not defects in what exists.

---

## 1. Priority summary

| ID | Severity | Title | Blocks |
|----|----------|-------|--------|
| **D1** | **Critical** | No DXF / flat-pattern export in the artifact pipeline | SendCutSend ordering |
| **D2** | High | No sheet-metal bend / flat-pattern unfold | bent brackets, bend service |
| **D3** | High | No assembly/joint model or cross-part hole-alignment check | multi-part correctness |
| **D4** | Medium | No BOM / manufacturing-package artifact | ordering & costing |
| **D5** | Medium | Inspector omits center-of-mass / inertia | stability/CoG checks |
| **D6** | Medium | No laser/sheet manufacturability checks (DFM) | SendCutSend yield |
| **D7** | Low | Contact-sheet panels are placeholders; no dimensioned drawings | review ergonomics |
| **D8** | Low | Several `requirements.yaml` check types unimplemented | spec completeness |
| **D9** | Low/Info | Export unit metadata not set explicitly | import-scale safety |

---

## D1 — No DXF / flat-pattern export *(Critical)*

**Evidence.** `src/cadx/runner.py`, `_export_build123d_object()` imports and uses only
3D exporters:
```python
from build123d import export_gltf, export_step, export_stl   # ~line 119
for extension, exporter, kwargs in [
    ("step", export_step, {}),
    ("stl",  export_stl,  {}),
    ("glb",  export_gltf, {"binary": True}),                  # ~lines 131-135
]:
```
No DXF anywhere; `diagnostics["exports"]` therefore never contains a `dxf` record, and
nothing downstream (`inspector`, `renderer`, `evaluate`) looks for one.

**Why it matters.** SendCutSend (and most laser/waterjet shops) consume **2D vector**
files — **DXF** (or DWG/AI/EPS/SVG) — for flat parts. STEP/STL/GLB are 3D and not
directly cuttable. Without DXF the harness cannot close the loop to fabrication for the
target vendor. `build123d 0.11` already ships `ExportDXF` and `ExportSVG`.

**Proposed fix.**
1. Add an explicit flat-publication API in `registry.py`:
   `publish_flat(label, profile, *, layer="cut", thickness_mm=None, **meta)` where
   `profile` is a `build123d` `Sketch`/`Face`/planar `Compound` (the cut outline +
   interior cutouts).
2. Add `publish_flat`-aware export in `runner.py`: for each flat publication, write
   `<label>.dxf` to the run dir using `ExportDXF` and append an
   `{"label","format":"dxf","path","layer","thickness_mm"}` record to
   `diagnostics["exports"]`.
   ```python
   from build123d import ExportDXF
   dxf = ExportDXF(unit=Unit.MM, line_weight=...)   # set units explicitly (see D9)
   dxf.add_layer("cut", color=...)
   dxf.add_shape(profile, layer="cut")
   dxf.write(run_dir / f"{label}.dxf")
   ```
3. **Auto-flatten convenience:** for a published *solid* part of uniform thickness that
   was extruded from a single planar sketch, derive the flat profile automatically
   (largest planar face → outline + inner wires) so existing single-sketch parts get a
   DXF "for free". Keep it advisory: if the part isn't a constant-thickness prism, skip
   with a warning rather than failing the run.
4. Export must be **SendCutSend-clean:** millimeter units, a single closed outer wire,
   closed interior wires for cutouts, no duplicate/overlapping segments, no text/dims on
   the cut layer.

**Acceptance (tests).**
- `test_dxf_export_basic`: a published plate (rectangle + 2 holes) yields
  `<label>.dxf`; parse it (e.g. `ezdxf`) and assert 1 closed outer polyline/contour and
  2 circles whose diameters match params within tolerance.
- `test_dxf_units_mm`: header `$INSUNITS == 4` (mm); bbox in mm equals the model bbox.
- `test_dxf_export_recorded`: `diagnostics["exports"]` contains a `format:"dxf"` record
  with a resolvable path.
- `test_dxf_autoflatten_skips_nonprismatic`: a non-uniform-thickness solid produces a
  warning, not a hard failure.

---

## D2 — No sheet-metal bend / flat-pattern unfold *(High)*

**Evidence.** No module references bends, K-factor, bend allowance, or unfolding;
`build123d` has no native sheet-metal unfolder. Flat export (D1) handles *flat* parts
only.

**Why it matters.** Many brackets are cheaper/stronger as a **single bent part** than as
bolted flats. SendCutSend offers bending, but needs a **flat pattern with correct
developed length** (bend allowance via material **K-factor**) plus **bend lines** on a
separate layer and bend direction/angle notes.

**Proposed fix.** A lightweight sheet-metal helper (does **not** require a full kernel
unfolder):
- Model parts as flat strips joined by explicit **bend operations**:
  `bend(flat_sketch, line, angle_deg, inside_radius, k_factor)`.
- Compute developed length: `BA = (π/180)·angle·(r + K·t)`; place flanges accordingly.
- Produce **both** representations: the folded **3D** solid (for assembly/clearance
  checks) **and** the **flat DXF** with the outline on `cut` and bend lines on a `bend`
  layer, plus a machine-readable bend table (`bends.json`: line, angle, dir, radius).

**Acceptance (tests).**
- `test_lbracket_developed_length`: flange A + flange B + bend allowance equals the flat
  pattern length within tolerance for a known K/t/r/angle.
- `test_bend_line_layer`: the flat DXF has exactly one entity on the `bend` layer at the
  bend location; folded-3D bbox matches the expected folded envelope.
- `test_bend_table_emitted`: `bends.json` lists angle/direction/radius per bend.

---

## D3 — No assembly/joint model or cross-part hole-alignment check *(High)*

**Evidence.** `registry.publish()` stores independent objects with no transform/parent;
`evaluate.py` clearance is pairwise **AABB** (`_aabb_clearance`) or **exact BREP**
(`_check_exact_clearance` via `Shape.distance`). There is no mate/joint and no check
that a hole in part A is **coaxial** with a hole in part B.

**Why it matters.** A test stand is many bolted plates. The dominant failure mode is
**misaligned bolt patterns / interference** between parts placed in different local
frames. The harness can't currently assert "these two parts bolt together".

**Proposed fix.**
1. Let `publish(label, obj, role=..., placement=Location(...))` record a placement so
   parts live in a common assembly frame; expose the placed shapes to inspect/evaluate.
2. New check `feature_alignment`: given two feature selectors (e.g. holes by id/kind),
   assert their **axes are collinear** and diameters compatible within tolerance.
3. New check `interference` (assembly-wide): flag any pair of solids with BREP distance
   `== 0` / negative clearance (overlap), reported with the offending labels.

**Acceptance (tests).**
- `test_alignment_pass_fail`: two plates with a shared 4-hole pattern pass; offset one
  plate by > tolerance → fail with the specific feature ids.
- `test_interference_detects_overlap`: two overlapping solids → `interference` fails;
  separated → passes.
- `test_placement_roundtrip`: a placed part's reported bbox reflects its `Location`.

---

## D4 — No BOM / manufacturing-package artifact *(Medium)*

**Evidence.** No BOM command or part-metadata registry; `cli.py` subcommands are
`init/run/inspect/render/evaluate/compare/loop` only.

**Why it matters.** Ordering needs a structured parts list: vendor, material, thickness,
finish, qty, source/part number, unit cost — plus per-plate **area** (SendCutSend quotes
on material + area + cut length) and fastener counts.

**Proposed fix.**
- `publish_part_meta(label, *, vendor, material, thickness_mm, finish, qty, source_url,
  unit_cost, part_number, process)` stored in the registry.
- `cadx bom <run_dir>` aggregates metadata + auto-derived facts (plate area from the
  flat profile, bounding box, hole count) into deterministic `bom.csv` / `bom.json`,
  grouped by vendor with totals.

**Acceptance (tests).**
- `test_bom_rows`: each published part appears with material/thickness/qty and a
  computed area; totals match the sum.
- `test_bom_deterministic`: re-running yields byte-identical `bom.csv`.

---

## D5 — Inspector omits center-of-mass / inertia *(Medium)*

**Evidence.** `runner.py:_normalize_published()` populates
`mass_properties = {"volume", "area"}` only — no `center_of_mass`, despite the spec
(`docs/agentic-cad-harness-spec.md`) showing `center_of_mass` and inertia.

**Why it matters.** Tip-over stability and locating the load cell under the assembly CoG
require **center of mass** (per part and assembly-aggregated with material densities).

**Proposed fix.**
- Compute `center_of_mass` (e.g. `Shape.center(CenterOf.MASS)`) and add to
  `mass_properties`; optionally principal inertia if cheap.
- Assembly aggregation: mass-weighted CoM across placed parts using a density per
  material (from `publish_part_meta`, D4).
- New checks: `center_of_mass` (target point/region) and `stability` (CoM projection
  within the support polygon / minimum tip angle).

**Acceptance (tests).**
- `test_com_prism`: a known prism reports CoM at its centroid within tolerance.
- `test_assembly_com_weighted`: two-part assembly CoM equals the mass-weighted mean.
- `test_stability_check`: CoM inside support polygon → pass; outside → fail.

---

## D6 — No laser/sheet manufacturability (DFM) checks *(Medium)*

**Evidence.** `evaluate.py` implements `dimension/topology/clearance/feature_count/
feature_dimension`; the spec lists a `manufacturability` type that is **not**
implemented (`_evaluate_check` raises on unknown types).

**Why it matters.** SendCutSend rejects/【flags】 parts violating DFM: min hole Ø ≥
material thickness, min slot width, min web/bridge between cutouts, hole/slot-to-edge ≥
~1× thickness, min bend radius and hole-to-bend distance (with D2). Catching these in
the harness avoids quote rejections and rework.

**Proposed fix.** Implement `manufacturability` with sheet/laser rules parameterized by
material + thickness; evaluate against detected/published features; report the offending
feature id and the violated rule, as warn or fail.

**Acceptance (tests).**
- `test_min_hole_diameter`: hole Ø < thickness → fail naming the feature; ≥ → pass.
- `test_hole_to_edge`: hole closer than 1× t to an edge → fail.

---

## D7 — Contact-sheet panels are placeholders; no dimensioned drawings *(Low)*

**Evidence.** `renderer.py:_draw_with_pillow()` draws scaled **rectangles** as
stand-ins, not real geometry. True geometry exists only in the separate `shaded_iso.png`
(STL software rasterizer) and the per-view **SVG** hidden-line projections.

**Why it matters.** Reviewing fab parts is easier with real silhouettes + hole locations
and a dimensioned drawing (DXF/PDF) per part. Low priority because usable artifacts
already exist.

**Proposed fix.** Compose the real SVG projections + `shaded_iso` into the contact
sheet; optionally emit a dimensioned 2D drawing per flat part (leveraging D1's profile).

**Acceptance (tests).** `test_contact_sheet_uses_real_views` asserts the contact sheet
references/embeds the rendered SVG/PNG rather than placeholder rectangles.

---

## D8 — Unimplemented `requirements.yaml` check types *(Low)*

**Evidence.** Spec §Requirement Schema lists `symmetry`, `visual`, `parametric`,
`manufacturability`; only the first five types are routed in `_evaluate_check`.

**Why it matters.** Completeness vs. the documented contract. `parametric` (run the same
checks across parameter sets) is genuinely useful for tolerance/stack-up studies on a
test stand.

**Proposed fix.** Implement incrementally as needed (`manufacturability` via D6;
`parametric` via a sweep runner; `symmetry`/`visual` later). Until then, **document**
the unsupported types so a design author isn't surprised by a raised error.

**Acceptance (tests).** Per type as implemented; minimally, an unknown type yields a
clear, documented error (already true) and the README lists supported types.

---

## D9 — Export unit metadata not set explicitly *(Low/Info)*

**Evidence.** Exporters are called with defaults (`export_step(obj, target)` etc.); DXF
(D1) must set units to avoid scale ambiguity on import.

**Why it matters.** A DXF without `$INSUNITS` (or a STEP without explicit units) can
import at the wrong scale — catastrophic for a cut part.

**Proposed fix.** Set units explicitly on every exporter (DXF `$INSUNITS = mm`, STEP
unit = mm) and record `units` in each export entry of `diagnostics["exports"]`.

**Acceptance (tests).** `test_export_units`: DXF header reports mm; export records carry
`"units":"mm"`.

---

## 2. Notes for the implementer

- Follow `cad-agent`'s `AGENTS.md`: write the failing test first, keep total coverage,
  one ADR per deficiency (or a small group), branch-per-ADR, update its `LOG.md`/
  `MEMORY.md`/`LESSONS.md`.
- Keep the **machine contract stable**: additions should extend `diagnostics["exports"]`
  and `spatial.json`/`checks.json` without breaking existing keys (the harness's tests
  and any downstream agents depend on them).
- D1 + D9 unblock this SRM project directly; D3 + D5 + D6 raise confidence on the
  multi-part assembly; D2 + D4 are quality-of-life for bent parts and ordering.
- In **this** repo we work around D1 by emitting DXF from `design.py` via `ExportDXF`;
  that workaround can be deleted once D1 lands upstream.

# ADR 0013: DXF Flat-Pattern Export and Explicit Export Units

## Status

Accepted for implementation.

## Context

`docs/ca-sheet-metal-fixes.md` deficiency **D1 (Critical)** observes that the
artifact pipeline exports only 3D formats. `runner._export_build123d_object()`
iterated `[("step", export_step), ("stl", export_stl), ("glb", export_gltf)]`
and never produced a 2D vector file. SendCutSend and essentially every laser /
waterjet shop consume **2D DXF** for flat parts; STEP/STL/GLB are not directly
cuttable. Without a DXF the harness cannot close the loop to fabrication for the
target vendor, which is the entire point of the SRM sheet-metal workflow.

Deficiency **D9 (Low/Info)** observes that exporters were called with defaults
and that no `units` field was recorded on export records. A DXF without
`$INSUNITS` (or a STEP imported at an ambiguous scale) can import at the wrong
size, which is catastrophic for a cut part. The two deficiencies are coupled: the
new DXF path must set millimeter units explicitly, and the existing 3D exporters
should record their units in the same machine contract so a downstream agent can
trust scale on every artifact.

build123d 0.10.0 ships `ExportDXF` and the necessary topology accessors
(`Face.outer_wire()`, `Face.inner_wires()`, `Shape.faces()`, `face.geom_type`,
`face.normal_at()`). (The spec doc references 0.11, but both `ExportDXF` and
`ExportSVG` already exist in 0.10.0 — verified.) This ADR adds an explicit
flat-publication API plus an auto-flatten convenience so existing single-sketch
parts get a DXF for free, without disturbing the stable diagnostics/spatial
machine contract that ADRs 0001–0012 established.

## Decision

### 1. New public authoring API: `publish_flat`

Added to `src/cadx/registry.py`, re-exported from `src/cadx/__init__.py`:

```python
def publish_flat(label, profile, *, layer="cut", thickness_mm=None, **meta) -> None
```

`profile` is a build123d `Sketch`, `Face`, or planar `Compound` carrying the
outer cut outline and any interior cutout wires. Flats are stored in a new
registry channel `_FLATS` and appear in `snapshot_registry()` under a new key
`"flats"`. The existing `"published"` and `"features"` keys are unchanged, so the
contract is extended, never broken.

### 2. DXF export in runner/worker

`runner._export_flats(flats, run_dir)` writes one `<label>.dxf` per flat through
a shared writer:

```python
def _write_dxf(profile, layer, target, *, extra_layers=None):
    dxf = ExportDXF(unit=Unit.MM)
    dxf.add_layer(layer)
    dxf.add_shape(profile, layer)
    ...
    dxf.write(target)
```

`_write_dxf` takes an optional `extra_layers` argument (a list of
`(layer_name, shapes)`) so the sheet-metal work in ADR 0016 can place bend lines
on a separate layer through this single writer instead of forking a second DXF
code path.

The profile is first relocated into the global XY plane by
`runner._flatten_to_xy`, because `ExportDXF` projects geometry onto the XY plane
and **silently drops points that lie off it** (it emits only a stderr warning,
`"N points found outside the XY plane"`). `_flatten_to_xy` localizes through the
profile's own largest planar face (`Plane(face).to_local_coords(profile)`), which
is correct for a flat profile modeled on *any* plane, with a pure z-translation
fallback for already-axis-aligned profiles. Each successful write appends an
export record:

```json
{"label": ..., "format": "dxf", "path": ..., "layer": ..., "thickness_mm": ..., "units": "mm"}
```

Export failures are **warnings**, not hard failures, matching
`_export_build123d_object`: a malformed profile yields a
`{"type": "flat_export_failed", ...}` warning and the run still succeeds.

### 3. Auto-flatten convenience for prismatic solids

For each `publish()`-ed real solid, `runner._auto_flat_profile(obj)` attempts to
derive a flat profile automatically so existing parts get a DXF without any code
change. The detector is a volume invariant rather than a fragile face heuristic:

- Find the largest-area planar face `F` and the parallel, equal-area planar face
  opposite it; `thickness = |(center_opp - center_F) · normal_F|`.
- Accept as a constant-thickness prism only when
  `abs(volume - F.area * thickness) <= 1e-3 * volume`. A stepped or tapered solid
  fails this test and is **skipped**.
- On acceptance, build `Face(F.outer_wire(), F.inner_wires())`, localize to z=0,
  and DXF-export it on the `cut` layer with `thickness_mm = thickness`.
- On rejection, append a `{"type": "autoflatten_skipped", ...}` warning and skip.
  The run never fails because a part is not prismatic.

`_auto_export_flats` skips synthetic dict publications, any label already
published as an explicit flat, and any entry carrying an internal sheet-metal
`flat` key (ADR 0016 writes that part's bend DXF elsewhere — the guard is written
here up front so the two export paths never race to write the same `<label>.dxf`).

### 4. D9: explicit units on every exporter and every record

`_export_build123d_object` now passes `unit=Unit.MM` to `export_step` and
`export_gltf` (both accept it; verified) and records `"units": "mm"` on every
record. `export_stl` is unchanged because STL is a unitless format and
`export_stl` has **no** `unit` parameter (verified); its record still carries
`"units": "mm"` to document the modeling unit. No existing key (`label`,
`format`, `path`) is renamed or removed.

## Success Criteria

Written so the new tests fail before implementation (the symbols and records do
not yet exist) and pass after.

- `test_dxf_export_basic`: a `publish_flat` 40×20 plate with two Ø4 holes
  produces `<label>.dxf`; parsed with `ezdxf` it has exactly two `CIRCLE`
  entities of radius 2 (±0.01) and a single closed outer contour.
- `test_dxf_units_mm`: the DXF header `$INSUNITS == 4` and the model bbox in mm
  equals the plate's planar size within tolerance.
- `test_dxf_export_recorded`: `diagnostics["exports"]` contains a `format=="dxf"`
  record whose `path` resolves and whose `units` is `"mm"`.
- `test_dxf_autoflatten_emits_for_prism`: a plain `publish()`-ed prism yields a
  `format=="dxf"` record via auto-flatten.
- `test_dxf_autoflatten_skips_nonprismatic`: a stepped solid produces an
  `autoflatten_skipped` warning, the run status stays `"ok"`, and no DXF is
  emitted for it.
- `test_export_units`: the DXF header reports mm **and** every export record
  (step/stl/glb/dxf) carries `"units": "mm"`.
- Existing ADR 0001–0012 tests continue to pass; `snapshot_registry()` still
  returns `published`/`features` unchanged and additionally returns `flats`.

## Consequences

- The artifact pipeline now emits SendCutSend-clean DXF: millimeter units, a
  closed outer wire, closed interior wires for cutouts, on a dedicated `cut`
  layer, with no text or dimensions. This unblocks the SRM fabrication loop.
- Single-sketch prismatic parts get a flat DXF automatically; non-prismatic parts
  degrade gracefully to a warning, preserving the harness rule that inspection is
  always useful even when an export cannot be produced.
- Every export record now advertises its units, so downstream agents can assert
  scale on STEP/STL/GLB/DXF uniformly. The `units` key is additive.
- STL remains unitless at the format level; the recorded `"units": "mm"` documents
  the modeling unit. This is a deliberate, documented limitation, not a silent
  scale claim.
- The prism detector is a volume invariant, so it is robust to face ordering and
  to interior cutouts (a holed plate is still a prism), at the cost of rejecting
  genuinely flat parts that are not single-thickness (correct: those need ADR
  0016 bend handling, not a naive flatten).

## After Action Report

The red state failed as predicted: importing `publish_flat` raised `ImportError`
before implementation, and no `format=="dxf"` records or `units` keys existed.

Two build123d realities shaped the implementation and were caught before they
could ship as silent bugs:

1. **`ExportDXF` silently drops off-plane geometry.** A Z-extruded part's broad
   face sits at `z = ±thickness/2`; handing it straight to `add_shape` emits only
   a stderr warning and drops the outer contour. The fix is `_flatten_to_xy`,
   which localizes the profile through its own plane
   (`Plane(face).to_local_coords`). This was verified to be necessary even for a
   simple X-extruded plate (broad-face normal `±X`), where a naive z-translation
   leaves the geometry off the XY plane.
2. **The outer boundary exports as separate `LINE` entities, not a single
   `LWPOLYLINE`.** The acceptance test therefore asserts "one closed loop" by
   checking that every `LINE` endpoint has vertex degree 2, rather than counting
   a polyline entity — a stricter "exactly one LWPOLYLINE" assertion would have
   been wrong.

The ADR 0002 integration test (`test_build123d_integration.py`) asserted the
export-format set was *exactly* `{step, stl, glb}`. Because a plain `Box` is a
constant-thickness prism, auto-flatten now (correctly) adds a `dxf`. The test was
updated to the intentionally extended contract `{step, stl, glb, dxf}` and
strengthened to assert the new `units` field and the `box.dxf` artifact. This is
a deliberate, recorded contract extension, not a regression.

`export_stl` has no `unit` parameter (STL is format-unitless), so its record
documents `"units": "mm"` for the modeling unit without claiming the file encodes
one. STEP and glTF take `unit=Unit.MM` directly.

An adversarial review of the diff (before merge) drove three hardening changes:

1. **Planarity guard against silent garbage.** The original `_flatten_to_xy`
   could still emit a degenerate DXF for an input it could not bring onto the XY
   plane — a solid handed to `publish_flat`, or a face-less wire on a tilted
   plane that the z-only fallback cannot rotate. `_flatten_to_xy` now derives the
   plane for wire-like profiles (`_reference_face` builds a `Face` to recover the
   plane) and, as a final guard, raises when the flattened geometry still lies
   off `z=0`, so the caller degrades to a `flat_export_failed` warning instead of
   writing a silently-wrong cut file. Covered by
   `test_export_flats_rejects_nonplanar_profile_as_warning` and
   `test_flatten_to_xy_localizes_offplane_face`.
2. **Arc-aware closed-loop assertion.** A real SendCutSend plate is usually
   fillet-cornered and exports as `LINE + ARC` segments; the original test only
   counted `LINE` endpoints and would have wrongly failed a filleted outline. The
   check now includes `ARC` endpoints, with a dedicated
   `test_dxf_filleted_outline_is_closed` exercising the path.
3. **No spurious warning for non-solids.** A `publish_flat`-only design returns a
   sketch that the runner auto-publishes as `result`; auto-flatten now skips any
   object with no volume silently instead of emitting a confusing
   `autoflatten_skipped` for a shape that was never a prism. Verified live: a
   flat-only run reports `warnings: []`.

Coverage: the full success paths (explicit flat export, auto-flatten emit,
auto-flatten skip, units on every record, filleted outline) are exercised
end-to-end through the `cadx run` subprocess in `tests/test_dxf_export.py`; the
decision and failure branches (`flat_export_failed`, the planarity guard, prism
accept/reject, off-plane localization, `autoflatten_skipped`, explicit/
sheet-metal/non-solid skips) are exercised in process in
`tests/test_dxf_export_unit.py`; and the registry channel is covered without a
CAD kernel in `tests/test_flat_registry.py`. `_write_dxf`'s `extra_layers`
parameter and the `export_dependency_missing` branch are exercised by ADR 0016
and a kernel-less environment respectively. The full suite passed with 37 tests
(19 prior + 18 new), no regressions.

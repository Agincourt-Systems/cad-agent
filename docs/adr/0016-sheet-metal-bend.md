# ADR 0016: Sheet-Metal Bend and Flat-Pattern Unfold

## Status

Accepted for implementation.

## Context

Deficiency D2 in `docs/ca-sheet-metal-fixes.md` (High) observes that the harness
has no sheet-metal model: nothing references bends, K-factor, bend allowance, or
unfolding, and `build123d` ships no native sheet-metal unfolder. ADR 0013 (D1)
added flat-pattern DXF export via `publish_flat`, but that path handles
intrinsically *flat* parts only. Many test-stand brackets are cheaper and stiffer
as a single bent part than as bolted flats, and SendCutSend can bend them — but
only if the design supplies a flat pattern whose **developed length** is correct
(the flat blank is longer than the sum of the flange lengths by the bend
allowance), bend lines on a **separate layer**, and bend direction/angle/radius
notes for the press-brake operator.

The agent therefore needs two coupled artifacts from one bend description: a
folded **3D solid** (so the existing clearance/interference/CoM machinery can
reason about the part in its assembled pose) and a **flat DXF** with the outline
on the `cut` layer and bend lines on a `bend` layer, plus a machine-readable bend
table. This is a *lightweight* model — flat flanges joined by an explicit bend
operation — not a general BREP unfolder.

This ADR depends on ADR 0013 for the `_write_dxf` writer (with its `extra_layers`
hook), the `ExportDXF`/`Unit.MM` plumbing, the `units` convention on export
records, and the auto-flatten skip guard.

## Decision

Add a module `src/cadx/sheetmetal.py` exposing `bend(...)` and a `SheetMetalPart`
result, with `publish_sheet_metal` in `registry.py` (re-exported from `cadx`).

- `bend(flange_a, flange_b, *, angle_deg, inside_radius, k_factor, thickness, width, direction="up")`
  models a two-flange (one-bend) strip. `BA = (pi/180) * angle * (inside_radius +
  k_factor * thickness)`; the developed length is `flange_a + BA + flange_b`; the
  bend line sits at `flange_a + BA/2`. Inputs are validated (`width`/`thickness`
  positive, non-negative flanges, `direction` in `{up, down}`) so a bad call
  raises a clear `ValueError` rather than an opaque kernel error.
- `SheetMetalPart` is a frozen dataclass: `developed_length`, `folded` (Part),
  `flat_profile` (Sketch), `bend_lines` (list[Edge]), `bends` (list of
  `{line, angle, direction, inside_radius}`).
- `publish_sheet_metal(label, part, *, layer="cut", role="part", **meta)` stores
  the folded solid as the published object (so STEP/STL/GLB and spatial checks see
  the assembled pose) and attaches the flat pattern, bend lines, and bend table
  under an internal `flat` key. That key makes ADR 0013 auto-flatten skip the
  entry, so the bend DXF is never overwritten by a naive flatten of the folded
  solid.
- `runner._export_sheet_metal(entry, run_dir)` writes `<label>.dxf` by **reusing**
  `_write_dxf` with `extra_layers=[("bend", bend_lines)]`, and appends a
  `{format:"dxf", units:"mm", layers:["cut","bend"]}` record.
  `runner._export_bend_table(entries, run_dir)` writes **one** `bends.json` per run
  aggregating every bent part's rows (each tagged by `label`) and appends a single
  `{format:"bends", units:"mm"}` record. `worker.py` routes sheet-metal entries
  through both.
- `evaluate.py` gains a `bend` check `type` reading `bends.json` to assert a
  bend's `count`/`angle`/`inside_radius`/`direction`. Unknown types still raise.

The folded solid uses a deterministic rectilinear construction: for a 90° bend the
envelope is exactly `(flange_a + thickness) x width x flange_b` regardless of fold
direction (the standing wall is aligned to the base bottom for `up` or the base
top for `down` so the base thickness is absorbed either way). Non-right-angle
bends rotate flange B about the bend axis, giving a representative valid solid
rather than a closed-form envelope. This trades inside-radius corner fidelity for
a predictable envelope; the flat pattern's developed length carries the
bend-allowance correction that matters for fabrication.

## Success Criteria

Written so the tests in `tests/test_sheet_metal_bend.py` fail before and pass
after implementation:

- `test_lbracket_developed_length`: `bend(...)` on a concrete L-bracket reports
  `developed_length == flange_a + flange_b + (pi/180)*90*(r + k*t)` within 1e-6 and
  the `flat_profile` x-length equals it within 1e-3 (fails today: no
  `cadx.sheetmetal`).
- `test_bend_line_layer`: a `publish_sheet_metal` design produces `<label>.dxf`
  whose `bend` layer holds exactly one LINE at `flange_a + BA/2` while `cut` holds
  the four outline lines, and the folded `spatial.json` bbox equals
  `(flange_a + t, width, flange_b)` (fails today: no sheet-metal export).
- `test_bend_table_emitted`: the run directory contains `bends.json` with each
  bend's `angle`/`direction`/`inside_radius`, and `diagnostics["exports"]` has a
  `format:"bends"` record (fails today: never written).
- Existing ADR 0001–0015 tests continue to pass; the contract is extended only.

## Consequences

- A single `bend(...)` description yields both the assembly-ready folded solid and
  a fabrication-ready flat pattern, eliminating manual developed-length math.
- `bends.json` is a stable machine contract (`{line, angle, direction,
  inside_radius, label}` rows) that BOM (ADR 0017) and DFM (ADR 0018,
  hole-to-bend, minimum bend radius) can consume.
- The folded envelope deliberately omits the rounded inside-radius corner; parts
  needing exact corner geometry must model it explicitly.
- Multi-bend parts are representable by chaining flanges; this ADR ships the
  single-bend helper, but the per-bend-line export loop and the aggregated bend
  table generalize without a contract change.

## After Action Report

The red state failed as predicted: `cadx.sheetmetal` and the `bend` check did not
exist, so the three acceptance tests raised `ImportError` / unsupported-type and
the subprocess runs produced no `bracket.dxf` or `bends.json`.

The two-box folded model reproduces the documented 90° envelope exactly
(`(43, 30, 25)` for the test L-bracket), and `.rotate(Axis, angle)` gives a valid
folded solid for non-right-angle bends. The `_write_dxf` `extra_layers` hook from
ADR 0013 was reused directly — the bend layer is added through the single writer,
not a forked DXF path — and the ADR 0013 auto-flatten guard
(`entry.get("flat") is not None`) was confirmed to keep auto-flatten from writing
a second `<label>.dxf` over the bend DXF.

An adversarial review of the diff found one **blocker** and one correctness gap,
both now fixed and covered by new tests:

1. **`bends.json` clobber across parts.** The first implementation wrote
   `bends.json` once *per sheet-metal entry*, so a second bent part overwrote the
   first's table and emitted two `format:"bends"` records pointing at the same
   file. The bend table is now written once per run by `_export_bend_table`,
   aggregating every part's rows tagged by `label`, with a single export record
   (`test_two_sheet_metal_parts_share_one_bend_table`).
2. **`direction="down"` envelope.** The down wall originally extended below the
   base, making the Z extent `flange_b + thickness` instead of `flange_b`. The
   wall is now aligned to the base top for a down bend, so both directions give the
   same `(flange_a + t, width, flange_b)` envelope
   (`test_down_bend_envelope_matches_up`).

Input validation was added so a degenerate `bend()` call raises a clear
`ValueError` instead of an opaque OCP error
(`test_bend_rejects_invalid_inputs`). The non-90° path, the `bend` evaluate check
(pass/fail and missing-table), and the down direction are all covered. The full
suite passed with 69 tests (60 prior + 9 new), no regressions.

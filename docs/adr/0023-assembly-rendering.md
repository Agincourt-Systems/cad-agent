# ADR 0023: Combined Assembly Export and Multi-Part Rendering

## Status

Accepted for implementation.

## Context

ADR 0014 gave the harness a real assembly frame: parts are published with
placements, every derived fact observes placed geometry, and the evaluator
verifies cross-part properties (clearance, interference, hole alignment,
assembly center of mass). But two downstream stages are still single-part:

1. **No assembly-level artifact exists.** Each part exports its own
   STEP/STL/GLB; there is no single file containing the whole placed assembly.
   An agent (or human, or downstream viewer) that wants to look at the actual
   assembled design has to open N files and mentally compose them.
2. **Rendering shows only the first part.** `_render_step_artifacts`
   projects `exports[0]` (its docstring acknowledges the MVP single-part
   scope), `_render_raster_artifacts` shades the first STL, and the contact
   sheet's placeholder/summary read `objects[0]`. A multi-part run passes its
   numeric checks but *renders* as whichever part happened to export first —
   exactly the situation where a visual sanity check matters most.

The 2026-07-02 assembly-capability review identified this pair as the
highest-leverage, contract-additive next step (a joint-driven placement layer
is deliberately out of scope: it is a design-heavy feature deserving its own
ADR once needed).

## Decision

### Combined assembly export (runner/worker)

- After per-part exports, when a run has **two or more real (non-dict)
  published objects**, the worker exports the placed assembly as
  `assembly.step`, `assembly.stl`, and `assembly.glb` via a build123d
  `Compound(children=[placed...])` (verified on build123d 0.10: the compound
  bakes each part's placement and the STEP round-trips with one solid per
  part). Every published real shape is included regardless of role — fixtures
  and reference geometry are useful *visual* context; physical-role filtering
  stays where it has semantic weight (mass aggregation, BOM).
- Export records carry `label: "assembly"` and a new additive
  `assembly: true` flag so consumers can include or exclude the combined
  artifact deliberately instead of guessing from the label.
- Single-part and synthetic-only runs are byte-identical to today: no
  combined records, no new files.
- If a published part is itself labeled `"assembly"`, the combined export is
  skipped with an `assembly_export_skipped` warning rather than silently
  clobbering that part's `assembly.step` on disk (and shadowing its label in
  every label-keyed index). Export or compound failures degrade to the
  existing `export_failed` warning shape, never a run failure.

### Consumers of the flag

- **Inspector**: `_step_exports` excludes `assembly: true` records. Feeding
  the combined STEP to feature detection would re-detect every part's
  features a second time under a bogus `obj.assembly` source; the per-part
  exports remain the detection input.
- **Renderer**: both the SVG projection stage and the shaded-raster stage
  prefer the `assembly: true` export when present and fall back to
  `exports[0]` exactly as today. Projected views, sections, and the shaded
  isometric therefore show the whole placed assembly; view records carry
  `label: "assembly"` so the manifest says what was drawn.
- **Evaluate/BOM**: no changes. The collision skip guarantees the `assembly`
  key can never shadow a real part label, and both stages only ever look up
  real part labels.

### Contact sheet reflects the assembly

- The summary line and the placeholder rectangle currently read `objects[0]`.
  Both move to a union bounding box over all objects (`_bounds_union`), with
  the summary text extracted into a pure `_summary_line(spatial)` helper so
  the behavior is unit-testable without decoding PNG bytes. For a single
  object the union is that object's bbox and the summary text is unchanged;
  for multiple objects the line reports `assembly_bbox=X x Y x Z` instead of
  the first object's bbox/topology fields.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_multi_part_run_exports_combined_assembly` (e2e): a two-part placed
  design yields `assembly`-labeled step/stl/glb records flagged
  `assembly: true`; the STEP re-imports with 2 solids; auto-detected features
  are not duplicated (planar datums counted once per part face); and
  `cadx render` produces views whose records carry `label: "assembly"`.
  Fails today: no combined records exist.
- `test_inspect_ignores_assembly_export_for_feature_detection`: a fabricated
  run directory with per-part and assembly-flagged STEP exports detects
  features only from the per-part export. Fails today: the assembly STEP is
  ingested too, duplicating every feature.
- `test_render_prefers_assembly_export`: with both part and assembly-flagged
  STEP/STL exports present, view and raster records name the assembly
  artifact. Fails today: `exports[0]` (the part) is rendered.
- `test_summary_line_reports_assembly_extent`: `_summary_line` reports the
  union bbox for a two-object spatial and the existing single-object text
  verbatim for one object. Fails today: the helper does not exist.
- `test_part_labeled_assembly_skips_combined_export`: a two-part run with one
  part labeled `assembly` emits an `assembly_export_skipped` warning, keeps
  the part's own exports intact, and writes no flagged records. Fails today:
  no warning is emitted.
- `test_single_part_run_keeps_export_contract`: a single-part run produces no
  assembly-flagged records or `assembly.*` files. This is a non-regression
  pin — it passes before and after by design, guarding the "byte-identical
  for single parts" promise above.
- Existing ADR 0001–0022 tests continue to pass unchanged.

## Consequences

- Every multi-part run now yields one artifact (`assembly.step` /
  `assembly.glb`) that downstream viewers, shops, and agents can open to see
  the assembled design, and the rendered views/contact sheet finally show
  what the evaluator was checking — closing the gap where a misplaced part
  passed rendering review because only the first part was ever drawn.
- The `assembly: true` flag is the single dispatch point for future
  assembly-aware consumers (e.g. a per-part exploded contact sheet), avoiding
  label-string matching.
- Feature detection cost does not double on multi-part runs because the
  combined STEP is excluded from inspection.

## After Action Report

Red state confirmed as designed: 5 of the 6 new tests failed before
implementation (no flagged records, doubled feature detection when an
assembly-flagged export was fabricated, part-first rendering, missing
`_summary_line`, no collision warning); the single-part contract pin was green
before and after, as documented. A pre-implementation kernel probe validated
the core assumption on build123d 0.10: `Compound(children=[...])` bakes
placements, the combined STEP re-imports with one solid per part, and the
combined STL carries both parts' triangles.

Two test-authoring corrections during green (not code changes): export
records are cwd-relative by existing contract, so the e2e assertions resolve
them against the project dir; and algebra-mode `Box` is origin-centered, so
the placed-top bbox expectation is z 22.5, not 25.

Beyond the suite, the feature was exercised end-to-end on a base-plate +
tower demo: the shaded isometric and contact sheet now show both placed
parts (previously only the first part rendered), the summary line reads
`objects=2 | ... | assembly_bbox=60 x 40 x 36` (correct union extent), and
feature detection stayed at 12 planar datums with no `obj.assembly`
duplicates. Full suite `112 passed` (106 prior + 6 new), no regressions.

Follow-on candidates deliberately not taken here: per-part multi-view
contact-sheet panels (exploded views), and a joint-driven placement layer
deriving `Location`s from build123d joints — the latter remains the next
big assembly step if mating-intent authoring is wanted.

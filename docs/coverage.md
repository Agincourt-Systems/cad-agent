# Coverage Rationale

The first feature branch uses contract-level tests around the CLI because the
CLI is the agent-facing interface. Those tests exercise:

- Project initialization.
- Design execution through a subprocess.
- Publication and feature capture.
- Artifact creation.
- Spatial inspection.
- Contact-sheet rendering.
- Requirement evaluation, including a failing check.
- Run-to-run comparison.
- Real end-to-end agent convergence with a parameterized build123d model,
  failed evaluation report, corrected run, passing evaluation, and comparison.
- Subprocess worker isolation, including runtime error capture, stdout/stderr
  capture, and timeout diagnostics.
- Requirement dimension ranges, topology checks, and AABB clearance checks.
- STEP-backed cylindrical-hole feature detection.
- Loop orchestration that fails once, invokes an external fixer command, passes
  on the second iteration, and records max-iteration failure.
- Exact BREP clearance checks from STEP exports.
- Planar datum, cylindrical boss, and simple obround slot feature detection.
- Headless shaded raster rendering from STL exports.
- Deduplication of automatically detected features against explicit
  publications, including the full `cadx init` starter flow passing its own
  requirements and preservation of unmatched explicit features.
- DXF flat-pattern export (ADR 0013): explicit `publish_flat` profiles and
  auto-flattened constant-thickness prisms emit SendCutSend-clean millimeter
  DXF, parsed back with `ezdxf` for outline/hole/units assertions; the
  prism-detector accept/reject decisions, the `flat_export_failed` (including the
  non-planar-profile guard) and `autoflatten_skipped` warning paths, the
  explicit/sheet-metal/non-solid auto-flatten skips, off-plane profile
  localization, and the `units:"mm"` field on every step/stl/glb/dxf export
  record.
- Assembly placement and cross-part checks (ADR 0014): `publish(..., placement=)`
  applied to real shapes and synthetic dict bboxes with the placement recorded on
  the spatial object; `feature_alignment` best-pair matching (pass and fail
  naming feature ids and offset, plus a missing-selector error) on detected
  bolt-pattern holes and synthetic features; `interference` via BREP intersection
  volume (overlap vs separated) and the AABB fallback with a `between` subset; and
  the dedup guard that keeps coaxial holes on different stacked parts distinct.
- Center of mass, inertia, and stability (ADR 0015): per-part `center_of_mass`
  (off-origin centroid) and `matrix_of_inertia` on real solids plus the
  kernel-free omission path; mass- and volume-weighted assembly aggregation and
  the mixed-density volume fallback; the `center_of_mass` check (point, region,
  object target, missing-assembly error); and the `stability` check (inside,
  outside with negative margin, tip-angle gating, degenerate support).
- Sheet-metal bends (ADR 0016): `bend()` developed-length arithmetic and flat
  profile, the 90° two-box folded envelope for both up and down directions, the
  non-right-angle rotated solid, input validation, the combined cut+bend DXF
  (parsed with `ezdxf` for per-layer entity counts and the bend-line location),
  the single aggregated `bends.json` across multiple bent parts, and the `bend`
  evaluate check (pass, fail, missing-table).
- BOM / manufacturing package (ADR 0017): `publish_part_meta` recorded into
  diagnostics; `cadx bom` deriving area (recorded flat-pattern area, including the
  bent-part unfolded area rather than the folded flange), hole count, and bbox;
  vendor grouping and totals; byte-identical CSV determinism; the no-metadata /
  no-STEP degrade path; an explicit `qty=0`; and the orphan-metadata warning.
- Manufacturability / DFM (ADR 0018): the `manufacturability` check's six rules
  (min_hole_diameter, min_slot_width, hole_to_edge, min_web, min_bend_radius,
  hole_to_bend) with pass and fail branches, offending-feature naming, warn
  severity not failing, explicit-thickness/factor limit resolution, the
  bbox-derived thickness axis for in-plane slot edge clearance, the unsourced
  min_web guard, and the unknown-rule skip.
- Real-geometry contact sheet (ADR 0019): the ISO panel embeds the real shaded
  raster (asserted by pixel signature — non-white fraction, color diversity, and
  blue-dominant hue), the manifest records the embedded panel source, and a
  synthetic dict design with no STL falls back to the placeholder with a
  `source is None` record.
- Parametric sweeps and documented check types (ADR 0020): the `parametric` check
  re-running a design across parameter sets with all-pass and out-of-range-fail
  aggregation, the failed-set `run_status` branch, the `cadx sweep` subcommand,
  the unchanged unknown-type `ValueError`, and the README check-type contract.

Known gaps:

- The E2E loop covers parameter correction, but not autonomous source-code
  patching by an external agent process.
- The worker is process isolation, not a hardened OS sandbox.
- Default clearance checks use AABB distance; exact BREP clearance requires
  `method: exact` and per-object STEP exports.
- Automatic feature detection covers planar datums, cylindrical holes and
  bosses, and simple obround slots; other feature kinds still require explicit
  publication.
- `cadx loop` tests use a local fixer command, not a real coding-agent
  invocation.
- Slot detection is limited to paired partial cylindrical end faces.
- Shaded raster rendering is a simple software renderer, not photorealistic.
- The sheet-metal folded solid (ADR 0016) uses a rectilinear envelope that omits
  the rounded inside-radius corner; the developed flat length carries the
  bend-allowance correction that matters for fabrication.
- The DFM `min_bend_radius`/`hole_to_bend` rules (ADR 0018) act on explicitly
  published `kind="bend"` features; the standard sheet-metal flow records bends in
  `bends.json` (consumed by the `bend` check), so these DFM rules are inert until
  such a feature is published.
- The contact sheet (ADR 0019) embeds the real shaded raster in the ISO panel
  only; the orthographic/section panels stay placeholders because no SVG
  rasterizer is available headless (the real SVG projections exist as standalone
  artifacts). A dimensioned per-part drawing is future work.
- The `symmetry` and `visual` requirement check types remain unimplemented (ADR
  0020); they are documented as such and raise a clear `ValueError`.

The remaining gaps are acceptable at the end of ADR 0020 — which closes the
sheet-metal/SendCutSend deficiencies D1-D9 (DXF flat export, assembly placement
and alignment/interference, center of mass and stability, sheet-metal bends, BOM,
DFM, real contact sheet, and parametric sweeps) on top of the ADR 0001-0012
harness — because each is a documented approximation or a feature scoped to a
later need (`symmetry`/`visual`, hardened sandboxing, agent-driven source
patching, a non-convex stability footprint), not a correctness gap in what is
implemented.

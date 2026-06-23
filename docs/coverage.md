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
  prism-detector accept/reject decisions, the `flat_export_failed` and
  `autoflatten_skipped` warning paths, the explicit/sheet-metal auto-flatten
  skips, and the `units:"mm"` field on every step/stl/glb/dxf export record.

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

The remaining gaps are acceptable at the end of ADR 0011 because the harness
has a tested full command loop with exact CAD exports, structured spatial
facts, projection and section SVGs, shaded raster output, evaluation reports,
loop orchestration, and run-to-run comparison. Hardened sandboxing and
agent-driven source patching can be added as later improvements.

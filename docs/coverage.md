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

Known gaps:

- Runtime-error handling is covered by implementation review and diagnostics
  structure, but not by a dedicated failing-design test yet.
- Automatic CAD topology discovery is not implemented in ADR 0001; the MVP
  relies on explicit `publish` and `publish_feature` calls.
- Shaded raster rendering is not implemented; ADR 0003 covers deterministic
  hidden-line and section SVGs generated from the STEP artifact.
- The E2E loop covers parameter correction, but not autonomous source-code
  patching by an external agent process.
- The worker is process isolation, not a hardened OS sandbox.
- Clearance checks are based on bounding boxes, not exact BREP distance.
- Automatic feature detection currently handles cylindrical faces only.

The remaining gap is acceptable at the end of ADR 0004 because the harness now
has a tested full command loop with exact CAD exports, structured spatial facts,
real projection SVGs, real section SVGs, evaluation reports, and run-to-run
comparison. External agent orchestration and shaded raster rendering can be
added as later improvements.

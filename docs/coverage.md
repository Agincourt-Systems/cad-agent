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

Known gaps:

- Runtime-error handling is covered by implementation review and diagnostics
  structure, but not by a dedicated failing-design test yet.
- Automatic CAD topology discovery is not implemented in ADR 0001; the MVP
  relies on explicit `publish` and `publish_feature` calls.
- Shaded raster rendering is not implemented; ADR 0003 covers deterministic
  hidden-line and section SVGs generated from the STEP artifact.

The remaining gap is acceptable at the end of ADR 0003 because the agent now
receives exact CAD exports, structured spatial facts, real projection SVGs, and
real section SVGs. Shaded raster rendering can be added as a later visual
quality improvement.

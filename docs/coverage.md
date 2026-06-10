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
- Rendering still uses a metric placeholder contact sheet rather than real CAD
  view rasterization.

The remaining gap is acceptable at the end of ADR 0002 because the real
`build123d` export and spatial loop is now covered by an integration test.
Rendering is handled by the next ADR.

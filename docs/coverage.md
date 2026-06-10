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

- Real `build123d` export paths are not executed in this environment because
  `build123d` is not installed on `fjord`.
- Runtime-error handling is covered by implementation review and diagnostics
  structure, but not by a dedicated failing-design test yet.
- Automatic CAD topology discovery is not implemented in ADR 0001; the MVP
  relies on explicit `publish` and `publish_feature` calls.

The gap is acceptable for ADR 0001 because the feature's primary contract is
the agent loop and artifact schema. The next branch that installs or vendors a
CAD runtime should add extended integration tests using real build123d parts.

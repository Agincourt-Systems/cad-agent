# LESSONS

- 2026-06-09: Per-feature unit tests missed a cross-feature regression for
  four ADRs: automatic STEP feature detection (ADR 0007) double-counted any
  feature a design also published explicitly, so the `cadx init` starter
  project failed its own starter requirements. Each detection test used only
  one channel (explicit publication or automatic detection), never both. When
  two channels feed the same output, add a test that exercises them together,
  and keep the full `init` → `run` → `evaluate` starter flow as a standing
  regression test (added in ADR 0012).

- 2026-07-24: Two of four parallel subagents stopped "waiting on a monitor"
  after they started their full-suite gate in the background. A stopped agent
  gets no notification, so the gate result was never read and the green
  commit / final report never happened. Instruct subagents to run their
  verification gates in the FOREGROUND and block until the result line, and
  treat any agent report without a pasted result line as incomplete.

- 2026-07-24: `from cadx import publish` is import-order-dependent under the
  full test suite: importing the `cadx.publish` MODULE (export plans,
  ADR 0029) rebinds the package attribute `publish` from the re-exported
  registry function to that module. In-process consumers should import from
  `cadx.registry`; the durable fix is renaming the module so a package
  attribute and a submodule never share a name.

- 2026-10-31: build123d's `ExportSVG` rounds path coordinates to 1e-6 mm while
  OCCT only treats a curve as closed within `Precision::Confusion` (1e-7 mm).
  Anything in that 10x window is open geometry to OCCT and a single point to
  the exporter, and `svgpathtools.Arc` then fails a bare `assert start != end`
  with no message. When two layers carry their own tolerance, state the
  tighter one explicitly (the renderer now passes `precision=SVG_PRECISION`)
  and filter the band between them rather than trusting either default.

- 2026-10-31: `project_to_viewport` aims its camera at `shape.center()`, so an
  axis-aligned camera plus a symmetric part is a crash, not an edge case: the
  look direction goes collinear with the default `viewport_up` and OCCT throws
  `gp_Dir::Crossed()` zero norm. The earlier fix guarded only *empty* shapes,
  which is why it came back. Guard the geometric condition (the cross product),
  not the one input that happened to produce it.

- 2026-10-31: A background waiter that polls `pgrep -f "pytest tests/"` matches
  its own command line, because the pattern is in it. The loop never exits, so
  three waiters span for 12 hours over two gates that had both finished in the
  first 40 minutes. Wait on the harness's own background-task notification
  instead of writing a waiter. Where a poll is unavoidable, make the pattern
  one the waiter cannot satisfy itself.

- 2026-10-31: `[tool.pytest.ini_options] addopts = "-q"` in pyproject.toml plus
  an explicit `-q` on the command line is `-qq`, which suppresses the
  `N passed in Xs` result line. The suite was green, but its log held only
  progress dots and so could not be read as a gate; it looked truncated. Do not
  repeat a flag the project config already sets, and treat a gate log with no
  result line as no result.

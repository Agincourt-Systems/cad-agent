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

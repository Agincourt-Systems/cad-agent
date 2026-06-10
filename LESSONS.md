# LESSONS

- 2026-06-09: Per-feature unit tests missed a cross-feature regression for
  four ADRs: automatic STEP feature detection (ADR 0007) double-counted any
  feature a design also published explicitly, so the `cadx init` starter
  project failed its own starter requirements. Each detection test used only
  one channel (explicit publication or automatic detection), never both. When
  two channels feed the same output, add a test that exercises them together,
  and keep the full `init` → `run` → `evaluate` starter flow as a standing
  regression test (added in ADR 0012).

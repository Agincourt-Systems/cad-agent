# LOG

## 2026-06-09

- Read `/home/orb/code/AGENTS.md`; no repo-local `AGENTS.md` existed because
  `/home/orb/code/cad-agent` was absent.
- Created `/home/orb/code/cad-agent` and initialized a git repository.
- Starting with ADR-backed, red-green TDD for the first CLI harness feature.
- Confirmed red state: 3 tests failed because the `cadx` package did not yet
  exist.
- Implemented the ADR 0001 MVP harness and confirmed the focused remote suite
  passes: `3 passed in 0.48s`.
- Pushed `master` and `codex/adr-0001-agentic-harness-contract` to
  `git@github.com:torchhound/cad-agent.git` after the user configured the
  remote.
- Started ADR 0002 on `codex/adr-0002-build123d-integration`.
- Installed `cad-agent` with `[cad,render,test]` into the Python 3.10 user site
  on `fjord`; `python3-venv` was unavailable.
- Confirmed ADR 0002 red state: real `build123d` exports existed, but
  `cadx run` did not write `spatial.json` immediately.
- Implemented runtime metadata and immediate spatial inspection for successful
  runs; full focused suite passed with `4 passed`.
- Merged ADR 0002 into `master` with a fast-forward merge and pushed
  `master` plus the preserved feature branch.
- Started ADR 0003 on `codex/adr-0003-real-view-rendering`.
- Confirmed ADR 0003 red state: `cadx render` had no render manifest, then no
  section artifacts after projection rendering was added.
- Implemented STEP-backed ISO/top/front/right SVG projections, XY/XZ/YZ section
  SVG projections, and `views/render_manifest.json`; full suite passed with
  `5 passed`.
- Started ADR 0004 on `codex/adr-0004-end-to-end-loop`.
- Confirmed ADR 0004 red state: the real agent-loop test failed because
  `cadx evaluate` did not return `report_path` or write `report.md`.
- Implemented evaluation reports that summarize failed checks and list existing
  spatial, checks, contact sheet, and render manifest artifacts; full suite
  passed with `6 passed`.
- Started ADR 0005 on `codex/adr-0005-isolated-execution-worker`.
- Confirmed ADR 0005 red state: design stdout polluted CLI JSON, and
  `cadx run` had no timeout option for hanging designs.
- Implemented `cadx.worker`, subprocess execution, stdout/stderr capture, and
  timeout diagnostics; full suite passed with `8 passed`.
- Started ADR 0006 on `codex/adr-0006-richer-requirement-checks`.
- Confirmed ADR 0006 red state: range dimensions required `equals`, and
  `topology`/`clearance` checks were unsupported.
- Implemented dimension ranges, topology target checks, and AABB clearance
  checks; full suite passed with `10 passed`.
- Started ADR 0007 on `codex/adr-0007-automatic-feature-detection`.
- Confirmed ADR 0007 red state: a real plate with two cylindrical cutouts
  produced zero detected features without explicit `publish_feature()` calls.
- Implemented STEP-backed cylindrical-hole detection in inspection; full suite
  passed with `11 passed`.
- Started ADR 0008 on `codex/adr-0008-loop-orchestration`.
- Confirmed ADR 0008 red state: `cadx loop` was not a recognized command.
- Implemented bounded loop orchestration around run/render/evaluate plus an
  external trusted agent command; full suite passed with `13 passed`.
- Started ADR 0009 on `codex/adr-0009-exact-geometric-clearance`.
- Confirmed ADR 0009 red state: `method: exact` clearance still used AABB
  behavior and reported `0` for diagonally separated cylinders.
- Implemented STEP-backed exact BREP clearance using `Shape.distance()`; full
  suite passed with `15 passed`.
- Started ADR 0010 on `codex/adr-0010-richer-feature-detection`.
- Confirmed ADR 0010 red state: richer automatic feature kinds were absent.
- Implemented planar datum, cylindrical boss, and simple obround slot detection;
  full suite passed with `16 passed`.
- Started ADR 0011 on `codex/adr-0011-shaded-raster-rendering`.
- Confirmed ADR 0011 red state: render manifests had no shaded raster artifact.
- VTK offscreen rendering aborted without an X server, so implemented a
  headless software STL rasterizer that writes `views/shaded_iso.png`.
- Generated `docs/images/cad-output.png` from real CAD output and added it to
  the README; full suite passed with `17 passed`.

## 2026-06-09 — overnight session (Claude)

- Read `AGENTS.md`, `README.md`, `LESSONS.md`, `LOG.md`, `MEMORY.md`, and
  `docs/` to take over from the prior Codex sessions; confirmed the full suite
  still passes with 17 tests.
- Smoke-tested every CLI command against real `build123d` in a scratch
  directory. `init`, `run`, `inspect`, `render`, `compare`, and `loop` all
  executed, but the `cadx init` starter project failed its own starter
  requirements: `mount_holes` observed 4 cylindrical holes on a 2-hole plate
  because automatic STEP detection (ADR 0007) re-detected the two explicitly
  published features.
- Corrected stale `docs/coverage.md` known-gap entries that ADRs 0005, 0007,
  0009, 0010, and 0011 had already closed (runtime-error test, automatic
  detection, exact clearance, shaded raster rendering) and re-dated the
  closing rationale from ADR 0004 to ADR 0011.
- Started ADR 0012 on `claude/adr-0012-feature-deduplication`.
- Confirmed ADR 0012 red state: the `cadx init` starter project failed its
  own `mount_holes` check because explicit publications and automatic STEP
  detection each reported the same two holes.
- Implemented kind/size/axis-aware deduplication of detected features against
  explicit publications in `inspect_run`, with `confirmed_by_detection`
  marking on corroborated publications; full suite passed with `19 passed`
  and a live `cadx init` → `cadx loop` smoke test converged in one iteration.

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

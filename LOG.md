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

## 2026-06-23 — sheet-metal / SendCutSend track (Claude)

- Read `docs/ca-sheet-metal-fixes.md`: nine deficiencies (D1–D9) for a
  laser-cut sheet-metal → SendCutSend workflow. Mapped them to eight ADRs
  (0013–0020) and confirmed implementation order
  `0013 → 0014 → 0015 → 0016 → 0017 → 0018 → 0019 → 0020`.
- Ran a design pass that drafted each ADR + its red tests + an implementation
  plan in parallel, then reconciled the shared machine contract (export-record
  vocabulary, new `spatial.json`/`checks.json` keys, registry signatures) into
  `scratchpad/design/CONTRACT.md` so the sequential implementation never
  regresses an earlier ADR. Confirmed `build123d 0.10.0` already ships
  `ExportDXF`/`ExportSVG` (the spec's 0.11 note is moot) and `ezdxf 1.4.4` is
  available for DXF parsing in tests.
- Started ADR 0013 on `claude/adr-0013-dxf-flat-pattern-export`.
- Confirmed ADR 0013 red state: `from cadx import publish_flat` raised
  `ImportError`, and no `format=="dxf"` export records or `units` keys existed.
- Implemented `publish_flat` + a `_FLATS` registry channel, a shared `_write_dxf`
  writer, `_flatten_to_xy` plane-localization (works around `ExportDXF` silently
  dropping off-XY-plane geometry), an auto-flatten volume-invariant prism
  detector, and `unit=Unit.MM` + `units:"mm"` on every export record (D9).
  Updated the ADR 0002 integration test for the intentionally extended export
  contract (`{step,stl,glb,dxf}`). Full suite passed with `34 passed`.
- Adversarial review of the ADR 0013 diff drove three fixes before merge: a
  planarity guard so a non-planar `publish_flat` profile degrades to a
  `flat_export_failed` warning instead of a silently-degenerate DXF, an
  arc-aware closed-loop test for fillet-cornered outlines, and a silent skip of
  auto-flatten for non-solid (`result` sketch) publications. Merged ADR 0013 to
  `master` (`37 passed`) and preserved the branch.
- Started ADR 0014 on `claude/adr-0014-assembly-placement`.
- Confirmed ADR 0014 red state: `publish` ignored `placement` and
  `feature_alignment`/`interference` were unsupported check types.
- Implemented `publish(..., placement=Location)` applied through a shared
  `runner._placed_object` helper (reused by ADR 0015), placement recorded on
  spatial objects, and `feature_alignment` + `interference` evaluate checks.
  Discovered and fixed a cross-ADR bug: ADR 0012's radial dedup collapsed
  coaxial holes from two *stacked* plates into one, deleting plate B's holes;
  guarded `_is_duplicate` so features on different `source_object`s never merge.
  Also made `feature_alignment` pair the best-aligned holes (build123d STEP face
  order is not translation-invariant). Full suite passed with `46 passed`.
- Reviewed ADR 0014; fixed `interference`/`feature_alignment` to return
  descriptive errors instead of `KeyError`/vacuous self-pass on bad selectors.
  Merged ADR 0014 to `master` (`48 passed`), branch preserved.
- Started ADR 0015 on `claude/adr-0015-center-of-mass`.
- Confirmed ADR 0015 red state: no `center_of_mass`/`matrix_of_inertia` in
  `mass_properties`, no `assembly` key, `center_of_mass`/`stability` unsupported.
- Implemented `runner._mass_properties` (center of mass + inertia on the placed
  object), `inspector._assembly_center_of_mass` (mass- or volume-weighted), and
  `center_of_mass` + `stability` evaluate checks (convex-hull support polygon,
  signed margin, tip angle). Review-driven fixes: mixed-density assemblies weight
  by volume (no unit-inconsistent hybrid), degenerate-support and kernel-free
  paths tested, `center_of_mass` target defaults to `assembly`. Merged ADR 0015
  to `master` with `60 passed`.
- Started ADR 0016 on `claude/adr-0016-sheet-metal-bend`.
- Confirmed ADR 0016 red state: no `cadx.sheetmetal` module and no `bend` check.
- Implemented `sheetmetal.bend()` (developed length via K-factor, two-box folded
  envelope, flat profile, bend lines), `publish_sheet_metal`, a sheet-metal DXF
  export reusing ADR 0013's `_write_dxf` with a `bend` layer, an aggregated
  `bends.json` bend table, and a `bend` evaluate check. Review found and fixed a
  multi-part `bends.json` clobber (now one aggregated table, label-tagged rows)
  and a `direction="down"` envelope discrepancy; added input validation. Merged
  ADR 0016 to `master` with `69 passed`.
- Started ADR 0017 on `claude/adr-0017-bom`.
- Confirmed ADR 0017 red state: no `publish_part_meta` and no `bom` subcommand.
- Implemented `publish_part_meta` (recorded into `diagnostics["part_meta"]` in
  all three envelopes), a `cadx bom` subcommand, and `src/cadx/bom.py` writing
  deterministic `bom.csv`/`bom.json` grouped by vendor with totals. Review-driven
  fixes: keep an explicit `qty=0`, record the true flat-pattern `area_mm2` on DXF
  exports so a bent part quotes on its unfolded area (not the folded flange), warn
  on orphan part metadata, and stabilize totals to float. Merged ADR 0017 to
  `master` with `76 passed`.
- Started ADR 0018 on `claude/adr-0018-dfm`.
- Confirmed ADR 0018 red state: `manufacturability` raised `unsupported check
  type`.
- Implemented a pure-python `src/cadx/dfm.py` engine (min_hole_diameter,
  min_slot_width, hole_to_edge, min_web, min_bend_radius, hole_to_bend) routed
  from `evaluate.py`. Review found two rule-math blockers, both fixed: hole_to_edge
  derived the thickness axis from the feature axis (wrong for detected slots,
  whose axis is the elongation direction) — now from the owning bbox with per-axis
  slot half-extents; and min_web paired two unsourced features (`None == None`).
  Merged ADR 0018 to `master` with `87 passed`.
- Started ADR 0019 on `claude/adr-0019-contact-sheet`.
- Confirmed ADR 0019 red state: the contact-sheet ISO panel was a placeholder
  rectangle (~0.06 non-white) and the manifest had no `contact_panels` key.
- Implemented embedding the real `views/shaded_iso.png` raster into the ISO panel
  (no headless SVG rasterizer, so ortho/section panels keep placeholders) and a
  `contact_panels` manifest record per panel. Merged ADR 0019 to `master` with
  `90 passed`.
- Started ADR 0020 on `claude/adr-0020-parametric`.
- Confirmed ADR 0020 red state: `parametric` raised `unsupported check type` and
  `cadx sweep` was unrecognized.
- Implemented a `parametric` check that re-runs the design's `source_snapshot.py`
  across parameter sets into `sweeps/<id>/NNNN` and aggregates sub-checks, an
  additive `timeout` keyword threaded through `_evaluate_check`/`evaluate_run`, a
  `cadx sweep` subcommand, and a README "Requirement check types" section naming
  `symmetry`/`visual` as unsupported. Review confirmed signature back-compat,
  determinism, report rendering, and timeout end-to-end with no blockers. Merged
  ADR 0020 to `master` with `96 passed`.
- All nine sheet-metal deficiencies (D1-D9) are now implemented across ADRs
  0013-0020.
- Ran a holistic acceptance review (acceptance vs spec, machine-contract
  stability, AGENTS.md compliance, end-to-end SendCutSend smoke). Verdict:
  go-with-fixes, zero blockers. Two should-fix items from the end-to-end view
  became ADR 0021 on `claude/adr-0021-assembly-robustness`:
  (1) `_assembly_center_of_mass` only aggregated `role="part"`, silently dropping
  the idiomatic `role="final"` base part from the assembly CoM (skewing
  stability/CoG checks) — now aggregates all physical roles, excluding only a
  fixture/reference denylist; (2) a mistyped `dimension`/`topology` target raised
  an uncaught `KeyError` aborting the whole evaluate — now degrades to a graceful
  failed check. Red state confirmed, fixed, merged with `98 passed`.

## 2026-07-02

- Ran a full-repository bug review (all 13 `src/cadx` modules). Confirmed by
  direct reproduction that the evaluator still crashed on four
  plausible-authoring-error shapes ADR 0021's resolution guard did not cover
  (non-scalar targets, `None` topology counts, `feature_dimension` on a missing
  property, exact clearance without STEP exports, plus a narrow CoM-resolver
  except clause), that `cadx loop` dropped `--timeout-seconds` on the evaluate
  leg, and that one unreadable STEP export aborted `inspect`/`render` — in the
  worker this converted a successful build into a failed run. Lesser
  findings (exit-code inconsistency for `evaluate`, duplicate publish labels,
  `next_run_dir` race, ASCII-STL sniffing, sweep-id path safety) recorded in
  the review conversation for future ADRs.
- Started ADR 0022 on `claude/adr-0022-graceful-evaluation`. Red state
  confirmed: all 8 new tests failed as predicted. Notable: `import_step` on
  garbage returns an *empty* shape (OCCT parse error on stdout, no exception),
  so the ingestion guards treat a faceless import as a failure too.
- Implemented value-side graceful degradation in `evaluate.py` (merging the
  duplicate `_check_dimension`/`_check_topology` into `_check_scalar_target`),
  the loop timeout passthrough, and per-export STEP guards in
  `inspector.py`/`renderer.py` with agent-visible `warnings` in `spatial.json`
  and `render_manifest.json`. Full suite `106 passed` (98 + 8 new), merged to
  `master`.

## 2026-07-03

- Assessed assembly capability on request: placement frame (ADR 0014),
  cross-part checks, and assembly CoM are solid; gaps were no combined
  assembly artifact, first-part-only rendering, and no joint/mate solver.
- Started ADR 0023 on `claude/adr-0023-assembly-rendering` for the first two
  gaps. Kernel probe confirmed `Compound(children=[...])` bakes placements and
  round-trips STEP with one solid per part. Red state confirmed: 5 of 6 new
  tests failed (the single-part non-regression pin is green-before by design).
- Implemented `runner._export_assembly` (combined `assembly.step/stl/glb`,
  records flagged `assembly: true`, emitted for >=2 real published shapes,
  `assembly_export_skipped` warning on a label collision), worker wiring,
  inspector exclusion of the flagged export from feature detection, renderer
  preference for the assembly artifact in projections and the shaded raster,
  and a union-bbox `_summary_line`/`_bounds_union` contact-sheet summary.
- Verified end-to-end on a demo base+tower design: shaded iso and contact
  sheet show both placed parts, summary reports the assembly union bbox, and
  planar-datum features stayed undoubled. Full suite `112 passed` (106 + 6
  new), merged to `master`. Joint-driven placement remains the next big
  assembly ADR if wanted.
- Started ADR 0024 on `claude/adr-0024-joint-driven-placement`: declarative
  mates. Kernel probe first: `target * anchor.inverse()` exactly reproduces
  `RigidJoint.connect_to` placement, so `mate()` resolves to the existing
  `placement` field and no downstream stage changes. Red state confirmed
  (7 new tests failed; `publish` rejected the `mate` keyword).
- Implemented `mate(to, anchor/target | joint/target_joint)` exported from
  `cadx`, `publish(mate=...)` (mutually exclusive with `placement`),
  `runner._resolve_mates` (fixpoint chain resolution; `mate_unresolved` /
  `mate_failed` warnings for unknown targets, unplaced parents, bad joints,
  and cycles), a JSON-safe `mate` record on spatial objects, and a README
  Assemblies section. Added an unplaced-parent guard beyond the ADR list so a
  child never silently mates against the origin.
- Verified end-to-end: three-part demo (joints + chained explicit frames)
  places tower at (20,5,18) and cap at (20,5,35) with the cap seated exactly
  on the tower top; shaded assembly render confirms. Full suite `119 passed`
  (112 + 7 new), merged to `master`.
- Started ADR 0025 on `claude/adr-0025-kinematic-joints`: kinematic joint
  types. Probe first: pose composes as one Location
  (`parent * target * Location((0,0,travel),(0,0,angle)) * anchor^-1`, axis =
  target frame local Z); b3d `RevoluteJoint` exposes only `relative_axis` (no
  angle-zero reference), so native kinematic-joint consumption is deferred.
  Red state confirmed: 7 new tests failed (`mate()` rejected `kind`).
- Implemented `mate(kind="revolute"|"prismatic"|"cylindrical", angle=,
  travel=, angle_range=, travel_range=)` with design-time validation of
  foreign pose args, pose math in `_mate_placement` (synthetic dicts support
  prismatic; revolute on a dict degrades to `mate_failed`),
  `mate_out_of_range` warnings that still place the requested pose, and pose
  fields on the spatial mate record (rigid records byte-identical). Zero
  evaluator changes: motion envelopes are ADR 0020 parametric sweeps over a
  params-driven pose — pinned by the flagship interference-sweep test and a
  README recipe.
- Verified end-to-end on a post/wall/arm demo: renders at 0/90 deg show the
  arm clear then colliding, with the out-of-range warning emitted at 90 deg
  against a declared 75 deg limit. Full suite `131 passed` (124 + 7 new),
  merged to `master`.
- Replaced the README hero screenshot (single slotted plate) with a shaded
  render of a four-part mated assembly (base, tower, revolute-posed swing arm
  at -35 deg, pivot boss) produced by the ADR 0024/0025 mate API, so the
  first image shows current capability.

## ADR 0026 — multi-view shaded screenshots (`cadx shots`)

- The shaded rasterizer was isometric-only (`_project_iso` hard-coded), so
  the one real headless raster always showed a single angle. Downstream
  (the `chupacabra-configuration` airframe README) that meant the wing
  planform and fin profile were invisible, and a throwaway per-repo
  screenshot script was written to render orthographic views from
  `assembly.stl`. Generalised that into cadx.
- `_render_stl_shaded` gained a `project` parameter (default `_project_iso`,
  so `render` output is byte-stable); added `_orthographic_projector` +
  a `SHADED_CAMERAS` registry (`iso`/`top`/`side`/`front`/`rear`) and a new
  `render_shots` + `cadx shots <run_dir> [--views ...] [--out DIR]` command.
  Multi-part runs shoot the combined `assembly.stl` via `_primary_export`,
  like `render`.
- TDD (`tests/test_multi_view_shots.py`): the behavioural test renders a
  plate wide in Y / thin in Z and asserts the `top` view's content is
  proportionally taller than the `side` view's — proving the projection,
  not just the filename, changed. Red→green; full suite green, legacy
  `render` path untouched.
- Verified on the real airframe: `top` = wing planform, `side` = fin
  profile, `front` = circular body with the cruciform/wing cross.

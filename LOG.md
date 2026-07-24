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
- Started ADR 0027 on `claude/adr-0027-render-materials`: appearance-only
  materials for shaded renders (user request: carbon fiber, steel, aluminum,
  fasteners, optics — no simulation). Noted the concurrent ADR 0026 (`cadx
  shots`) landed first and integrated with it rather than duplicating.
  Red state confirmed: 9 new tests failed (no `cadx.materials`, all-blue
  renders).
- Implemented `cadx.materials` (preset table with metals, black_oxide,
  anodized finishes, carbon_fiber two-tone, translucent glass, plastics; hex
  literals; `DEFAULT_PALETTE` whose first entry preserves the legacy blue
  formula; part_meta substring mapping), a per-part composite rasterizer path
  (`_shaded_batches` + `_render_shaded` with Blinn-style specular, centroid
  two-tone hash, alpha compositing) feeding both `render` and `shots`, with
  `parts` appearance records and `appearance_unknown` warnings in the
  manifest/payload. `_render_stl_shaded` stayed byte-stable as a one-batch
  legacy wrapper; pinned ADR 0011/0023/0026 contracts unchanged.
- Visual verify on a camera-module showcase caught navy outline striping on
  cylinder flanks; presets gained darkened per-material `outline` colors
  (legacy/palette/hex keep (32,54,72) for byte-stability). Full suite `149 passed` (140 + 9 new),
  merged to `master`.
- Added the ADR 0027 materials showcase render (aluminum/carbon/steel/glass/
  black-oxide/brass camera module) to the README as a second hero image
  alongside the mated-assembly screenshot.
- Started ADR 0028 on `claude/adr-0028-shot-lighting`: configurable light
  direction for `cadx shots` (the ADR 0027 side views were ambient-dark under
  the fixed ADR 0011 light). Red state: 4 of 5 new tests failed (the
  invalid-spec test was green-before via argparse's own rejection, noted in
  the AAR).
- Implemented `DEFAULT_LIGHT`, a `light` parameter through `_render_shaded`,
  `_resolve_shot_light` (`"camera"` = per-view front light, `"X,Y,Z"`
  explicit, fail-fast on garbage), `cadx shots --light`, and per-shot
  recorded light vectors in the payload. Default path byte-stable (pinned);
  `render`/contact sheet deliberately unchanged. Visual verify on the
  materials showcase: default side view murky, `--light camera` legible;
  documented the off-axis soft-light recipe since pure front light maximizes
  specular on camera-facing faces. Full suite `154 passed` (149 + 5 new),
  merged to `master`.


## 2026-07-05 — ADR 0029: cadx publish (apexmesh export)

apexmesh (../apexmesh) is now the pipeline hub (its design-v2.md); `cadx
publish <run_dir> --project <name>` exports a run dir: a `cadx` run record
(external_ref `<project-dir>:<run-number>`, status mirroring diagnostics
errors, check counts as metrics), source_snapshot.py + params.resolved.yaml
as lineage inputs, every recorded STEP/STL/GLB/DXF export plus checks/
diagnostics/spatial/bom/report/views as outputs, and bom.json rows mapped
into apexmesh parts (declared part_number wins) with a revision named after
the run number under a `<PROJECT>-ASSY` assembly. Pure plan layer + fake
client execution tests (12); apexmesh-client optional lazy import;
republish refuses without --force; 409 revision conflicts reuse the
existing revision. Suite 154 → 166 green. Live end-to-end validation rides
apexmesh ADR-0018 (chupacabra backfill).


## 2026-07-21 — ADRs 0030–0039: downstream arm deficiency fixes (D-001…D-015)

The downstream robot-arm project's Phase-0 capability probe filed 15
deficiencies against cadx (`docs/specs/arm-deficiencies.md`, committed today
with the docs/specs/ restructure). All fixable items shipped as ten ADRs in
four parallel tracks, each implemented by an Opus subagent in an isolated
worktree (ADR-first red-green TDD, stacked `opus/adr-NNNN-*` branches),
reviewed by Fable, rebased onto master in sequence, full suite green at
every merge gate:

- **Packaging/CLI (0030–0031)**: capped `build123d>=0.10,<0.11` (0.11 makes
  interference silently pass on overlaps and crashes assembly render —
  D-001; adoption of 0.11 deferred to a future ADR), declared `ezdxf` in the
  test extra (D-002); top-level CLI handler turns escaped exceptions into
  `{"status":"error"}` JSON on stdout + traceback on stderr + exit 1
  (D-010); README now documents the real `--artifact-root` layout, implicit
  inspect-in-run, and the variable export set (D-011/012/015). Review fix by
  Fable: the new packaging guard tests' own imports (`packaging`, `tomli`)
  declared in the test extra — the same D-002 class they guard against.
- **Sheet metal (0032–0034)**: `bend_chain()` generalizes `bend()` to
  ordered flange/bend chains — U-channel/clevis parts are now ONE blank with
  N bend lines and no double-counted web (D-003); `publish_sheet_metal`
  emits each bend as a `kind="bend"` spatial feature so the
  `min_bend_radius`/`hole_to_bend` DFM rules finally bind on the real bend
  flow (D-004); the folded solid is now a constant-thickness ribbon swept
  along the neutral centreline (straights + annular sectors at rho =
  R + K·t), so folded volume equals the conserved blank volume exactly by
  Pappus — the ~4.9% bend-region deficit is gone (D-005).
- **Mass properties (0035–0037)**: new `cadx.density` material→density
  table (g/mm³, whole-token matching so "plastic" never resolves to "PLA");
  a declared known material now implies density and per-part
  `mass_properties.mass`, with `density_source` provenance and explicit
  `density=` always winning (D-008); `spatial["assembly"]` gains an
  `inertia` block — parallel-axis aggregate about the assembly CoM, g·mm²
  when every part has density, mm⁵ geometric otherwise, mirroring the CoM
  `weighting` degradation (D-006); every `matrix_of_inertia` now carries a
  sibling `matrix_of_inertia_semantics` record naming the mm⁵/unit-density/
  world-axes/about-centroid trap instead of leaving it implicit (D-007).
- **Kinematics/exports (0038–0039)**: resolved mates now export their
  defining `anchor`/`target` frames, world-frame joint `axis` (kinematic
  kinds), and zero-pose `origin` (`parent * target * anchor⁻¹`) so a URDF
  generator needs no side-channel record (D-013); root/unmated parts get an
  explicit identity placement while declared-but-unresolved mates keep the
  absence signal (D-014); the `parametric` check accepts opt-in
  `fail_on_range_violation` turning `mate_out_of_range` warnings into set
  failures (D-009).

Suite 166 → 201 green (+35 tests). Not addressed by design: D-005 mass
correction workaround is superseded by the real fix; the D-001 build123d
0.11 API adoption is a recorded follow-up ADR. All ten feature branches
preserved and pushed.

## 2026-07-21 — ADRs 0040–0050: second arm deficiency round (D-016…D-026)

The downstream robot-arm project verified all fifteen Phase-0 fixes and
filed eleven new entries (D-016…D-026) from its Phase-3 part-modeling work.
Same protocol as the first round: four parallel Opus subagent tracks in
isolated worktrees (pre-assigned ADR numbers, file-ownership boundaries,
`PYTHONPATH="$PWD/src"` guard against the editable-install trap), Fable
review of every diff, sequential rebase onto master with a full-suite gate
before each fast-forward merge (A → B → C → D), plus one coordinator knit
ADR by Fable at the end. Zero test failures at any gate; the one worktree
conflict (worker.py, two tracks extending the same call sequence) resolved
by keeping both changes.

- **Sheet-metal API (0040–0042, track A)**: `bend_chain`/`bend` accept
  `holes=[...]` in flange-local frames (`flange`, `u`, `v`; round `diameter`
  or rectangular `length`/`width`); one unfold feeds the DXF cut layer, a
  boolean subtraction from the folded solid, and flat-frame
  `kind="cylindrical_hole"`/`"cutout"` spatial features, so the cut file,
  the DFM rules, and the mass all carry the same holes; folded volume ==
  developed − hole material exactly; bend-straddling or off-blank holes are
  clear `ValueError`s (D-019). `publish_sheet_metal` accepts
  `placement`/`mate` with `publish()`'s exact contract — a clevis can
  finally be a revolute-mate child through the public API; flat pattern,
  bends.json, and bend/hole features stay in the flat-pattern frame
  (D-020). The folded solid's `y in [-width, 0]` extrusion frame is now
  documented and pinned by tests, with opt-in `center_width=True` (D-024).
- **Sheet DFM (0043–0044, track B)**: new `min_flange` rule checks every
  developed-strip segment (outer legs and interior webs) against a
  `4.0*t` default (D-022); the `min_bend_radius` `1.0*t` floor is kept
  deliberately conservative — a fab house's verified tighter radius (e.g.
  SendCutSend 0.81 mm on 2.29 mm 5052) opts in via explicit `min`, pinned
  by test, never silently weakened (D-021); folded bend arcs no longer
  masquerade as phantom `slot` features — the discriminator is concentric
  same-axis-line partial cylinders at different radii (a real obround
  slot's ends are equal-radius on displaced axes), corroborated against
  the published bend table; `hole_to_edge` gains `frame: flat` so it runs
  coherently with `hole_to_bend` on bent parts (D-023).
- **Materials/aggregates (0045–0046, track C)**: a declared material that
  resolves to no density (and no explicit `density=`) now emits a
  structured `material_unresolved` warning in diagnostics — a massless
  link can no longer ship silently past a warnings-watching gate (D-016);
  the `assembly` block is self-describing about role filtering
  (`included_roles` + `excluded` `{label, role}` list) and a new
  `assembly_options(include_roles=[...])` declaration opts fixtures into
  mass/CoM/inertia consistently (D-026).
- **Frames/checks (0047–0049, track D)**: `inertia_link_frame` (+
  `_semantics`, + mass-scaled g·mm² variant when density resolved) emits
  the tensor rotated into the part's body frame `Rᵀ·I·R`, with R extracted
  via build123d itself so the Euler convention cannot drift (D-017); mate
  records gain `origin_in_parent`/`axis_in_parent` (rotation-only on the
  axis vector; root-parented == world, pinned) for direct URDF `<origin>`/
  `<axis>` consumption (D-018); dimension checks accept `frame: part`
  backed by a new `bbox_local` measured from the unplaced solid — a
  revolute-posed 60 mm platform reads 60.0, not the 84.85 world-AABB
  artifact (D-025).
- **Self-describing blank (0050, Fable knit)**: tracks A and B each shipped
  complete but left the same residual — DFM facts about the flat blank had
  to be passed explicitly because `spatial.json` never recorded them.
  `publish_sheet_metal` now serializes a `sheet` metadata block
  (`blank_length`/`blank_width`/`thickness`); `min_flange`, `frame: flat`
  `hole_to_edge`, and `_resolve_thickness` fall back to it (explicit
  parameters always win). This also retires the ADR 0033 thickness trap:
  the folded bbox minimum is the strip WIDTH on most folded parts, and is
  now the last resort instead of the first fallback.

Suite 201 → 259 green (+58 tests; 254 from the four tracks, 5 from the
knit). Review notes: Opus handled all four tracks with no rescues; my
integration work was the worker.py conflict resolution, the
spec-file-conflict protocol (agent snapshots predated the canonical
D-016…D-026 spec commit — one track re-authored entries locally and was
told mid-flight to drop them; all rebased clean), and ADR 0050. Residuals
recorded honestly in AARs: wrapped (bend-straddling) cutouts unsupported;
flat-frame projection of auto-detected folded-frame holes; Track D AARs
await a downstream URDF consumption cycle.
## 2026-07-24 — ADRs 0051–0057: third arm deficiency round (D-027…D-034)

The downstream robot-arm project filed its Phase-6 rollup: 8 open items,
all MINOR. D-030 (URDF serializer) is their own Phase-5 layer by design.
We fixed the other seven as ADRs 0051–0057, in four parallel Opus agent
tracks on isolated worktrees, with strict red-green TDD per ADR.

- **ADR 0051 (D-029)** — `opus/adr-0051-detected-feature-dedup`. STEP
  auto-detection re-observed `holes=`-authored bores in the folded frame
  (usually as `cylindrical_boss`) and false-positived `hole_to_bend` /
  `hole_to_edge`. Fix: suppress the re-detection at feature-merge time,
  gated on the part having published bends AND authored holes AND a
  radius match within 0.05 mm; the authored hole still gets
  `confirmed_by_detection`. Escape hatch: `exclude_detected: true` on
  manufacturability checks. `hole_to_bend` now has a provable green path
  on hole-bearing bent parts.
- **ADR 0052 (D-027)** — `opus/adr-0052-view-cone-check`. New
  `view_cone` check: apex (point or `obj.<label>.center` reference),
  axis, `half_angle_deg`, targets tested by 9 bbox points (8 corners +
  center, all must pass), optional `occluders` via segment-vs-AABB slab
  test that errs toward over-reporting occlusion. Malformed config fails
  the check loudly.
- **ADR 0053 (D-028)** — `opus/adr-0053-mate-placement-conflict-warning`.
  A mate poses with `located()`, which discards the shape's own
  `.location`. Now emits a `placement_overridden_by_mate` warning when a
  mated part carries a non-identity own transform. Positioning behavior
  unchanged; warning-only by design.
- **ADR 0054 (D-034)** — `opus/adr-0054-world-joint-frames`. Mate records
  gain `joint_world` and `joint_world_zero`: a world point ON the joint
  axis plus the world axis direction, at the posed and zero
  configurations. Revolute frames coincide (rotation about own Z);
  prismatic origin slides by travel. Points full-transform, axes
  rotation-only.
- **ADR 0055 (D-031)** — `opus/adr-0055-density-unit-contract`. The
  `density=` contract is now explicit: g/mm³, documented in `publish()`
  and README. New `density_unit=` ("g/mm^3" default, "kg/mm^3" ×1000)
  normalizes at publish time and records `density_unit_declared`;
  unknown unit raises at the design line. Mass/inertia labels can no
  longer be silently wrong by 1000×.
- **ADR 0056 (D-033)** — `opus/adr-0056-robotics-inertia-guidance`.
  `inertia_link_frame_mass_semantics.recommended_use` points robotics
  consumers at the body-frame mass-scaled tensor; new
  `docs/inertia-consumers.md` gives the worked 30°-rotated example, the
  three-tensor trap, and the exact URDF `<inertial>` recipe with
  g·mm² → kg·m² conversion.
- **ADR 0057 (D-032)** — `opus/adr-0057-feature-tolerance-metadata`.
  `publish_feature(..., tolerance={fit, nominal, tol_plus, tol_minus,
  note})` with a closed key set validated loudly at publish time; the
  dict rides the existing feature passthrough into `spatial.json`, and
  `bom.json` gains a per-run `tolerances` rollup. ISO 286 resolution and
  STEP/DXF GD&T embedding are documented out of scope.

Process notes. Two agent tracks stopped mid-flight "waiting on a
monitor" after launching their full-suite runs; both were resumed with
instructions to run the gate in the foreground and finished cleanly —
same lesson as the pipe-masking issue last round: subagents must block
on their own gates. Track C's full-suite run caught a real pre-existing
trap: importing the `cadx.publish` module rebinds the package attribute
`publish` from the re-exported registry function to the module, so
`from cadx import publish` is import-order-dependent under the full
suite (fixed in-track by importing from `cadx.registry`; a repo-level
rename remains open). Merge protocol as before: sequential
rebase-onto-master A→B→C→D with a full-suite gate per merge
(265 → 276 → 290 → 301 green), fast-forward merges only, feature
branches preserved. Suite grew 259 → 301.


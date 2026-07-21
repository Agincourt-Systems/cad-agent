# DEFICIENCIES — cadx

Every cadx failure, partial behavior, wrong geometry, workaround, or
confusing interface gets an entry. Format per spec §8:

```
ID · phase · operation attempted · expected · actual · minimal repro
severity: BLOCKER | MAJOR | MINOR | PAPERCUT
workaround used (if any) · suggested fix
```

Severity protocol:
- BLOCKER / MAJOR: log, surface to operator in-session, STOP until
  acknowledged.
- MINOR / PAPERCUT: log immediately, surface in batch at the next phase gate.

---

## D-001 — Unbounded build123d dependency breaks cadx; interference check silently passes

> **STATUS: FIXED upstream (ADR 0030, cadx fba4d36): build123d capped <0.11, cap comment cites this ID. Verified: 201/201 cadx tests green on 0.10.0.**

- **ID:** D-001
- **Phase:** 0 (bring-up)
- **Operation attempted:** Fresh install per cadx README (`pip install -e .[cad,render,test]`), then `pytest`.
- **Expected:** Test suite green (cadx LOG.md records "166 green").
- **Actual:** With build123d 0.11.0 or 0.11.1 (both satisfy cadx's declared
  `build123d>=0.10`), 11 of 166 tests fail. Two failure classes:
  1. **Silent wrongness:** the `interference` check returns `pass` on
     overlapping parts (`test_interference_detects_overlap`,
     `test_motion_envelope_sweep_catches_interference`). No error, no
     warning — an assembly with colliding geometry evaluates clean.
  2. **Crashes:** assembly export/render and section render raise
     `anytree TreeError: Cannot add non-node object [Face]` from
     `Compound(children=pieces)` (`renderer.py:639`), failing
     `cadx render` on multi-part runs (7 render/material tests).
- **Minimal repro:**
  ```
  uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[cad,render,test]' ezdxf
  .venv/bin/python -m pytest tests/test_assembly_placement.py::test_interference_detects_overlap
  # fails (interference 'pass' on overlap). Then:
  uv pip install -p .venv/bin/python 'build123d==0.10.0' && re-run: passes.
  ```
  Reproduced identically on Python 3.12.13 and 3.14; build123d version is
  the only variable. Full suite is 166/166 green on build123d 0.10.0.
- **Severity:** MAJOR. The false-pass interference result is
  wrong-but-plausible output on the critical path (spec §8: MAJOR minimum).
  It occurs under a dependency version cadx itself declares supported.
- **Workaround used:** Pin `build123d==0.10.0` in our probe venv.
- **Suggested fix:** Cap the dependency (`build123d>=0.10,<0.11`) until the
  0.11 API changes (Compound children handling, intersect() behavior) are
  adopted; add a CI job against the latest build123d to catch this class.

## D-002 — `ezdxf` used by tests but not declared as a dependency

> **STATUS: FIXED upstream (ADR 0030): ezdxf declared. Verified in pyproject.**

- **ID:** D-002
- **Phase:** 0 (bring-up)
- **Operation attempted:** Install per README, run DXF-related tests.
- **Expected:** Declared extras are sufficient to run the full suite.
- **Actual:** DXF parse-back tests `importorskip("ezdxf")`; a README-faithful
  install silently skips them. DXF verification is load-bearing for this
  project (SendCutSend flat patterns).
- **Minimal repro:** `pip install -e .[cad,render,test]` then
  `pytest tests/test_dxf_export.py` → skips.
- **Severity:** PAPERCUT.
- **Workaround used:** Install `ezdxf` explicitly.
- **Suggested fix:** Add `ezdxf` to the `test` extra (or a `dxf` extra).

## D-003 — Multi-bend sheet parts unsupported (single-bend, two-flange cap)

> **STATUS: FIXED upstream (ADR 0032): new multi-bend chain API. Verified: 2-bend clevis = one blank, dev length 150.3597 mm = 40+BA+60+BA+40, two bend lines at hand-calc positions, one DXF, connected folded solid; 3-bend also works.**

- **ID:** D-003
- **Phase:** 0 (capability probe, sheet metal)
- **Operation attempted:** Express a two-bend part (U-channel / clevis
  bracket) as one flat blank via the `bend()` / `publish_sheet_metal` API.
- **Expected:** One blank, two bend lines, developed length
  L + BA + W + BA + L; one DXF.
- **Actual:** `bend()` accepts exactly two flanges and one bend
  (`SheetMetalPart` always carries one bend line and one bend-table row).
  Composing two `bend()` calls yields two disjoint blanks with the shared
  web double-counted (75.18 + 75.18 mm vs correct single blank 110.36 mm)
  and disconnected folded solids.
- **Minimal repro:** `probes/sheetmetal/probe_05_multibend.py`.
- **Severity:** MAJOR. Directly blocks the clevis brackets this arm needs
  (spec §1.3: every pitch joint is a clevis).
- **Workaround:** None inside the sheet-metal API. Options (need operator
  input): (a) model clevis brackets as CNC parts instead of bent sheet;
  (b) decompose each multi-bend part into single-bend parts joined by
  fasteners; (c) treat multi-bend support as a cadx fix.
- **Suggested fix:** Generalize `bend()` to an ordered flange/bend chain;
  the flat-pattern math extends directly.

## D-004 — Bend-safety DFM rules can never fire on the real bend flow

> **STATUS: FIXED upstream (ADR 0033): bends emitted as spatial kind="bend" features. Verified red-green: r=0.5 mm fails min_bend_radius, r=2.29 passes; hole_to_bend fires both ways. Note: check needs explicit thickness: parameter (documented).**

- **ID:** D-004
- **Phase:** 0 (capability probe, sheet metal)
- **Operation attempted:** `manufacturability` check with
  `min_bend_radius` / `hole_to_bend` against a `publish_sheet_metal` part
  folded with a sub-minimum inside radius (0.5 mm ≈ 0.22 t on 2.29 mm
  5052 — a radius SendCutSend would reject).
- **Expected:** Check fails (violation flagged).
- **Actual:** Check passes. `bend()` records bends in `bends.json`, not as
  `kind="bend"` spatial features; the two DFM rules only inspect spatial
  features, so they are inert on the only path that produces bends. They
  fire only if the user hand-publishes a redundant `kind="bend"` feature.
- **Minimal repro:**
  `probes/sheetmetal/probe_06_dfm.py::test_min_bend_radius_is_inert_on_the_real_bend_flow`.
- **Severity:** MAJOR. A manufacturability safety check that silently
  passes bad geometry (wrong-but-plausible class).
- **Workaround used:** Probe (and later design practice): publish an
  explicit `kind="bend"` feature per bend so the rules bind; encode
  SendCutSend minima in our own parameter checks as well.
- **Suggested fix:** `publish_sheet_metal` should emit each bend as a
  spatial `kind="bend"` feature carrying `inside_radius` and `line`.

## D-005 — Folded-solid volume ~4.9% low (bend-region material omitted)

> **STATUS: FIXED upstream (ADR 0034): swept-ribbon bend region, Pappus-exact. Verified: folded volume = blank volume to ~1e-16 relative for 1-, 2-, 3-bend parts.**

- **ID:** D-005
- **Phase:** 0 (capability probe, sheet metal)
- **Operation attempted:** Mass properties of a 90° bent part (40/60 mm
  flanges, 30 mm width, t=2.29 mm, R=2.29 mm, K=0.44).
- **Expected:** Volume of the conserved blank: 7225.86 mm³.
- **Actual:** 6870.0 mm³ — 355.86 mm³ (4.92%) low; the rectilinear folded
  envelope omits the bend-region material (BA·t·w). Documented in cadx
  coverage.md, but it biases every mass/CoM/inertia/stability result that
  includes bent parts. Flat DXF and developed length are unaffected.
- **Minimal repro:** `probes/sheetmetal/probe_02_bend_allowance.py`.
- **Severity:** MINOR (documented approximation; bounded, correctable).
- **Workaround:** Apply a per-part mass correction (+BA·t·w·ρ per bend) in
  our analysis layer; note it in `TOLERANCES.md` when mass rollups matter.
- **Suggested fix:** Model the bend region as a cylindrical sector in the
  folded solid.

## D-006 — No assembly-level inertia aggregation

> **STATUS: FIXED upstream (ADR 0036): assembly.inertia tensor with units/about/axes semantics. Verified vs parallel-axis hand calc to 0.0000% on a two-material assembly.**

- **ID:** D-006
- **Phase:** 0 (capability probe, assembly)
- **Operation attempted:** Read an aggregate inertia tensor for a multi-part
  assembly (needed for URDF `<inertial>` per link).
- **Expected:** Assembly record carries mass, CoM, and inertia tensor.
- **Actual:** `spatial["assembly"]` has only
  `{center_of_mass, mass, weighting, part_count}` — no inertia.
- **Minimal repro:**
  `probes/assembly/probe_07*::test_no_aggregate_inertia_tensor_available`.
- **Severity:** MINOR. Per-part inertia + placements are available, so we
  can compose per-link inertia ourselves (parallel-axis theorem) in the
  URDF generation layer.
- **Workaround:** Own aggregation code in Phase 5.
- **Suggested fix:** Aggregate inertia in `_assembly_center_of_mass`'s
  sibling path with parallel-axis transfer.

## D-007 — `matrix_of_inertia` is unit-density geometric inertia (mm⁵), a silent unit trap

> **STATUS: FIXED upstream (ADR 0037): matrix_of_inertia_semantics field (units mm^5, unit density, part centroid, world-at-placed-pose axes). Verified self-consistent incl. 30-deg-rotated part off-diagonals. Residual: still world axes, see D-017.**

- **ID:** D-007
- **Phase:** 0 (capability probe, mass properties)
- **Operation attempted:** Read mass moment of inertia for a part.
- **Expected (naive reader):** Mass inertia in mass·length² units.
- **Actual:** Geometric second moments at unit density, in mm⁵, about the
  part centroid; density is never applied. Correct once understood
  (ρ·MoI matched the hand-calculated plate inertia to <0.5%), but a naive
  consumer is wrong by ρ and by 10¹⁵ when converting to kg·m².
  **Addendum (exports probe):** the tensor is expressed in WORLD axes at
  the placed pose (off-diagonals appear when a part is published with a
  rotation), not the link body frame. A URDF consumer must rotate it back
  by the placement orientation — a second silent trap on top of the units.
- **Minimal repro:** `probes/assembly/probe_06*` (unit-density check;
  translation invariance confirms about-centroid).
- **Severity:** MINOR (documented here; our probes pin the semantics).
- **Workaround:** Analysis/URDF layer applies ρ and unit conversion; probe
  suite guards the semantics against future cadx changes.
- **Suggested fix:** Name the field `geometric_inertia_mm5` or include
  units metadata in the JSON.

## D-008 — No per-part mass; material name does not imply density

> **STATUS: FIXED upstream (ADR 0035): material-implied density (6061-T6 -> 2.70 g/cm^3), per-part mass in spatial.json, explicit density= override wins, density_source recorded. Residual: unknown material silent, see D-016.**

- **ID:** D-008
- **Phase:** 0 (capability probe, mass properties)
- **Operation attempted:** Get part mass from a declared material
  (`publish_part_meta(material="6061-T6")`).
- **Expected:** Material implies density; part record carries mass.
- **Actual:** Only `volume` is reported. Density flows solely as
  `publish(..., density=<g/mm³>)` metadata, used for CoM weighting; there
  is no material→density table.
- **Minimal repro:** `probes/assembly/probe_06*`.
- **Severity:** MINOR.
- **Workaround:** Central material→density table in our parameters file;
  every `publish` passes `density=` explicitly.
- **Suggested fix:** Built-in material density table keyed by common alloy
  names, with override.

## D-009 — ROM sweep is discrete sampling; `angle_range` violations only warn

> **STATUS: FIXED upstream (ADR 0039): parametric check gains opt-in fail_on_range_violation. Verified red-green: 120 deg outside [0,90] fails with range_violations detail; in-range passes; default remains warn-only.**

- **ID:** D-009
- **Phase:** 0 (capability probe, kinematics)
- **Operation attempted:** Continuous ROM interference verification with
  enforced per-joint limits (spec §5.1).
- **Expected:** Joint limits enforced (failure on out-of-range); collision
  anywhere inside ROM detected.
- **Actual:** Motion envelope = `parametric` interference check over a
  hand-enumerated angle list; collisions between samples are missed.
  `angle_range` violations emit only a `mate_out_of_range` warning, never
  a check failure.
- **Minimal repro:** `probes/assembly/probe_03*`.
- **Severity:** MINOR. Mechanism works and expresses per-joint ROM; we
  must choose sampling density (plan: ≤5° steps plus the four extremes).
- **Workaround:** Dense sampling in Phase 4; treat warnings as failures in
  our gate scripts.
- **Suggested fix:** Optional `fail_on_range_violation` and swept-volume
  interference.

## D-010 — CLI emits raw traceback instead of JSON error

> **STATUS: FIXED upstream (ADR 0031): missing-dir inspect/evaluate now print {"status":"error","message":...} JSON, exit 1, traceback on stderr. Verified red-green; new probe probes/modeling/probe_07_cli_error_contract.py.**

- **ID:** D-010 · **Phase:** 0 (modeling probe) · **Severity:** PAPERCUT
- **Operation:** `cadx inspect /nonexistent/xyz` (also `evaluate`).
- **Expected:** JSON error object per the CLI's one-JSON-per-subcommand
  contract. **Actual:** raw `FileNotFoundError` traceback, empty stdout,
  exit 1 — unparseable by a machine caller.
- **Workaround:** Wrap CLI calls; treat empty stdout as error.
- **Suggested fix:** Top-level exception handler printing a JSON error.

## D-011 — `--artifact-root` path shape undocumented

> **STATUS: FIXED upstream (ADR 0031, doc): README documents <root>/NNNN/ layout and authoritative artifact_dir.**

- **ID:** D-011 · **Phase:** 0 (modeling probe) · **Severity:** PAPERCUT
- **Operation:** `cadx run design.py --artifact-root out`.
- **Expected:** `out/runs/NNNN/` (as docs imply). **Actual:** `out/0001/`.
  Non-blocking: run JSON returns the true `artifact_dir`.
- **Workaround:** Always read `artifact_dir` from the run JSON.

## D-012 — Implicit `inspect` inside `run` undocumented

> **STATUS: FIXED upstream (ADR 0031, doc): README states run writes spatial.json; inspect optional before evaluate.**

- **ID:** D-012 · **Phase:** 0 (modeling probe) · **Severity:** PAPERCUT
- **Operation:** README quick-start `run → inspect → evaluate`.
- **Expected/Actual:** `run` already writes `spatial.json` with detected
  features; `inspect` is not required before `evaluate`. Doc clarity only.

## D-013 — Mate anchor/target frames dropped from JSON; joint axis and zero-pose origin unrecoverable

> **STATUS: FIXED upstream (ADR 0038): mate record now carries axis, origin, anchor, target. Verified: elbow-30-deg reconstruction parent*target*J(30)*anchor^-1 equals posed placement to <1e-6. Residual: frames are world-frame only (see D-017).**

- **ID:** D-013
- **Phase:** 0 (capability probe, exports)
- **Operation attempted:** Recover joint axis unit vector and zero-pose
  parent→child transform for a revolute mate from `spatial.json` (needed
  for URDF `<joint><axis>` and `<origin>`).
- **Expected:** Mate record exposes the frames the pose math already uses.
- **Actual:** `objects[].mate` keeps only `to/kind/angle/angle_range`; the
  anchor/target `Location`s are dropped in `_normalize_published`
  (runner.py ~400). Only the posed child placement is recorded, which
  conflates joint origin with the current pose. The root part carries no
  placement record at all.
- **Minimal repro:** `probes/exports/probe_6*`
  (`assert "axis" not in arm["mate"]`).
- **Severity:** MAJOR (as a cadx gap: kinematic mates exist but their
  defining frames are unexportable). Spec §6 anticipates this class
  ("if cadx cannot export frames, derive them from mate definitions and
  log the gap") and pre-approves the workaround.
- **Workaround:** We author the design files, so our parameter/design
  layer records every joint frame (axis, origin) alongside the `mate()`
  call; the URDF generator (Phase 5) reads our record, and cross-checks
  posed placements from cadx against it at sampled angles.
- **Suggested fix:** Serialize anchor/target frames into the mate record.

## D-014 — Root/unplaced parts have `placement: None`

> **STATUS: FIXED upstream (ADR 0038): root/unmated parts record explicit identity placement.**

- **ID:** D-014 · **Phase:** 0 (exports probe) · **Severity:** MINOR
- **Operation:** Read the world frame of the root part of an assembly.
- **Expected:** Identity placement recorded. **Actual:** `placement` is
  `None`; ambiguous if several parts are unplaced.
- **Minimal repro:** `probes/exports/probe_6*` (`base["placement"] is None`).
- **Workaround:** Treat `None` as identity; our designs place every
  non-root part explicitly.

## D-015 — Export set varies silently with part count / shape

> **STATUS: FIXED upstream (ADR 0031, doc): per-run export set documented (assembly.* only with >=2 real parts; auto-flatten rules).**

- **ID:** D-015 · **Phase:** 0 (exports probe) · **Severity:** PAPERCUT
- **Operation:** Predict the artifact set of a run.
- **Expected/Actual:** Combined `assembly.step` appears only with ≥2 real
  parts; a lone constant-thickness box silently auto-flattens to a DXF
  even when it is not a sheet part. Documented (cadx ADR-0013) but
  surprising.
- **Workaround:** Gate scripts assert the expected artifact list per run.

## D-016 — Unknown material name resolves silently to no density / no mass

> **STATUS: OPEN (found while verifying the D-008 fix, cadx fba4d36).**

- **ID:** D-016 · **Phase:** 0 (fix verification) · **Severity:** MINOR
- **Operation:** `publish(material="unobtanium")` (a typo'd alloy name).
- **Expected:** A warning in diagnostics; ideally the run flags it.
- **Actual:** Only `metadata.density_resolved=false`; `warnings==[]`; the
  part silently has no mass. A gate that does not read `density_resolved`
  ships a massless link into torque math.
- **Minimal repro:**
  `probes/assembly/probe_06*::test_unknown_material_records_unresolved_and_is_silent`.
- **Workaround:** Our gate scripts assert `density_resolved` (or `mass`)
  on every part with a material.
- **Suggested fix:** Emit a diagnostics warning for unresolved materials.

## D-017 — Inertia tensor still world-axes at placed pose, not link frame

> **STATUS: OPEN (residual of the D-007 fix; semantics are now honest, consumption still manual).**

- **ID:** D-017 · **Phase:** 0 (fix verification) · **Severity:** MINOR
- **Operation:** Consume per-part inertia for URDF `<inertial>`.
- **Expected (ideal):** Mass-scaled inertia + CoM in the link body frame.
- **Actual:** Unit-density tensor in world axes at the placed pose
  (declared truthfully by `matrix_of_inertia_semantics`). The URDF layer
  must scale by density and rotate by the inverse placement.
- **Minimal repro:** `probes/exports/probe_6*` (elbow at 30°: CoM/tensor in
  world coordinates).
- **Workaround:** Do the scale+rotation in our URDF generator (Phase 5);
  probes pin the semantics.
- **Suggested fix:** Optional `frame:"link"` emission alongside world.

## D-018 — Joint axis/origin exported in world frame only

> **STATUS: OPEN (residual of the D-013 fix).**

- **ID:** D-018 · **Phase:** 0 (fix verification) · **Severity:** PAPERCUT
- **Operation:** Read parent-relative joint origin/axis (URDF convention).
- **Expected:** Parent-relative form available directly.
- **Actual:** World-frame only; derivable as `parent^-1 * origin` since
  parent placements are now always present.
- **Minimal repro:** `probes/exports/probe_6*`.
- **Workaround:** One transform in the URDF generator.

## D-019 — Flat-pattern DXF contains no holes or cutouts (bare outline only)

> **STATUS: OPEN. Found Phase 3 (shoulder group), cadx fba4d36.**

- **ID:** D-019 · **Phase:** 3 (part modeling) · **Severity:** MAJOR
- **Operation:** Export the SendCutSend cut DXF for a bent bracket with
  bolt circles and lightening holes.
- **Expected:** Developed blank outline + all cutouts on the `cut` layer.
- **Actual:** `bend_chain.flat_profile` is a bare rectangle; there is no
  cutout API. Holes exist only as separately published flat-frame DFM
  features and are ABSENT from the exported DXF. A shop cutting this file
  produces an undrillable blank — wrong-but-plausible manufacturing
  output (spec §8: MAJOR minimum).
- **Minimal repro:** run `designs/shoulder_upperarm.py`; inspect
  `shoulder_yoke.dxf` → 4 cut lines, zero holes.
- **Workaround:** None acceptable for fabrication. Phase 3 gate holds the
  DXFs as geometric flat patterns only; fabrication-ready DXFs blocked on
  a cadx fix (hole projection into the developed blank) or an
  operator-approved post-process step.
- **Suggested fix:** Let `publish_sheet_metal` accept hole/cutout
  primitives positioned in flange-local frames, unfold them into the
  developed blank, and emit them on the `cut` layer.

## D-020 — `publish_sheet_metal` accepts neither placement nor mate

> **STATUS: OPEN. Found independently by two Phase 3 groups (elbow EF-2, shoulder SU-1).**

- **ID:** D-020 · **Phase:** 3 · **Severity:** MAJOR
- **Operation:** Pose a folded sheet part in an assembly / make it the
  child of a revolute mate (a clevis IS a folded sheet part; this is the
  normal case, not an edge case).
- **Expected:** `publish_sheet_metal(label, part, placement=…, mate=…)`.
- **Actual:** TypeError — folded parts are always identity-placed and
  cannot be a mate child through the public API.
- **Minimal repro:** `publish_sheet_metal("x", part, placement=Location(...))`.
- **Workarounds used (both ugly):** elbow group mutated the registry
  entry dict directly; shoulder group baked a Location into a re-wrapped
  `SheetMetalPart` and hung the J2 mate on a fixture horn instead of the
  arm. Both bypass the public API and will not survive a cadx refactor.
- **Suggested fix:** Pass placement/mate through `publish_sheet_metal` to
  the underlying publish record.

## D-021 — DFM `min_bend_radius` default (1.0·t) rejects verified press-brake radii

> **STATUS: OPEN (MINOR).**

- **ID:** D-021 · **Phase:** 3 · **Severity:** MINOR
- Default floor = 1.0×thickness (2.29 mm) fails every bend at the
  SendCutSend-verified 0.81 mm effective radius for 0.090" 5052; every
  design must pass an explicit `min`. Also our own params lacked a
  `min_bend_radius` field to anchor the override (added — see LOG).
- **Repro:** manufacturability check with no explicit min on an R=0.81 bend.
- **Suggested fix:** material/process-aware default or a documented
  requirement that `min` mirrors the fab house's published value.

## D-022 — No `min_flange` DFM rule

> **STATUS: OPEN (MINOR).**

- **ID:** D-022 · **Phase:** 3 · **Severity:** MINOR
- `params.sheet.design_rules.min_flange_length` (8.28 mm verified) has no
  cadx DFM counterpart; shoulder group enforced it in-test from bend
  positions. **Suggested fix:** add a `min_flange` rule keyed off the
  bend table.

## D-023 — DFM frame split: bend rules use flat frame, edge/web rules use folded bbox

> **STATUS: OPEN (MINOR).**

- **ID:** D-023 · **Phase:** 3 · **Severity:** MINOR
- `hole_to_bend` evaluates in the flat-pattern frame while
  `hole_to_edge`/`min_web` use the folded 3-D bbox, so the rule sets
  cannot run together on a bent part; folded bend arcs also auto-detect
  as spurious `slot` features (2 per bend), polluting feature-based
  rules. Groups restricted DFM to frame-consistent rules.
- **Repro:** `designs/elbow_forearm.py` evaluate with edge rules enabled.
- **Suggested fix:** evaluate all sheet DFM in the flat frame; suppress
  feature auto-detection on bend arc surfaces.

## D-024 — `bend_chain` extrudes width along −Y (undocumented)

> **STATUS: OPEN (PAPERCUT).**

- **ID:** D-024 · **Phase:** 3 · **Severity:** PAPERCUT
- Folded solid occupies Y ∈ [−width, 0]; silently caused a width-sized
  placement error until caught by bbox inspection. Document or center it.

## D-025 — Dimension checks are world-AABB only; no part-frame target

> **STATUS: OPEN (MINOR).**

- **ID:** D-025 · **Phase:** 3 · **Severity:** MINOR
- A revolute-posed 60 mm platform reads 84.85 mm at 45° because
  `obj.<part>.bbox` is the world AABB. Zero-pose checks are the
  workaround. **Suggested fix:** `frame: part` option on dimension checks.
- **Repro:** `designs/base_j1.py` evaluate at `j1_angle=45`.

## D-026 — Assembly aggregates silently exclude `role="fixture"` parts

> **STATUS: OPEN (MINOR, documented behavior worth a warning).**

- **ID:** D-026 · **Phase:** 3 · **Severity:** MINOR
- `assembly.mass`/CoM count only final parts (612 g vs true ~711 g with
  fixtures in the base group); a stability or CoM check authored without
  knowing this validates the wrong number. **Suggested fix:** aggregate
  metadata listing what was included, or an `include_roles:` option.
- **Repro:** `designs/base_j1.py`, compare assembly.mass vs summed parts.

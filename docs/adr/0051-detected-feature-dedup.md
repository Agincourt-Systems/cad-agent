# ADR 0051: Suppress folded-frame re-detections of authored sheet holes (D-029)

## Status

Accepted for implementation. Stacked on ADR 0040 (holes= API), ADR 0044
(frame: flat DFM), and ADR 0050 (self-describing sheet blank).

## Context

Deficiency **D-029** (MINOR; flagged downstream as "the one most worth an
upstream third round") is a composition failure between two shipped features:

- ADR 0040 lets `bend`/`bend_chain` author fastener holes through a `holes=`
  argument. Each authored hole is published as a `kind="cylindrical_hole"`
  spatial feature in the **flat-pattern** frame, and is also physically bored
  out of the **folded** solid so the mass/volume/render agree with the blank.
- ADR 0044 makes `hole_to_edge` measure in the flat-pattern frame when a check
  declares `frame: flat`, so it composes with `hole_to_bend`.

Because the authored hole exists in the folded solid, STEP auto-detection
(`inspector.py`) re-observes it as a **folded-frame** cylindrical feature. That
re-detection is not the authored publication: it sits in folded 3-D
coordinates, and on most flanges it is detected as a `cylindrical_boss` (its
through-axis is the thin sheet thickness, which is far shorter than the part's
longest extent, so the "through" test fails). It therefore does **not**
deduplicate against the flat-frame publication (different frame, different kind),
and it pollutes the DFM rules.

Two observed symptoms, both reproduced before implementation on a single-bend
bracket authored via `holes=[{"flange": 1, "u": 25, "v": 0, "diameter": 3}]`:

1. **`frame: flat` composition.** `frame: flat` re-frames only the *published*
   flat holes. The auto-detected folded-frame boss is left in folded coordinates,
   so `hole_to_bend` and `hole_to_edge` false-positive on it (observed
   `hole_to_bend = -0.64`, in the same class as the downstream's reported
   `-1.64 / -2.5`) even though the authored hole is compliant and far from the
   bend.

2. **Normal run -> inspect pipeline.** The same re-detection appears as a
   world-frame `cylindrical_boss`. Scoping a check with `kind: cylindrical_hole`
   hides it from `min_hole_diameter` / `hole_to_edge` / `min_web`, but
   `hole_to_bend` needs holes **and** bends in one unfiltered selection, so a
   `kind` filter cannot save it: the boss is treated as a hole (it is in
   `_CYLINDRICAL`), pairs with the bend, and false-positives. The result is that
   `hole_to_bend` has **no provable GREEN path** on a hole-bearing bent part
   authored via `holes=` — only the red case (a hole genuinely too close to the
   bend) is provable, and that red case passes for the wrong reason (the phantom
   fails alongside the real hole).

## Decision

Two changes, both inside this track's owned files (`inspector.py`, `dfm.py`).

### 1 - Suppress folded-frame re-detections at detection-merge time (`inspector.py`)

`_merge_features` gains a suppression pass. A detected cylindrical feature
(`cylindrical_hole` or `cylindrical_boss`) is dropped when it re-observes an
authored sheet-metal hole:

- **Sheet-metal gate.** The feature's owning source object carries at least one
  published `kind="bend"` feature (ADR 0033) — i.e. it is a folded sheet part.
  A plain part has no bend features, so its detected holes are never touched and
  the existing explicit/detected dedup (ADR 0012) is unchanged.
- **Authored-hole gate.** The same source object carries at least one authored
  `kind="cylindrical_hole"` publication (an ADR 0040 hole). A bent part with no
  authored holes keeps all its detected features.
- **Radius match.** The detected radius matches an authored hole radius on that
  source within `_SHEET_HOLE_RADIUS_TOLERANCE` (0.05 mm).

When a re-detection is suppressed, the corroborated authored hole (same source,
same radius) is marked `confirmed_by_detection`, exactly the signal the ADR 0012
dedup already emits, so an agent still learns the authored hole reached the
solid.

**Why radius match and not "same axis line".** The suggested-fix wording asks
for an axis-line + radius match, but that comparison is unavailable here: the
authored hole is published in the flat-pattern frame and the re-detection is in
the folded frame, and for any hole on a folded (angled) flange the two axis
lines genuinely differ (a 90-degree flange hole has flat axis `[0,0,1]` and
folded axis `[1,0,0]`). Reconstructing the fold transform inside the inspector
would require the sheet-metal authoring data (`sheetmetal.py` / `registry.py`),
which this track must not modify. The sheet-metal + authored-hole gate is a
sufficient discriminator on its own: a **folded sheet blank's only full-cylinder
faces are its authored bores**, so a radius match on such a part is a reliable
duplicate signal without any cross-frame geometry. This also matches ADR 0044's
precedent, which suppresses sheet-part detection artifacts (bend arcs) by a
sheet-metal-gated signature rather than by exact geometry.

### 2 - `exclude_detected` filter on manufacturability checks (`dfm.py`)

`evaluate_manufacturability` accepts an optional `exclude_detected: true` on the
check. When set, every feature carrying `detected: true` is removed from the
selection before any rule runs — the `detected: true` exclusion the downstream
explicitly requested. This is defense-in-depth for the case the automatic
suppression above deliberately does **not** cover: a hole modelled as raw
build123d geometry (not through the `holes=` API) on a sheet part, which has no
authored `cylindrical_hole` publication to gate on. An agent that hand-publishes
its holes can then scope with `exclude_detected: true` and get a clean
`hole_to_bend`. The config lives entirely in `dfm.py`'s check-entry path; with
the key absent, behavior is byte-identical.

Auto-detected features already carry `detected: true` (set at detection time in
`_plane_feature`, `_slot_features`, and the cylindrical branch); published and
authored features never carry it. That tag is the contract both changes rely on,
and it is documented here as such.

## Alternatives considered

- **Project auto-detected folded holes into the flat frame** (the ADR 0044
  "future work" option). Correct in principle, but it needs the fold transform,
  which lives in `sheetmetal.py`/`registry.py` (out of this track's ownership).
  Rejected as out of scope.
- **Exclusion filter only, no automatic suppression.** Leaves the normal
  run->inspect pipeline broken unless every agent remembers to add
  `exclude_detected: true`; the deficiency's core complaint is that
  `hole_to_bend` has no provable green path *by default*. Rejected as
  insufficient; kept as the secondary mechanism.
- **Automatic suppression only, no filter.** Fixes the authored-hole case but
  not the raw-geometry case, and ignores the downstream's explicit request.
  Both are cheap, so both ship.
- **Suppress by exact center/kind match in `_is_duplicate`.** Fails across
  frames (centers and kinds differ), as shown above.

## Success criteria

Tests fail before implementation, pass after:

- `test_hole_to_bend_green_on_authored_holes` (**core**): a bent part authored
  via `holes=` with a compliant hole far from the bend passes an **unfiltered**
  `hole_to_bend` check. Before the fix the phantom boss false-positives
  (observed `-0.64`); after, the boss is suppressed and the authored hole passes.
- `test_hole_to_bend_red_when_hole_near_bend` (**core, red direction**): moving
  the authored hole close to the bend fails `hole_to_bend`, citing the
  **authored** hole (`feat.bracket_hole_0`), not a phantom.
- `test_authored_hole_not_duplicated_in_spatial`: `spatial.json` for such a part
  carries the authored `cylindrical_hole` and **no** detected cylindrical
  feature on that source; the authored hole is marked `confirmed_by_detection`.
- `test_frame_flat_composes_with_authored_holes`: the D-029 headline — a
  `frame: flat` check with `hole_to_bend` + `hole_to_edge` on an authored-hole
  bent part passes (no false positive from the folded re-detection), composing
  ADR 0040 + 0044 + 0050 (blank extents from sheet metadata).
- `test_exclude_detected_filter`: a check with `exclude_detected: true` drops a
  detected feature that would otherwise violate a rule; without the key the
  violation stands.
- `test_non_sheet_detection_unchanged`: a plain (non-bent) part's detected
  features are untouched — the sheet-metal gate never fires.

## Failure criteria

Any change to detection or DFM behavior for non-sheet parts; any suppression of
a genuinely distinct feature; any new mandatory argument; regression in the
existing 259 tests (notably the ADR 0012 dedup and ADR 0040 hole-binding tests).

## After Action Report

Pending downstream verification.

- Reproduced the bug before implementing: the single-bend authored-hole bracket
  emits a phantom `feat.auto_bracket_cylindrical_boss_1` at folded center
  `[24.01, -15.0, 30.15]`, and an unfiltered `hole_to_bend` fails with observed
  `-0.64` citing that phantom, exactly the D-029 symptom.
- After the fix the phantom is gone from `spatial.json`, the authored hole is
  marked `confirmed_by_detection`, the unfiltered `hole_to_bend` green path
  passes, and the `frame: flat` composition passes. [full-suite result recorded
  in the final report]

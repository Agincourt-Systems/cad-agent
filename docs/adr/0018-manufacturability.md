# ADR 0018: Laser/Sheet Manufacturability (DFM) Checks

## Status

Accepted for implementation.

## Context

Deficiency D6 in `docs/ca-sheet-metal-fixes.md` observes that the spec lists a
`manufacturability` requirement type that is not implemented — `evaluate.py`
raises `ValueError` on it. SendCutSend and similar laser/waterjet shops reject or
flag parts that violate DFM rules: a minimum hole diameter (≥ material
thickness), minimum slot width, a minimum web/bridge between cutouts, a
hole/slot-to-edge distance (≈ 1× thickness), and — for bent parts — a minimum
bend radius and hole-to-bend distance. Catching these in the harness avoids quote
rejections and rework.

The harness already produces everything these rules need in `spatial.json`: each
detected or published feature carries a `kind`, `center`, `axis`, `diameter`/
`width`/`length`, and `source_object`, and each object carries a bounding box. So
the check can be pure-Python over `spatial.json`, with no CAD kernel on its path,
keeping the evaluator deterministic.

## Decision

Add a `manufacturability` evaluate check whose rule math lives in a new
`src/cadx/dfm.py`; `evaluate.py` gains an import and a one-line router branch.
A requirement is:

```yaml
- id: plate_dfm
  type: manufacturability
  object: obj.plate      # optional feature filter by source_object
  kind: cylindrical_hole # optional feature filter by kind
  material: 6061-T6      # optional, recorded
  thickness: 4           # optional; else the owning object's thinnest bbox dimension
  rules:
    - rule: min_hole_diameter
      min: 3.0           # optional absolute limit
      factor: 1.0        # optional multiplier of thickness (else the per-rule default)
      severity: fail     # optional, default fail; warn surfaces without failing
```

Each rule's limit is an explicit `min` or `factor * thickness` (defaulting to the
per-rule factor in `DEFAULT_FACTORS`). Thickness defaults to the owning object's
smallest bounding-box dimension — the sheet's thickness axis — resolved per owning
object inside each rule.

Rules: `min_hole_diameter` and `min_slot_width` (size below the limit);
`hole_to_edge` (in-plane clearance to the owning object's edge); `min_web`
(edge-to-edge gap between two features on the same object); `min_bend_radius` and
`hole_to_bend` (over explicitly published `kind="bend"` features). A `warn`-
severity rule surfaces in `warnings` without failing. The result record is
`{id, type:"manufacturability", status, material, thickness, violations[],
warnings[]}` with each violation `{rule, severity, features[], observed, limit}`,
so a failure names exactly which feature(s) and by how much.

This is purely additive to the `checks.json` contract; no `spatial.json` or
`diagnostics.json` change, no new exports, no CLI change.

## Success Criteria

Written so the new tests fail before implementation and pass after:

- `test_min_hole_diameter`: a hole smaller than thickness fails naming it; a
  large enough one passes. Fails today because `manufacturability` raises.
- `test_hole_to_edge`: a hole within 1× thickness of an edge fails; centered it
  passes.
- `test_min_web_names_both_features`: two holes whose edges are too close fail,
  naming both; spread apart they pass.
- `test_severity_warn_does_not_fail`: a violated `warn` rule surfaces in
  `warnings` and the check stays `pass`.
- `test_explicit_thickness_and_factor`: an explicit `thickness` and per-rule
  `factor` drive the resolved limit.
- Existing ADR 0001–0017 tests continue to pass; the contract gains only the
  additive `manufacturability` check record.

## Consequences

- The harness can gate a design on real sheet-metal DFM rules before a SendCutSend
  quote, naming the offending feature and the violated limit so an agent can fix
  it.
- The check is deterministic and kernel-free, preserving the evaluator's no-CAD
  property; `bends.json` is consumed by the `bend` check (ADR 0016), while the DFM
  bend rules operate on explicitly published `bend` features (inert otherwise).
- Costing/yield estimation is out of scope; this is a pass/fail (or warn) gate.

## After Action Report

The red state failed as predicted: `manufacturability` raised
`unsupported check type`. The record shape matches the reconciled contract, and
`report.md` renders for a failing manufacturability check (the generic failed-
check block tolerates the absent `observed`/`expected`/`tolerance` keys).

An adversarial review of the diff found **two correctness blockers** in the rule
geometry, both fixed and covered:

1. **`hole_to_edge` was wrong for slots.** `_edge_clearance` excluded the
   *feature's own* `axis`. That is right for a hole (whose axis is the through
   axis), but `inspector._slot_features` records a slot's `axis` as its
   *elongation direction* (in-plane). The rule therefore measured clearance
   against the 4 mm thickness face and spuriously failed any centered through-slot
   while ignoring the slot's length, which can actually overhang the real edge.
   The thickness/through axis is now derived from the owning object's bounding box
   (its thinnest dimension), the in-plane axes are the other two, and a slot's
   per-axis half-extent (length along its long axis, width across it) is subtracted
   — so a centered slot passes and an overhanging one fails
   (`test_hole_to_edge_slot_uses_thickness_axis`). This also fixes any hole whose
   axis is X or Y.
2. **`min_web` paired unrelated unsourced features.** The same-object guard
   `source_a != source_b` was satisfied as `None == None`, so two features with no
   `source_object` were treated as a web. It now requires a non-`None` shared
   source (`test_min_web_ignores_unsourced_features`).

The unknown-rule skip and the slot pass branch also gained coverage
(`test_unknown_rule_and_passing_slot`), and `min_slot_width`/`min_bend_radius`/
`hole_to_bend` active paths are tested. The full suite passed with 87 tests
(76 prior + 11 new), no regressions.

# ADR 0043: `min_flange` DFM rule + bend-radius default policy (D-021, D-022)

## Status

Accepted for implementation.

## Context

Two related sheet-metal DFM gaps, both MINOR (`docs/specs/arm-deficiencies.md`):

**D-022 — no `min_flange` rule.** The `manufacturability` check (ADR 0018) gates
hole size, slot width, edge distance, web width, bend radius, and hole-to-bend
distance, but nothing checks that each **flange is long enough to form**. A press
brake cannot bend a flange shorter than roughly half the die opening plus the
bend radius: the blank has nothing for the tooling to grip, so the bend either
will not form or deforms out of tolerance. A design can therefore pass every
existing rule and still be unmanufacturable because a leg is too stubby to bend.

**D-021 — the `min_bend_radius` default floor is too aggressive for verified
press-brake radii.** The rule defaults to `1.0 * thickness` (see
`DEFAULT_FACTORS` in `src/cadx/dfm.py`). Real fab houses form tighter: SendCutSend
verifies an **0.81 mm effective inside radius on 2.29 mm 5052** (~0.35 t), well
under the generic 1.0 t floor. With the default floor the check *rejects a bend
the shop will actually make*, a false-positive that erodes trust in the gate. The
fix must **not silently weaken the default** (the 1.0 t floor is the right
conservative default for a design with no verified radius table); it must instead
make the escape hatch explicit and documented, so a design working to a shop's
published radius table opts in per-check.

## Decision

### `min_flange` rule (D-022)

Add a `min_flange` rule to `src/cadx/dfm.py`, keyed off the published
`kind="bend"` features (ADR 0033 emits one per bend from `publish_sheet_metal`,
in the **flat-pattern frame**; the same lines appear in `bends.json`). Each bend
feature carries a 2D `line` `[[x0, y0], [x1, y1]]` across the blank width and a
`center` at the line midpoint; the bends of one part share a `source_object`.

The **developed (flange) axis is x** — perpendicular to the bend lines, matching
`bend_chain`'s flat pattern (`Pos(L/2, 0) * Rectangle(developed_length, width)`,
bend lines at `[[bend_x, ±width/2]]`). Each bend's position along that axis is the
x-coordinate of its line midpoint (== `center[0]`). The blank runs from `x = 0` to
`x = blank_length` (the developed length), so:

- The **boundaries** along the strip are, in order: the leading blank edge
  (`x = 0`), each bend position (sorted), and the trailing blank edge
  (`x = blank_length`).
- Each **flange segment** is the gap between two consecutive boundaries. Its
  length must be `>= limit`. A segment bounded by two bends is the **interior web**
  of a U-channel; a segment bounded by a blank edge is an **outer flange**.
- A too-short segment is a violation citing the adjacent bend feature(s): one bend
  for an outer flange, both bends for an interior web.

`blank_length` is supplied on the rule (`{"rule": "min_flange", "blank_length": L}`)
because the developed blank extent is **not** recoverable from `spatial.json`: the
published object's bbox is the *folded* solid (its smallest dimension is a flange,
not the blank), and `publish_sheet_metal` does not serialize the flat pattern's
extent. The design author knows it as `part.developed_length`. This mirrors ADR
0033's already-documented rule that a folded-part `manufacturability` check must
pass an explicit `thickness`. Without `blank_length` the rule still checks every
**interior web** (bend-to-bend, needing no blank edge) but skips the outer
flanges; this documented subset is noted so a single-bend part is only fully
checked when `blank_length` is given.

The limit uses the same forms as every other rule (`_limit`): an absolute `min`
in mm, or `factor * thickness` (thickness-relative, e.g. the `"1.0t"` idea), with
a per-rule default factor. The **default floor is `4.0 * thickness`**: a widely
cited press-brake rule of thumb for the minimum formable leg (about half an
8 t air-bend die opening plus the bend radius). It is deliberately conservative
and documented as such; an explicit `min` overrides it.

### Bend-radius default policy (D-021)

No code weakening of the default. `_limit` already lets an explicit `min` win over
the `factor * thickness` default (`if rule.get("min") is not None: return min`),
and `min: 0` is a valid, documented **disable/escape hatch** (`observed < 0` is
never true, so the rule always passes). This ADR:

- **Verifies** `min:` overrides the default with a test (0.81 mm explicit min on
  2.29 mm stock passes a 0.81 mm bend and still fails a 0.5 mm bend).
- **Documents**, in the `min_bend_radius` docstring and the README DFM section,
  that the 1.0 t default is a *conservative generic floor* and that a design
  working to a fab house's published radius table (e.g. SendCutSend's verified
  0.81 mm on 2.29 mm 5052) **MUST pass an explicit `min`** to opt into the tighter
  verified radius, and may pass `min: 0` to disable the rule entirely.

## Success Criteria

Tests fail before implementation, pass after:

- `test_min_flange_outer_flange`: a single-bend part with a stubby outer flange
  fails `min_flange` naming the bend; lengthening the flange passes.
- `test_min_flange_relative_limit`: the thickness-relative form (`factor`) resolves
  the limit from the part thickness; a flange under `factor * t` fails, over passes.
- `test_min_flange_interior_web`: a two-bend U-channel with a too-short interior
  web fails `min_flange` citing **both** bends; every flange segment is checked.
- `test_min_flange_real_bend_flow`: a folded part published via
  `publish_sheet_metal` (real `kind="bend"` features) with a stubby flange fails.
- `test_min_bend_radius_explicit_sub_t_min`: `min: 0.81` on 2.29 mm stock **passes**
  a 0.81 mm bend (verified radius) and **fails** a 0.5 mm bend (below the min).
- Existing ADR 0018 / 0033 DFM tests continue to pass; the change is additive.

## Consequences

- A stubby, unformable flange is caught before a fab quote, naming the bend an
  operator sees in `bends.json`.
- The bend-radius gate no longer forces a false rejection of a shop's verified
  tighter radius: the design opts in with an explicit `min`, and the conservative
  default still protects designs that declare no verified radius.
- `min_flange`'s outer-flange check needs the developed blank length passed
  explicitly, consistent with the folded-part thickness convention; the interior
  web is checked with no extra input.

## After Action Report

Implemented as designed. The four `min_flange` tests were red before the rule
existed (no evaluator for `min_flange` → no violations → the check passed where a
fail was expected), and green after adding `_rule_min_flange` and registering it.
The D-021 verification test (`test_min_bend_radius_explicit_sub_t_min`) was
**already green before any code change** — confirming `_limit` already lets an
explicit `min` win over the default floor — so D-021 was a documentation and
test-pinning task, not a code change: the `min_bend_radius` docstring, the
`DEFAULT_FACTORS` comment, and the README now record the policy (conservative
1.0 t default, explicit `min` to opt into a shop's verified radius, `min: 0` to
disable). The third assertion in that test pins the motivation: the bare default
does reject the verified 0.81 mm radius, which is exactly why the explicit-`min`
escape hatch matters.

Design notes borne out in implementation:

- The developed-axis position of a bend is `center[0]` (the flat-frame midpoint x
  `publish_sheet_metal` already emits), with a `line`-midpoint fallback, so no new
  data had to be published — honouring the constraint not to touch `registry.py`
  or `sheetmetal.py`.
- `blank_length` on the rule supplies the flat blank extent because it is not in
  `spatial.json` (the object bbox is the folded solid). The real-bend-flow test
  computes it from the bend-allowance formula and passes it, exactly as a design
  author would from `part.developed_length`. Without `blank_length` only interior
  webs are checked — a documented, honest subset rather than a wrong outer-flange
  answer.
- Segment-boundary modelling (blank edge / bend / bend / blank edge) makes the
  interior web fall out naturally and cites **both** bounding bends, matching the
  operator's `bends.json` view.

Full `tests/test_manufacturability.py` suite: 16 passed (11 prior + 5 new), no
regressions. Full repository suite result recorded in ADR 0044's AAR (the two
ADRs are stacked and were validated together at the end).

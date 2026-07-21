# ADR 0042: Documented Width-Extrusion Frame for the Folded Solid (D-024)

## Status

Accepted for implementation.

## Context

Deficiency **D-024** (`docs/specs/arm-deficiencies.md`, PAPERCUT) observes that
the folded solid produced by `bend_chain` / `bend` occupies **`y in [-width, 0]`**
— it is extruded across its width to one side of the `y = 0` plane rather than
centred on it — and that this convention was **undocumented**. A downstream
consumer that assumed a width-centred part (`y in [-width/2, +width/2]`, which is
what the *flat pattern* uses) was off by half a width in `y`, a silent
width-sized placement error that only surfaced during assembly.

The sign convention was verified empirically before writing this ADR (the
deficiency says "verify, do not trust"): the folded solid of a single 40/25
bracket (`width = 30`) has `bbox.min.y = -30.0`, `bbox.max.y = 0.0`, and a
two-bend U-channel (`width = 30`) likewise spans `y in [-30, 0]`. The convention
is real and it is `y in [-width, 0]`, exactly as the deficiency reports. The
cause is mechanical: `_folded_profile` builds the cross-section ribbon on
`Plane.XZ` (whose normal is `-y`) and extrudes it by `+width`, so the material
grows from `y = 0` toward `y = -width`.

## Decision

The **primary deliverable is documentation**: make the width-extrusion frame an
explicit, discoverable contract rather than a surprise.

- The `bend_chain` and `bend` docstrings and the `sheetmetal` module docstring
  state the folded-solid frame precisely: the base flange's lower fibre lies on
  `z = 0`, the developed length runs along `+x`, and the part is extruded across
  its width to **`y in [-width, 0]`** (not centred on `y = 0`; the flat pattern,
  by contrast, is width-centred on `y in [-width/2, +width/2]`).
- The README sheet-metal section documents the same frame so an assembly author
  reads it without opening the source.

The **default geometry frame is unchanged** (`y in [-width, 0]`): downstream now
depends on it, and ADR 0034's volume-conserving construction and every existing
bounding-box test are preserved bit-for-bit.

Two regression tests pin the folded-solid bbox frame — one single bend, one chain
— asserting `bbox.min.y == -width` and `bbox.max.y == 0` (and `x`/`z` extents
unchanged), so any future construction change that silently re-frames the part is
loud rather than a repeat of D-024. These characterize existing behaviour (they
are green on the current code by design — a regression pin has no red phase).

### Opt-in `center_width`

Because the half-width offset is the exact footgun D-024 describes, `bend_chain` /
`bend` gain an **opt-in** `center_width=False` keyword. When `True`, the folded
solid is translated by `+width/2` in `y` after construction so it spans the
width-centred `y in [-width/2, +width/2]` — aligned with the flat pattern and with
the symmetric convention most assembly math expects. It changes **only** the
folded solid's `y` position: the flat pattern, bend lines, `bends.json`, and the
bend/hole spatial features are all in the flat-pattern frame and are untouched;
volume and every other extent are identical (a rigid translation). The default
stays `False`, so all existing behaviour and downstream dependence is unchanged.
This is a genuinely new behaviour and is red/green tested (before: `TypeError` on
the unknown keyword; after: centred bbox).

## Success Criteria

Written in `tests/test_sheet_metal_bend.py`:

- `test_folded_solid_width_frame_single` / `test_folded_solid_width_frame_chain`
  (regression pins): the folded bbox spans `y in [-width, 0]` for a single bend
  and for a U-channel chain.
- `test_center_width_centres_folded_solid` (red/green): with `center_width=True`
  the folded bbox spans `y in [-width/2, +width/2]`, the volume is unchanged, and
  the flat-pattern bend line is still at its flat developed x (placement of the
  fold does not move the flat frame). Before implementation the keyword is a
  `TypeError`.
- `test_center_width_defaults_false` (red/green safety): the default folded frame
  is `y in [-width, 0]`, byte-identical to before.

## Consequences

- The width-extrusion frame is documented, so the D-024 half-width placement
  error is now attributable to a stated contract instead of a silent surprise.
- Assemblies that want a symmetric part opt into `center_width=True` and avoid the
  offset entirely; those that depend on the historical frame are unaffected.
- If more sheet-metal frame options are needed later (e.g. base flange centred on
  `z`), they follow this opt-in pattern.

## After Action Report

The empirical check confirmed the deficiency verbatim: a single 40/25 bracket
(`width = 30`) folds to `bbox.y in [-30, 0]` and a two-bend U-channel likewise to
`y in [-30, 0]` — the frame is `y in [-width, 0]`, as D-024 states, caused by the
`Plane.XZ` (normal `-y`) extrude by `+width`.

The two regression pins (`test_folded_solid_width_frame_single` / `_chain`) and
the default test are green on the current code by construction — a regression pin
has no red phase; they exist so a future re-framing of the folded solid fails
loudly. The `center_width` behaviour is genuinely new and was red first
(`bend(..., center_width=True)` raised `TypeError: unexpected keyword argument`)
and green after: with `center_width=True` the folded bbox spans
`y in [-15, +15]`, the volume matches the default to `rel=1e-9` (a rigid
translation), and the flat-pattern bend line stays at `FLANGE_A + BA/2`. The
default remains `False`, so the historical frame downstream depends on is
untouched.

Documentation shipped in three places — the module docstring, the `bend_chain` /
`bend` docstrings, and the README sheet-metal section — so the width-extrusion
frame is discoverable without reading the source, closing the "undocumented"
half of D-024. The full sheet-metal suite passes.

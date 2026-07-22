# ADR 0050 — Self-describing sheet blank: serialize blank extents and thickness

- **Status:** accepted
- **Date:** 2026-07-21
- **Deficiencies:** residuals of D-021/D-022/D-023 (ADRs 0043, 0044) and the
  ADR 0033 explicit-thickness note (D-004 verification)

## Motivation

ADRs 0040–0044 fixed the sheet-metal deficiency cluster in two parallel
tracks. Each track shipped complete, but each left the same class of residual:
a DFM rule that needs a fact about the flat blank must receive that fact as an
explicit check or rule parameter, because `spatial.json` did not record it.

Three explicit parameters remained after the two tracks merged:

1. `min_flange` (ADR 0043) needs `blank_length` on the rule. Without it, the
   rule checks only interior webs and skips the outer flanges.
2. `hole_to_edge` with `frame: flat` (ADR 0044) needs `blank_length` and
   `blank_width` on the check.
3. Every thickness-relative rule on a folded part needs an explicit
   `thickness`, because the fallback (the owning object's smallest bounding
   box dimension) reads the FOLDED solid. On a folded bracket the smallest
   bbox dimension is usually the strip WIDTH, not the sheet thickness — a
   silently wrong limit up to an order of magnitude too large. ADR 0033
   documented this trap; it did not remove it.

All three facts are known exactly where the part is made: `bend_chain` computes
the developed length, receives the width and the thickness. The producer knows;
the consumer guesses. This ADR moves the facts into `spatial.json` so the DFM
rules read them from the part record — the agent-consumer principle that a
record should carry its own interpretation (same rationale as ADR 0037's
inertia semantics).

## Decision

1. `SheetMetalPart` gains `thickness` and `width` fields (default `None`, so
   hand-built parts and older pickles stay valid). `bend_chain` fills them.
2. `publish_sheet_metal` serializes a `sheet` block into the published entry's
   metadata when the part carries the dimensions:
   `{"blank_length": developed_length, "blank_width": width, "thickness": t}`.
   A user-supplied `sheet` metadata key wins — the block is only added when
   absent. Entries whose part carries no dimensions get no block.
3. `dfm.py` reads the block as a fallback, explicit parameters always win:
   - `_resolve_thickness`: explicit `check["thickness"]` → owning object's
     `metadata.sheet.thickness` → smallest bbox dimension (unchanged last
     resort).
   - `min_flange`: rule `blank_length` → per-object `metadata.sheet.blank_length`.
   - `hole_to_edge` with `frame: flat`: check `blank_length`/`blank_width` →
     per-feature owning object's `metadata.sheet` values.

## Success criteria

- A `bend_chain` part published through `publish_sheet_metal` carries
  `metadata.sheet` with the three keys in `spatial.json`, equal to the
  authored dimensions.
- `min_flange` with NO `blank_length` parameter flags a short outer flange on
  such a part (previously silently skipped).
- `hole_to_edge` with `frame: flat` and NO blank dimensions on the check flags
  a hole near the blank edge of such a part.
- A thickness-relative rule (e.g. `min_bend_radius`) with NO explicit
  `thickness` uses the true sheet thickness from the metadata, not the folded
  bbox minimum (the width), on such a part.
- Explicit parameters still override every fallback; parts without the
  metadata behave byte-identically to before.

## Failure criteria

Any change to rule behavior for non-sheet parts or for checks that pass
explicit parameters; any new mandatory argument.

## After Action Report

Shipped in the same session as ADRs 0040–0049 (coordinator knit). All four
success criteria pinned by `tests/test_sheet_blank_metadata.py` (red first:
the metadata block absent, the outer flange skipped, the flat-frame check
inert, the bbox-width thickness trap). Downstream gate scripts can drop the
explicit `blank_length`/`blank_width`/`thickness` parameters for parts
published through the sheet-metal API; explicit values remain supported and
authoritative. Residual (unchanged from ADR 0044): flat-frame projection of
auto-detected folded-frame holes.

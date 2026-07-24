# ADR 0057 — Per-feature tolerance / fit metadata (D-032)

- **Status:** accepted
- **Date:** 2026-07-23
- **Deficiency:** D-032 (MINOR) — no tolerance / fit / GD&T representation

## Motivation

A precision feature carries a fit, not just a nominal size. A 16 mm bearing
seat is `16 H7`; an 8 mm dead shaft is `8 g6`. Today cadx records the nominal
geometry of a hole but has no place to state its fit. The downstream harness
keeps these facts in a sidecar file (`output/TOLERANCES.md`), so a STEP or
`spatial.json` consumer never sees them. The feature record and the tolerance
that governs it live in two documents that can drift.

The deficiency report pre-approves the sidecar workaround but asks for the
upstream capability: per-feature tolerance metadata that flows with the feature
into the exports and the BOM, so one record carries its own interpretation
(the same agent-consumer principle as ADR 0037 inertia semantics and ADR 0050
sheet-blank metadata).

## Decision

1. **Schema.** `publish_feature(...)` accepts an optional `tolerance=` keyword,
   a plain dict that is stored verbatim on the feature record and serializes
   as-is. The dict admits a fixed, closed set of top-level keys:

   | key         | type   | meaning                                              |
   |-------------|--------|------------------------------------------------------|
   | `fit`       | str    | ISO tolerance-class / fit designation, e.g. `"H7"`, `"g6"` — passed through **verbatim** |
   | `nominal`   | number | nominal size the fit applies to (mm)                 |
   | `tol_plus`  | number | upper deviation (mm)                                 |
   | `tol_minus` | number | lower deviation (mm)                                 |
   | `note`      | str    | free-text note                                       |

   Every key is optional; a design supplies whichever it knows. The example
   forms are `tolerance={"fit": "H7", "nominal": 16.0}` (a named fit) and
   `tolerance={"tol_plus": 0.02, "tol_minus": -0.01, "note": "press fit"}`
   (discrete deviations).

2. **Light validation at publish time.** `fit` strings pass through unchanged —
   cadx does **not** build an ISO 286 deviation table and does not resolve a fit
   into `tol_plus`/`tol_minus` (out of scope; a fit string is opaque to cadx).
   `note` must be a string. `nominal`, `tol_plus`, `tol_minus` must be real
   numbers (booleans are rejected — `bool` is an `int` subclass but is never a
   size) and are normalized to `float` for a stable record shape. Any key
   outside the table above raises a `ValueError` naming the offending key, so a
   typo (`{"ft": "H7"}`) fails loudly at the design line that made it instead of
   vanishing silently. An empty `tolerance={}` is rejected for the same reason —
   it carries no fit and is almost always a mistake.

3. **Passthrough to `spatial.json`.** No change to `runner.py` / `worker.py` /
   `inspector.py` is needed. A published feature's arbitrary keys already flow
   unchanged: `publish_feature` → `_FEATURES` → `snapshot_registry` (deepcopy)
   → `diagnostics.json` `features` → `inspector._merge_features` (`dict(feature)`
   copy) → `spatial.json`. Prior ADRs (0033 bend fields, 0040 hole fields, 0050
   `sheet` block) added feature/metadata keys the same way with no normalization
   edit. The `tolerance` dict rides that path untouched, so it lands on the
   feature record in `spatial.json` verbatim — visible to any STEP/spatial
   consumer.

4. **Aggregation surface — the BOM.** cadx already has a per-run aggregator,
   `cadx bom` (`bom.py`), which reads `spatial.json`. `build_bom` now emits a
   `tolerances` array in `bom.json`: one entry per toleranced feature,
   `{feature, kind, object, tolerance}`, sorted by feature id for
   determinism. `object` is the owning part label derived from the feature's
   `source_object` (`obj.<label>` → `<label>`), or `null` for a feature with no
   owner. This gives a fabricator a single flat list of every fit in the run
   alongside the part rows. `bom.csv` is unchanged (its fixed per-part column
   set has no place for per-feature fits); the machine-consumable `bom.json` is
   where the tolerance list lives.

### Why the BOM and not a `spatial.json` top-level `tolerances` block

A top-level summary array in `spatial.json` would have to be written in
`inspector.inspect_run`, which is owned by a concurrent track and off-limits
this round. The per-feature passthrough (item 3) already puts every tolerance
into `spatial.json` on its feature — the primary deliverable — so a consumer
reading `spatial.json` sees them. The BOM is the natural run-level *rollup* and
is a surface this ADR may extend, so the aggregation lands there.

## Alternatives considered

- **Discrete top-level feature keys** (`fit=`, `tol_plus=` … directly on the
  feature) instead of a nested `tolerance` dict. Rejected: it pollutes the
  feature namespace, which already carries geometry keys (`diameter`, `center`,
  `axis`), and makes "does this feature have a tolerance?" a multi-key test
  rather than one `"tolerance" in feature` check. A single dict groups the
  metadata and keeps the passthrough and BOM rollup trivial.
- **Resolving fits into deviations via an ISO 286 table.** Rejected: out of
  scope and a maintenance liability. A fit string is authoritative on its own;
  a consumer that needs deviations owns the lookup.
- **Top-level `tolerances` in `spatial.json`.** Rejected this round: requires
  editing `inspector.py` (another track owns it). See above.

## Scope exclusions

- **STEP / DXF embedding of tolerances is OUT OF SCOPE.** Attaching GD&T to the
  exported solid is STEP AP242 semantic PMI — a project of its own. This ADR
  carries tolerances in the JSON records (`spatial.json`, `bom.json`) only; the
  STEP/DXF geometry is unchanged.
- No ISO 286 fit-to-deviation table (item 2 above).

## Success criteria

Written so the new `tests/test_feature_tolerance.py` fails before and passes
after implementation:

- A feature published with `tolerance={"fit": "H7", "nominal": 16.0}` through a
  real `cadx run` carries that exact dict on its record in `spatial.json`.
- `fit` strings (`"H7"`, `"g6"`) survive verbatim.
- A feature published with **no** `tolerance` has no `tolerance` key —
  byte-identical to before this ADR.
- An unknown key inside `tolerance` raises `ValueError` naming the key;
  a non-numeric `tol_plus`, a boolean numeric, and an empty dict are rejected.
- `cadx bom` emits a `tolerances` array in `bom.json` listing every toleranced
  feature with its owning object and tolerance dict.
- The full existing suite stays green.

## Failure criteria

Any change to the serialization of features that carry no tolerance; any edit to
`runner.py`/`worker.py`/`inspector.py`; any new mandatory argument on
`publish_feature`; a fit string mutated on its way through.

## After Action Report

AAR: pending downstream verification. The per-feature passthrough required no
normalization edit (confirmed: features flow through `inspector._merge_features`
as `dict(feature)` copies, so arbitrary keys survive). The aggregation shipped
as the **BOM variant** — a `tolerances` array in `bom.json` — because the
`spatial.json` top-level would require editing another track's `inspector.py`.
The per-feature tolerance is present in `spatial.json` regardless; the BOM adds
the run-level rollup. No residuals: no runner edit was needed. STEP/DXF GD&T
embedding remains deferred by design (AP242 PMI).

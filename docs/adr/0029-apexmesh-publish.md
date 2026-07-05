# ADR 0029: Publish run directories to apexmesh (`cadx publish`)

- Status: accepted (2026-07-05)
- Predecessors: ADR 0017 (BOM package — the `bom.json` this exports), the
  runner's per-run artifact layout (`diagnostics.json` exports index,
  `checks.json`, `source_snapshot.py`, `params.resolved.yaml`).
- References: `../apexmesh/design-v2.md` (pipeline hub design), apexmesh
  ADR-0010/0011/0013 (artifact store, run registry + lineage, Python
  client); `src/cadx/publish.py`, `src/cadx/cli.py`, `tests/test_publish.py`.

## Context

apexmesh is the pipeline's single source of truth: configs, artifacts, runs,
lineage, and BOM live there with audit attribution. cadx already snapshots
everything relevant into numbered run dirs, but those dirs are local: nothing
upstream (the trade study that recommended the configuration) or downstream
(the future CFD agent) can query which STEP came from which design source at
which parameters, and the BOM the design declares never reaches the hub's
parts/revisions model.

## Decision

**`cadx publish <run_dir> --project <name>`** — a read-only exporter over an
existing run dir, printing one JSON object like every other subcommand.
`src/cadx/publish.py` splits a pure planning layer from a thin execution
layer over `apexmesh-client` (lazy import with an install hint; cadx runs
fine without it; credentials via `APEXMESH_URL`/`APEXMESH_TOKEN`, an
apexmesh *agent* token).

- **Run identity**: `external_ref = "<project-dir>:<run-number>"` (e.g.
  `chupacabra-configuration:0010`), tool `cadx`, version from package
  metadata. Status mirrors reality: `failed` when `diagnostics.json` records
  errors, else `succeeded`. Metrics: check pass/fail counts, export count,
  error count.
- **Inputs**: `source_snapshot.py` (role `design_source`) and
  `params.resolved.yaml` (role `params`), kind `config` — together they are
  the run's exact reproducible definition.
- **Outputs**: every export recorded in `diagnostics.json` (STEP/STL/GLB/DXF
  → kinds `cad_step`/`cad_stl`/`cad_glb`/`cad_dxf`, role = part label);
  `checks.json`/`diagnostics.json`/`spatial.json`/`bom.json`/`bom.csv` as
  `manifest`; `report.md` as `report`; `views/*.png` as `image`. Missing
  optional files skip silently — a render-less run still publishes.
- **BOM mapping** (when `bom.json` exists): an assembly part
  `<PROJECT>-ASSY` plus one part per BOM row (part number = declared
  `part_number` or `<PROJECT>-<label>`), each getting a **revision named
  after the run number** (attributes: material, thickness, finish, process,
  vendor, costs, derived geometry), and one BOM line assembly→part per row
  with the row quantity. Parts and revisions upsert (409 → reuse existing),
  so republishing or later runs revise rather than duplicate.
- **Republish guard**: a succeeded run with the same `external_ref` refuses
  without `--force`.

Rejected alternatives:

- *Publishing from inside `cadx run`* — couples design execution to hub
  availability; the loop can call `publish` as a separate step.
- *apexmesh-side importer* — the export semantics (labels, part_meta, check
  meaning) live here; design-v2 decision 7 chose tool-side hooks.
- *Mapping parts to graph nodes* — apexmesh's parts/revisions/BOM model is
  the right home; node links can come later via its `node_part_links`
  stretch design.

## Success criteria

- Fast suite (no network, no build123d): plan building over a synthetic run
  dir (exports mapped to kinds/roles, inputs, manifests, views, BOM
  presence/absence), status derivation from diagnostics errors, and
  execution sequencing against a fake client (guard → run → artifacts → BOM
  upserts with 409 reuse → succeeded), including `--force`.
- A real run dir (`../chupacabra-configuration/artifacts/runs/0010`)
  publishes into a live apexmesh with lineage back to the trade study —
  exercised in apexmesh ADR-0018 (chupacabra backfill).

## Failure criteria

- Per-run revisions bloat the parts table across many iterations → revise
  only on content change (hash the row payload) or collapse to design-level
  revisions.
- Upload volume of full run dirs annoys → publish assembly-level exports
  only by default with `--all-parts` opt-in.

## After Action Report

*2026-07-05:* Fast-layer success criteria met: 12 tests red→green (plan
identity/status/metrics, export→kind/role mapping, input specs, manifests/
views, BOM row→part specs with declared-part-number precedence, missing-file
tolerance, fatal missing diagnostics; execution sequencing, failed-status
publish, republish guard + --force, 409 revision reuse). Full suite 154 →
166 green (5m31s — the publish tests add negligible time). The live-publish
criterion (run 0010 of ../chupacabra-configuration into apexmesh with
lineage to the trade study) lands with apexmesh ADR-0018; update this AAR
then. Watch items from failure criteria (per-run revision bloat, upload
volume) — assess after the backfill publishes real volumes.

# ADR 0046: Assembly Aggregate Role Visibility and Opt-In Inclusion

## Status

Accepted for implementation.

## Context

Deficiency **D-026** (`docs/specs/arm-deficiencies.md`) observes that the assembly
mass/center-of-mass/inertia aggregate (`inspector._assembly_center_of_mass`,
ADR 0015/0036) silently excludes every part whose `role` is in
`_NON_PHYSICAL_ROLES` (`fixture`, `reference`, `datum`, `keepout`). That default
is correct — a keep-out volume or a datum plane is not mass — and it is documented
in the inspector source. But the `assembly` record itself is *not self-describing*:
it reports `{center_of_mass, mass, weighting, part_count, inertia}` and says
nothing about which parts were dropped or by what rule.

Two concrete problems follow. First, the `role` is overloaded: `fixture` names
both a throw-away assembly jig (rightly excluded from as-built mass) *and* a
permanently-mounted counterweight (which *is* as-built mass). A stability or
center-of-mass check authored without reading the inspector source validates
612 g when the true as-built mass is ~711 g, with no signal that ~99 g of declared
geometry was skipped. Second, there is no supported way to opt a chosen role back
in: the author must re-implement the mass rollup in their own layer.

The fix is two independent, small surfaces: **make the aggregate say what it
counted and what it excluded**, and **let a design opt a role back in**.

### Where assembly options can flow

`inspect_run(run_dir)` is the sole caller of `_assembly_center_of_mass`, and it is
invoked both by the worker (after a run) *and* standalone by `cadx inspect`. It
receives no config today — it reads everything from `diagnostics.json` on disk.
There is no existing run-level "assembly options" channel: `publish` metadata is
per-part, and design `params` are the design's own YAML, not harness config.

Rather than overload per-part `publish` metadata for an assembly-wide policy (which
role is counted is a property of the *assembly*, not of one part), this ADR adds a
tiny run-level declaration that persists into `diagnostics.json`, so both entry
points into `inspect_run` read the same option with no new argument plumbing
through the CLI. This is the cleanest of the candidates: it is metadata-driven (no
environment variable), assembly-scoped (matches the semantics), and works
identically for a fresh run and a re-inspected one.

## Decision

### Part 1 — self-describing aggregate

`_assembly_center_of_mass` gains two additive keys on the `assembly` record:

```json
"included_roles": ["final", "part"],
"excluded": [{"label": "jig", "role": "fixture"}]
```

* `included_roles` — the sorted distinct roles of the parts that actually
  contributed to the aggregate (parts that passed the role filter *and* had a
  positive volume and a centroid). It answers "what counts as mass here?".
* `excluded` — one `{label, role}` entry for every object dropped **specifically
  by the role filter**, in publication order. A part dropped for a *different*
  reason (missing volume/centroid) is not a role exclusion and does not appear
  here; that keeps `excluded` a precise answer to "what did the role rule remove?"
  rather than a catch-all.

Both keys are always present (an empty `excluded` list when nothing was role-
excluded), so a consumer never has to distinguish "no exclusions" from "this cadx
version doesn't report them". `part_count` is unchanged and documented explicitly
as the count of *included* contributing parts (it already equalled
`len(contributions)`); `included_roles`/`excluded` remove the ambiguity about what
that number covers.

### Part 2 — opt-in inclusion

A new run-level authoring helper `assembly_options(*, include_roles=[...])` in the
registry records a list of roles to count even though they are normally non-
physical. Mechanism, end to end:

1. **Registry.** A module-level `_ASSEMBLY_OPTIONS` dict, reset by
   `clear_registry`, set by `assembly_options`, and returned by
   `snapshot_registry` under an `assembly_options` key. Exported from
   `cadx/__init__` so design files can call it.
2. **Worker.** `execute_worker` writes the snapshot's `assembly_options` into
   `diagnostics.json` — but only when non-empty, so a run that declares no options
   produces a byte-identical diagnostics file to before this ADR.
3. **Inspector.** `inspect_run` reads `diagnostics["assembly_options"]["include_roles"]`
   (defaulting to none) and passes it to `_assembly_center_of_mass(objects,
   include_roles=...)`. The aggregation removes the opted-in roles from the
   still-excluded set (`excluded_roles = _NON_PHYSICAL_ROLES - set(include_roles)`),
   so an opted-in `fixture` flows through the *same* qualifying-parts list as any
   physical part. Mass, center of mass, **and** the ADR 0036 inertia tensor
   therefore all reflect it consistently — they are composed from one list, so
   they can never disagree about which parts are present.

`_assembly_center_of_mass(objects, include_roles=None)` keeps its old single-
argument call working (every existing caller and test passes only `objects`), so
the change is backward compatible.

## Success Criteria

Written so the new tests fail before implementation and pass after.

- `test_assembly_reports_included_roles_and_excluded` (unit): two `role="part"`
  boxes and one `role="fixture"` box yield `included_roles == ["part"]`,
  `excluded == [{"label": <fixture>, "role": "fixture"}]`, and `part_count == 2`.
- `test_excluded_omits_non_role_skips` (unit): a `role="part"` object with no
  volume is *not* listed in `excluded` (it was skipped for missing data, not by
  role), and does not inflate `part_count`.
- `test_include_roles_counts_fixture_mass_com` (unit): with
  `include_roles=["fixture"]`, the same three boxes count all three; the mass and
  center of mass match the hand computation over all three, `excluded == []`, and
  `"fixture"` appears in `included_roles`.
- `test_include_roles_extends_inertia` (unit): with `include_roles=["fixture"]`
  and every part carrying a tensor, the aggregate `inertia.tensor` reflects the
  fixture too (matches the three-box closed form), confirming mass/CoM/inertia
  stay consistent.
- `test_default_assembly_block_adds_only_metadata_keys` (unit): a default run's
  `mass`, `center_of_mass`, `weighting`, and `part_count` are unchanged and only
  the two new keys are added.
- `test_include_roles_end_to_end` (build123d, through `cadx run`): a design that
  calls `assembly_options(include_roles=["fixture"])` produces an `assembly.mass`
  covering the fixture, while an otherwise identical design without the call does
  not — proving the registry→diagnostics→inspector path.
- Existing ADR 0015/0035/0036 assembly tests continue to pass; the schema-pin
  tests that enumerate the assembly block are extended in the same commit as the
  feature (see below).

### Schema-pin note

Existing tests assert individual `assembly` keys by value (`part_count`, `mass`,
`weighting`, …) but none assert the *exact* key set, so adding `included_roles`
and `excluded` does not break a pin. Any test that is tightened to enumerate the
full key set is updated in the same commit as the feature, and the commit message
says so.

## Consequences

- The assembly aggregate is self-describing: a stability check reads
  `excluded`/`included_roles` and knows immediately whether a counterweight it
  cares about was counted, instead of silently validating the wrong mass.
- A design can count a chosen non-physical role in mass/CoM/inertia with one
  `assembly_options(include_roles=[...])` call, with no bespoke re-implementation
  of the rollup — and because the inertia tensor is composed from the same
  qualifying list, opting a role in never desynchronizes mass from inertia.
- A run that declares no options is byte-identical in `diagnostics.json` and only
  gains two keys in `spatial.json`'s assembly block; nothing else moves.

## After Action Report

The red state failed as predicted: the assembly record had no `included_roles` /
`excluded` keys (KeyError) and `_assembly_center_of_mass` rejected the
`include_roles` keyword (TypeError) — all 6 new tests failed before the change.

Both parts landed as designed. Part 1 (self-description) fell out of the existing
qualifying loop: the role check now records a `{label, role}` entry on the *role*
branch before `continue`, while the missing-data skip below it deliberately
records nothing — so `excluded` answers precisely "what did the role rule remove?"
and `test_excluded_omits_non_role_skips` pins that a volumeless part is a data
skip, not a role exclusion. Part 2 (opt-in) reduced to a one-line set difference
`excluded_roles = _NON_PHYSICAL_ROLES - opted_in`; because the opted-in role then
flows through the same qualifying list, mass, CoM, and the ADR 0036 inertia tensor
all pick it up with no extra code, which `test_include_roles_extends_inertia`
verifies against the three-box closed form.

The chosen plumbing — a run-level `assembly_options` registry declaration
persisted into `diagnostics.json` — proved to be the right seam: `inspect_run`
reads it from disk, so the opt-in works identically for a fresh `cadx run` and a
later `cadx inspect`, and the CLI needed no new argument. Writing the diagnostics
key only when non-empty kept every option-free run byte-identical, so no existing
diagnostics-shape test moved.

Schema pins: no existing test asserted the full assembly key set, so none broke;
the new `test_default_assembly_block_adds_only_metadata_keys` pins the exact set
(the pre-ADR four keys plus `included_roles`/`excluded`) and was added in the same
commit as the feature, as ADR discipline requires.

No design changes were needed during implementation. All 6 new tests pass; the
existing ADR 0015/0035/0036 assembly, center-of-mass, and inertia tests remain
green. Full suite: 212 passed (201 prior + 5 for ADR 0045 + 6 for ADR 0046), no
regressions.

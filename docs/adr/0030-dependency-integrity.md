# ADR 0030: Dependency integrity — cap build123d, declare ezdxf

- Status: accepted (2026-07-21)
- Predecessors: ADR 0013 (DXF flat-pattern export — the `ezdxf` parse-back
  tests this ADR makes installable), the assembly/interference checks
  (`test_assembly_placement.py`) and the renderer (`renderer.py`) that the
  build123d cap protects.
- References: `docs/specs/arm-deficiencies.md` D-001, D-002;
  `pyproject.toml`; `tests/test_packaging.py` (new).

## Context

The arm bring-up (spec §0) tried a fresh, README-faithful install
(`pip install -e .[cad,render,test]`, then `pytest`) and hit two dependency
integrity defects logged as D-001 (MAJOR) and D-002 (PAPERCUT).

**D-001 — the declared dependency range admits a broken build123d.**
cadx declares `build123d>=0.10` with no upper bound. build123d 0.11.0 and
0.11.1 both satisfy that range, and both break cadx in two distinct classes:

1. **Silent wrongness (the dangerous class):** the `interference` check
   returns `pass` on genuinely overlapping parts. `intersect()` behavior
   changed in 0.11, so `test_interference_detects_overlap` and
   `test_motion_envelope_sweep_catches_interference` no longer detect the
   collision — an assembly with colliding geometry evaluates clean, with no
   error and no warning. This is wrong-but-plausible output on the critical
   path.
2. **Crashes:** `Compound(children=pieces)` in the renderer
   (`renderer.py`, the assembly-combine path) raises
   `anytree TreeError: Cannot add non-node object [Face]` on 0.11's changed
   Compound children handling, failing `cadx render` on every multi-part run
   (7 render/material tests).

Eleven of 166 tests fail on 0.11; the full suite is 166/166 green on 0.10.0.
build123d version is the only variable (reproduced on CPython 3.10, 3.12,
3.14). The upstream project treats these as intended 0.11 API changes, so the
fix is a version cap, not a monkey-patch.

**D-002 — a load-bearing test dependency is undeclared.** The DXF parse-back
tests `importorskip("ezdxf")`. A README-faithful install of the declared
extras never brings in `ezdxf`, so those tests *silently skip* rather than
fail — the operator believes DXF verification ran when it did not. DXF
correctness is load-bearing downstream (SendCutSend flat patterns), so a
skip here is a hole in the safety net, not a convenience.

## Decision

**Cap build123d and declare ezdxf, and guard both with a packaging test.**

1. **Cap the CAD extra** to `build123d>=0.10,<0.11`. This is the smallest
   change that restores a correct, reproducible install: it keeps every
   supported 0.10.x patch release while excluding the 0.11 line that breaks
   the interference check and the renderer.

   We explicitly **do not** adopt the 0.11 API in this ADR. Migrating to 0.11
   is a separate, larger piece of work (a future ADR) that must fix, at
   minimum, the two failure classes recorded above:
   - restore overlap detection in the `interference` check under 0.11's
     changed `intersect()`/boolean semantics; and
   - rebuild the renderer's assembly-combine path so `Compound`/`Part`
     children are assembled without the `anytree` `TreeError`.
   The cap buys time to do that migration deliberately, behind its own
   red→green test evidence, instead of under a broken suite. A CI job pinned
   to the *latest* build123d would surface the next such break early; that is
   recommended follow-on, out of scope here.

2. **Add `ezdxf` to the `test` extra.** DXF verification is part of the test
   contract, so the dependency that makes it run belongs with the other test
   dependencies. A single `[cad,render,test]` install now runs the DXF
   parse-back tests instead of skipping them. (A separate `dxf` extra was
   considered and rejected: `ezdxf` is used only by tests today, so co-locating
   it with `pytest` keeps the install story to one extra and avoids a second
   extra that every contributor must remember.)

3. **Guard both with `tests/test_packaging.py`.** A small, non-trivial test
   parses `pyproject.toml` and asserts the invariants directly, so a future
   edit that widens the range or drops `ezdxf` fails loudly instead of
   silently regressing D-001/D-002. The test evaluates the *version
   specifier* (via `packaging.specifiers`) rather than string-matching, so it
   holds `0.11.0` and `0.12` out of the allowed set regardless of how the cap
   is written, and confirms a representative 0.10.x patch stays allowed.

## Success criteria

- `tests/test_packaging.py` asserts (a) the resolved build123d specifier
  admits `0.10.0` but rejects `0.11.0`/`0.12.0`, and (b) `ezdxf` appears in
  the `test` extra. Red before the `pyproject.toml` edit, green after.
- Full suite remains 166/166 green (the cap does not change behavior on the
  already-installed 0.10.0; the new packaging test adds to the count).

## Failure criteria

- If a future 0.10.x patch itself regresses cadx, the lower bound may need to
  pin a specific known-good patch — revisit the cap then.
- If the 0.11 migration lands, this cap is lifted by that ADR, and the
  packaging test's upper-bound assertion is updated to the new supported
  ceiling in the same change.

## After Action Report

*2026-07-21:* Both packaging guards landed red→green.
`tests/test_packaging.py` first failed on both assertions (the range admitted
`0.11.0`; `ezdxf` was absent from the `test` extra), then passed after the
`pyproject.toml` edit capped `build123d>=0.10,<0.11` and added `ezdxf>=1.0`.
The test evaluates the version specifier semantically via `packaging`, so it
holds `0.11.0`/`0.11.1`/`0.12.0` out while keeping `0.10.0` in, independent of
how the cap is spelled. The environment already had build123d 0.10.0 and
ezdxf 1.4.4 installed, both inside the new bounds, so no behavior on the
critical path changed — the DXF parse-back tests were already running here,
and this ADR makes a clean `[cad,render,test]` install reproduce that state.
Full suite 166 → 168 green (the two packaging tests are the only additions;
run time unchanged). D-001's build123d-0.11 failure classes are recorded above
for the future adopter; the 0.11 migration and a latest-build123d CI job
remain open, out of scope here.

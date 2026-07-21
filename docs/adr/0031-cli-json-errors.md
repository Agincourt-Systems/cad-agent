# ADR 0031: CLI JSON error contract + artifact/loop documentation fixes

- Status: accepted (2026-07-21)
- Predecessors: the CLI contract established across the harness — every
  subcommand prints exactly one JSON object to stdout (`src/cadx/cli.py`,
  `tests/test_cli_contract.py`); ADR 0013 (DXF flat-pattern export — the
  auto-flatten behavior D-015 documents).
- References: `docs/specs/arm-deficiencies.md` D-010, D-011, D-012, D-015;
  `src/cadx/cli.py`; `tests/test_cli_contract.py`; `README.md`.

## Context

The arm modeling probes (spec §0) surfaced four CLI/documentation defects.
One is a machine-contract break; three are documentation gaps where the
observed behavior contradicts what the README implies.

**D-010 (PAPERCUT, contract break) — the CLI emits raw tracebacks.** The CLI
promises one JSON object per subcommand so an agent caller never scrapes
human logs. But `cadx inspect /nonexistent/xyz` (and `evaluate`, and any
subcommand whose reader hits a missing file) raises `FileNotFoundError` all
the way out: stdout is empty, a Python traceback lands on stderr, exit is 1.
A machine caller that does `json.loads(stdout)` gets a `JSONDecodeError` on
empty input and has no structured error to branch on. The `shots` and
`publish` subcommands already model the desired shape
(`{"status": "error", "message": ...}` with a nonzero exit); the rest of the
dispatch has no such safety net.

**D-011 (PAPERCUT) — `--artifact-root` path shape is undocumented/misleading.**
The docs imply `--artifact-root out` yields `out/runs/NNNN/`. Actual layout is
`out/0001/` (the root *is* the runs directory; run dirs are the zero-padded
number directly under it). The run JSON returns the true `artifact_dir`, which
is authoritative, but the README never says so.

**D-012 (PAPERCUT) — the implicit `inspect` inside `run` is undocumented.**
The README quick-start reads `run → inspect → evaluate`, implying `inspect` is
a required step. In fact `run` already writes the spatial facts, and
`evaluate` reads them, so `inspect` is optional (it re-derives/writes
`spatial.json` for direct viewing and `compare`).

**D-015 (PAPERCUT) — the export set varies silently with part count/shape.**
A combined `assembly.step`/`.stl`/`.glb` appears only when a run has ≥2 real
parts; a lone constant-thickness box auto-flattens to a DXF (cadx ADR 0013)
even when the author did not think of it as a sheet part. Documented in ADR
0013 but surprising when predicting a run's artifact set.

## Decision

**1. D-010 — add a top-level exception handler in `main()`.** Wrap the whole
subcommand dispatch in one `try/except`. On any uncaught exception:

- print `{"status": "error", "message": str(exc)}` to **stdout** (compact,
  sorted — the same `_print` every other subcommand uses, so the contract
  "one JSON object on stdout" holds even on failure); and
- print the full traceback to **stderr** (so interactive debugging keeps the
  stack); and
- return exit code **1** (nonzero, matching the prior failure exit).

The handler sits *inside* `main()` but *after* `parse_args()`, so argparse's
own usage errors (unknown command, missing argument → exit 2) are untouched —
those are a different, already-structured contract. The `error` shape matches
the existing `shots`/`publish` convention exactly, so a caller has one error
schema across every subcommand. The dispatch body is extracted into a
`_dispatch(args)` helper purely to keep the `try` block readable; behavior of
each branch is unchanged, so subcommands that already emit their own
`{"status": "error", ...}` and choose their own exit code keep doing so.

**2. D-011/D-012/D-015 — correct the README.** State the real
`--artifact-root` layout (`<root>/NNNN/`, `artifact_dir` in the run JSON is
authoritative); state that `run` already writes `spatial.json` so `inspect` is
optional before `evaluate`; and document that the export set varies with part
count/shape (combined `assembly.*` only with ≥2 real parts; a lone
constant-thickness box auto-flattens to DXF per ADR 0013). No behavior change
— the code is already correct; only the docs were wrong.

Rejected alternatives:
- *Catching `FileNotFoundError` at each call site* — repetitive and
  fragile; a new subcommand or a new reader path could reintroduce the raw
  traceback. One top-level handler covers every present and future branch.
- *Exit code 2 for the generic error* — reserved for argparse usage errors
  and the subcommands that already use 2 for their own validation; a generic
  runtime failure keeps the historical exit 1.
- *Changing the `--artifact-root` layout to actually be `runs/NNNN`* — that
  is a behavior change to a stable contract to make the old docs true;
  cheaper and safer to document the real, already-shipped layout.

## Success criteria

- New subprocess tests in `tests/test_cli_contract.py`: `cadx inspect`
  and `cadx evaluate` against a missing path each produce stdout that
  `json.loads` parses to `{"status": "error", "message": <nonempty str>}`,
  a nonzero exit code, and a nonempty stderr (traceback preserved). Red
  before the `cli.py` change, green after.
- Full suite green.
- README states the true artifact layout, the optional `inspect`, and the
  variable export set.

## Failure criteria

- If the broad `except` ever masks a bug by turning a should-crash
  programming error into a tidy JSON message during development, narrow it or
  add a debug escape hatch (e.g. re-raise under an env flag). The stderr
  traceback mitigates this: the stack is always still printed.

## After Action Report

*2026-07-21:* D-010 landed red→green. The two new subprocess tests in
`tests/test_cli_contract.py` (`cadx inspect` / `cadx evaluate` on a missing
path) first failed with `JSONDecodeError` on empty stdout; after wrapping the
dispatch in `main`'s top-level `try/except`, both parse
`{"status": "error", "message": <errno text>}` from stdout, exit 1, and keep
the traceback on stderr. Manual check confirms the same: stdout is one clean
JSON object, stderr carries the 14-line traceback, exit is 1. The dispatch was
extracted verbatim into `_dispatch(args)`; no subcommand branch changed, so
`shots`/`publish` keep their own error shapes and exit codes.

D-011/D-012/D-015 were documentation-only: the README now states that
`--artifact-root out` yields `out/NNNN/` (not `out/runs/NNNN/`) with
`artifact_dir` in the run JSON authoritative; that `run` already writes
`spatial.json` so `inspect` is optional before `evaluate`; and that the export
set varies (combined `assembly.*` only with ≥2 real parts, a lone
constant-thickness box auto-flattens to DXF per ADR 0013), with
`diagnostics.json` as the authoritative export index.

Full suite 168 → 170 green (the two CLI-error tests are the only additions).
Open watch item from the failure criteria: the broad `except Exception` could
in principle tidy away a genuine programming bug during development; the
always-printed stderr traceback is the mitigation, and a re-raise-under-env
escape hatch remains available if that ever bites.

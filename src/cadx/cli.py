"""Command line interface for the CAD agent harness.

The CLI is the stable contract for coding agents. Each subcommand prints a
single JSON object so callers do not need to scrape human-oriented logs.
Detailed records are written to the run directory for later inspection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cadx.compare import compare_runs
from cadx.evaluate import evaluate_run
from cadx.files import init_project
from cadx.inspector import inspect_run
from cadx.renderer import render_run
from cadx.runner import run_design


def _print(payload: dict[str, Any]) -> None:
    """Emit compact JSON for machine consumers."""

    print(json.dumps(payload, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with one subcommand per harness action."""

    parser = argparse.ArgumentParser(prog="cadx")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init", help="Create starter design and requirement files")

    run_parser = subcommands.add_parser("run", help="Execute a build123d design")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("--params", type=Path, default=Path("params.yaml"))
    run_parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/runs"))

    inspect_parser = subcommands.add_parser("inspect", help="Write spatial.json for a run")
    inspect_parser.add_argument("run_dir", type=Path)

    render_parser = subcommands.add_parser("render", help="Write visual contact sheet artifacts")
    render_parser.add_argument("run_dir", type=Path)

    evaluate_parser = subcommands.add_parser("evaluate", help="Evaluate a run against requirements")
    evaluate_parser.add_argument("run_dir", type=Path)
    evaluate_parser.add_argument("--requirements", type=Path, default=Path("requirements.yaml"))

    compare_parser = subcommands.add_parser("compare", help="Compare two inspected run directories")
    compare_parser.add_argument("left_run_dir", type=Path)
    compare_parser.add_argument("right_run_dir", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested command and return a process exit code."""

    args = build_parser().parse_args(argv)

    if args.command == "init":
        _print(init_project(Path.cwd()))
        return 0
    if args.command == "run":
        payload = run_design(args.source, args.params, args.artifact_root)
        _print(payload)
        return 0 if payload["status"] == "ok" else 1
    if args.command == "inspect":
        _print(inspect_run(args.run_dir))
        return 0
    if args.command == "render":
        _print(render_run(args.run_dir))
        return 0
    if args.command == "evaluate":
        payload = evaluate_run(args.run_dir, args.requirements)
        _print(payload)
        return 0
    if args.command == "compare":
        _print(compare_runs(args.left_run_dir, args.right_run_dir))
        return 0

    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

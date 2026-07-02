"""Bounded run/render/evaluate orchestration.

This module intentionally composes the public command implementations instead
of duplicating CAD logic. It is a thin control loop around existing artifacts,
which keeps it useful for any external coding agent command.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from cadx.evaluate import evaluate_run
from cadx.files import write_json
from cadx.renderer import render_run
from cadx.runner import run_design


def _run_agent_command(
    agent_command: str,
    iteration: int,
    run_dir: Path,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Invoke the trusted external agent command after a failed evaluation."""

    env = {
        **os.environ,
        "CADX_ITERATION": str(iteration),
        "CADX_LAST_RUN_DIR": str(run_dir),
        "CADX_EVALUATION_STATUS": evaluation["status"],
        "CADX_REPORT_PATH": evaluation.get("report_path", ""),
        "CADX_CHECKS_PATH": evaluation.get("checks_path", ""),
    }
    completed = subprocess.run(
        agent_command,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
        cwd=Path.cwd(),
        env=env,
    )
    return {
        "command": agent_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _write_loop_record(loop_path: Path, record: dict[str, Any]) -> None:
    """Persist loop state after every iteration for debuggability."""

    write_json(loop_path, record)


def loop_until_done(
    source: Path,
    params: Path,
    requirements: Path,
    artifact_root: Path,
    loop_path: Path,
    agent_command: str | None,
    max_iterations: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run the CAD loop until checks pass or no more iterations remain."""

    iterations: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "running",
        "source": str(source),
        "params": str(params),
        "requirements": str(requirements),
        "max_iterations": max_iterations,
        "iterations": iterations,
    }

    final_report_path = ""
    for iteration in range(1, max_iterations + 1):
        run_payload = run_design(source, params, artifact_root, timeout_seconds)
        run_dir = Path(run_payload["artifact_dir"])
        iteration_record: dict[str, Any] = {
            "iteration": iteration,
            "run": run_payload,
        }

        if run_payload["status"] != "ok":
            iteration_record["render"] = None
            iteration_record["evaluation"] = {
                "status": run_payload["status"],
                "failed": [],
                "report_path": "",
            }
            iterations.append(iteration_record)
            record["status"] = run_payload["status"]
            record["reason"] = "run_failed"
            _write_loop_record(loop_path, record)
            return {
                "status": run_payload["status"],
                "reason": "run_failed",
                "iterations": len(iterations),
                "loop_path": str(loop_path),
                "final_report_path": "",
            }

        render_payload = render_run(run_dir)
        # The evaluate leg shares the caller's timeout budget: a parametric
        # check re-runs the design, so it must not revert to the default.
        evaluation = evaluate_run(run_dir, requirements, timeout_seconds)
        final_report_path = evaluation["report_path"]
        iteration_record["render"] = render_payload
        iteration_record["evaluation"] = evaluation
        iterations.append(iteration_record)

        if evaluation["status"] == "pass":
            record["status"] = "pass"
            record["reason"] = "checks_passed"
            _write_loop_record(loop_path, record)
            return {
                "status": "pass",
                "reason": "checks_passed",
                "iterations": len(iterations),
                "loop_path": str(loop_path),
                "final_report_path": final_report_path,
            }

        if iteration < max_iterations and agent_command:
            agent_result = _run_agent_command(agent_command, iteration, run_dir, evaluation)
            iteration_record["agent_command"] = agent_result
            if agent_result["returncode"] != 0:
                record["status"] = "error"
                record["reason"] = "agent_failed"
                _write_loop_record(loop_path, record)
                return {
                    "status": "error",
                    "reason": "agent_failed",
                    "iterations": len(iterations),
                    "loop_path": str(loop_path),
                    "final_report_path": final_report_path,
                }
        _write_loop_record(loop_path, record)

    record["status"] = "fail"
    record["reason"] = "max_iterations"
    _write_loop_record(loop_path, record)
    return {
        "status": "fail",
        "reason": "max_iterations",
        "iterations": len(iterations),
        "loop_path": str(loop_path),
        "final_report_path": final_report_path,
    }

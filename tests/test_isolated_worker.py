import json
import os
import subprocess
import sys
from pathlib import Path


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through the public CLI contract."""

    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    return subprocess.run(
        [sys.executable, "-m", "cadx.cli", *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def stdout_payload(result: subprocess.CompletedProcess[str]) -> dict:
    """Return the machine-readable payload even when cadx exits nonzero."""

    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def write_empty_params(tmp_path: Path) -> Path:
    """Create the minimal params file used by worker tests."""

    params = tmp_path / "params.yaml"
    params.write_text("{}\n", encoding="utf-8")
    return params


def test_worker_captures_stdout_stderr_and_runtime_errors(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
import sys


def build(params):
    print("stdout from design")
    print("stderr from design", file=sys.stderr)
    raise RuntimeError("intentional worker failure")
""",
        encoding="utf-8",
    )

    result = run_cadx(tmp_path, "run", str(design), "--params", str(write_empty_params(tmp_path)))
    payload = stdout_payload(result)
    diagnostics = json.loads((tmp_path / payload["artifact_dir"] / "diagnostics.json").read_text())

    assert result.returncode == 1
    assert payload["status"] == "error"
    assert diagnostics["status"] == "error"
    assert diagnostics["errors"][0]["type"] == "RuntimeError"
    assert diagnostics["errors"][0]["message"] == "intentional worker failure"
    assert "stdout from design" in diagnostics["captured_stdout"]
    assert "stderr from design" in diagnostics["captured_stderr"]


def test_worker_times_out_hanging_design(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
import time


def build(params):
    while True:
        print("still running", flush=True)
        time.sleep(0.2)
""",
        encoding="utf-8",
    )

    result = run_cadx(
        tmp_path,
        "run",
        str(design),
        "--params",
        str(write_empty_params(tmp_path)),
        "--timeout-seconds",
        "1",
    )
    payload = stdout_payload(result)
    diagnostics = json.loads((tmp_path / payload["artifact_dir"] / "diagnostics.json").read_text())

    assert result.returncode == 1
    assert payload["status"] == "timeout"
    assert diagnostics["status"] == "timeout"
    assert diagnostics["errors"][0]["type"] == "TimeoutExpired"
    assert diagnostics["timeout_seconds"] == 1
    assert "still running" in diagnostics["captured_stdout"]

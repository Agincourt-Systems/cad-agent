import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the public CLI entrypoint."""

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


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse cadx stdout regardless of process status."""

    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def write_loop_project(tmp_path: Path, width: int = 10) -> tuple[Path, Path, Path]:
    """Create a parameterized real build123d project for loop tests."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(params["width_mm"], 20, 5)

    publish("box", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    params = tmp_path / "params.yaml"
    params.write_text(f"width_mm: {width}\n", encoding="utf-8")
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: width
    type: dimension
    target: obj.box.bbox.size.x
    equals: 12
    tolerance: 0.05
""",
        encoding="utf-8",
    )
    return design, params, requirements


def test_loop_invokes_agent_command_until_requirements_pass(tmp_path):
    design, params, requirements = write_loop_project(tmp_path, width=10)
    fixer = tmp_path / "fixer.py"
    fixer.write_text(
        """
import os
from pathlib import Path

assert Path(os.environ["CADX_REPORT_PATH"]).is_file()
assert os.environ["CADX_EVALUATION_STATUS"] == "fail"
Path("params.yaml").write_text("width_mm: 12\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )

    result = run_cadx(
        tmp_path,
        "loop",
        str(design),
        "--params",
        str(params),
        "--requirements",
        str(requirements),
        "--agent-command",
        f"{sys.executable} {fixer}",
        "--max-iterations",
        "3",
    )
    data = payload(result)
    loop_record = json.loads((tmp_path / data["loop_path"]).read_text())

    assert result.returncode == 0
    assert data["status"] == "pass"
    assert data["iterations"] == 2
    assert [iteration["evaluation"]["status"] for iteration in loop_record["iterations"]] == ["fail", "pass"]
    assert loop_record["iterations"][0]["agent_command"]["returncode"] == 0
    assert Path(tmp_path / data["final_report_path"]).is_file()


def test_loop_stops_at_max_iterations_with_structured_failure(tmp_path):
    design, params, requirements = write_loop_project(tmp_path, width=10)

    result = run_cadx(
        tmp_path,
        "loop",
        str(design),
        "--params",
        str(params),
        "--requirements",
        str(requirements),
        "--max-iterations",
        "1",
    )
    data = payload(result)

    assert result.returncode == 1
    assert data["status"] == "fail"
    assert data["reason"] == "max_iterations"
    assert data["iterations"] == 1
    assert Path(tmp_path / data["final_report_path"]).is_file()

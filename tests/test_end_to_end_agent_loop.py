import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx as an external coding agent would invoke it."""

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


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    """Decode cadx's single machine-readable stdout object."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def write_design(tmp_path: Path) -> Path:
    """Create a small real CAD model that an agent can fix by changing params."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    width = params["width_mm"]
    with BuildPart() as model:
        Box(width, 20, 5)

    publish("box", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    return design


def write_params(tmp_path: Path, width: int) -> Path:
    """Write the parameter value that changes between failed and passing runs."""

    params = tmp_path / "params.yaml"
    params.write_text(f"width_mm: {width}\n", encoding="utf-8")
    return params


def write_requirements(tmp_path: Path) -> Path:
    """Write requirements that intentionally fail for the first run."""

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: box_width
    type: dimension
    target: obj.box.bbox.size.x
    equals: 12
    tolerance: 0.05
  - id: box_depth
    type: dimension
    target: obj.box.bbox.size.y
    equals: 20
    tolerance: 0.05
  - id: box_height
    type: dimension
    target: obj.box.bbox.size.z
    equals: 5
    tolerance: 0.05
""",
        encoding="utf-8",
    )
    return requirements


def test_real_agent_loop_fails_reports_fixes_passes_and_compares(tmp_path):
    design = write_design(tmp_path)
    requirements = write_requirements(tmp_path)

    first_params = write_params(tmp_path, 10)
    first = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", str(first_params)))
    first_run_dir = tmp_path / first["artifact_dir"]

    first_render = parse_stdout_json(run_cadx(tmp_path, "render", str(first_run_dir)))
    assert Path(tmp_path / first_render["manifest"]).is_file()

    first_eval = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(first_run_dir), "--requirements", str(requirements))
    )
    assert first_eval["status"] == "fail"
    assert first_eval["failed"] == ["box_width"]

    first_report = tmp_path / first_eval["report_path"]
    report_text = first_report.read_text(encoding="utf-8")
    assert "box_width" in report_text
    assert "observed: 10.0" in report_text
    assert "expected: 12" in report_text
    assert "spatial.json" in report_text
    assert "checks.json" in report_text
    assert "views/contact.png" in report_text
    assert "views/render_manifest.json" in report_text

    second_params = write_params(tmp_path, 12)
    second = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", str(second_params)))
    second_run_dir = tmp_path / second["artifact_dir"]

    second_render = parse_stdout_json(run_cadx(tmp_path, "render", str(second_run_dir)))
    assert Path(tmp_path / second_render["manifest"]).is_file()

    second_eval = parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(second_run_dir), "--requirements", str(requirements))
    )
    assert second_eval["status"] == "pass"
    assert "Status: PASS" in (tmp_path / second_eval["report_path"]).read_text(encoding="utf-8")

    comparison = parse_stdout_json(run_cadx(tmp_path, "compare", str(first_run_dir), str(second_run_dir)))
    assert comparison["status"] == "ok"
    assert comparison["changes"]["objects"]["obj.box"]["bbox.size"] == pytest.approx([2, 0, 0])

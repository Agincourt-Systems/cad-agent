import json
import os
import subprocess
import sys
from pathlib import Path


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx through its public CLI."""

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
    """Parse successful cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def write_two_block_design(tmp_path: Path) -> Path:
    """Create synthetic spatial publications for evaluator-focused tests."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish


def build(params):
    publish(
        "left",
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="final",
    )
    publish(
        "right",
        {
            "bbox": {"min": [15, 0, 0], "max": [25, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        },
        role="fixture",
    )
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def run_two_block_model(tmp_path: Path) -> Path:
    """Run the synthetic model and return its run directory."""

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(write_two_block_design(tmp_path))))
    return tmp_path / payload["artifact_dir"]


def test_dimension_range_topology_and_clearance_checks_pass(tmp_path):
    run_dir = run_two_block_model(tmp_path)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: left_width_range
    type: dimension
    target: obj.left.bbox.size.x
    min: 9.5
    max: 10.5
  - id: left_faces
    type: topology
    target: obj.left.topology.faces
    equals: 6
  - id: block_clearance
    type: clearance
    between: [obj.left, obj.right]
    min: 5
""",
        encoding="utf-8",
    )

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))

    assert result["status"] == "pass"
    checks = json.loads((run_dir / "checks.json").read_text())
    assert [check["status"] for check in checks["checks"]] == ["pass", "pass", "pass"]
    assert checks["checks"][2]["observed"] == 5


def test_dimension_range_and_clearance_failures_are_reported(tmp_path):
    run_dir = run_two_block_model(tmp_path)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: left_too_wide
    type: dimension
    target: obj.left.bbox.size.x
    max: 8
  - id: clearance_too_small
    type: clearance
    between: [obj.left, obj.right]
    min: 6
""",
        encoding="utf-8",
    )

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    report = (tmp_path / result["report_path"]).read_text(encoding="utf-8")

    assert result["status"] == "fail"
    assert result["failed"] == ["left_too_wide", "clearance_too_small"]
    assert "left_too_wide" in report
    assert "clearance_too_small" in report
    assert "observed: 5" in report
    assert "expected: {'min': 6}" in report

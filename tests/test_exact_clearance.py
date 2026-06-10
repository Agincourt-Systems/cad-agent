import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke cadx the way an agent would."""

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
    """Parse cadx JSON stdout."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def write_cylinder_pair(tmp_path: Path) -> Path:
    """Create two real cylinders whose AABBs overlap but BREPs do not."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as left:
        Cylinder(2, 5)
    with BuildPart() as right:
        Cylinder(2, 5)

    publish("left", left.part, role="final")
    publish("right", right.part.located(Location((3, 3, 0))), role="fixture")
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def run_pair(tmp_path: Path) -> Path:
    """Run the cylinder pair and return its run directory."""

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(write_cylinder_pair(tmp_path)), "--params", "params.yaml"))
    return tmp_path / payload["artifact_dir"]


def test_exact_clearance_uses_brep_distance_instead_of_aabb(tmp_path):
    run_dir = run_pair(tmp_path)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: exact_diagonal_clearance
    type: clearance
    method: exact
    between: [obj.left, obj.right]
    min: 0.2
    max: 0.25
""",
        encoding="utf-8",
    )

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))

    assert result["status"] == "pass"
    assert checks["checks"][0]["method"] == "exact"
    assert checks["checks"][0]["observed"] == pytest.approx(0.242640687, rel=1e-6)


def test_exact_clearance_failure_is_reported(tmp_path):
    run_dir = run_pair(tmp_path)
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: exact_clearance_too_small
    type: clearance
    method: exact
    between: [obj.left, obj.right]
    min: 0.3
""",
        encoding="utf-8",
    )

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    report = (tmp_path / result["report_path"]).read_text(encoding="utf-8")

    assert result["status"] == "fail"
    assert result["failed"] == ["exact_clearance_too_small"]
    assert "exact_clearance_too_small" in report
    assert "observed: 0.242640" in report
    assert "expected: {'min': 0.3}" in report

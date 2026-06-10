import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the same subprocess path agents use."""

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
    """Parse cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_inspect_detects_cylindrical_holes_from_step_export(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(40, 20, 4)
        with Locations((-10, 0, 0), (10, 0, 0)):
            Cylinder(2, 8, mode=Mode.SUBTRACT)

    publish("plate", model.part, role="final")
    return model.part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: detected_hole_count
    type: feature_count
    kind: cylindrical_hole
    equals: 2
  - id: detected_hole_diameter
    type: feature_dimension
    selector:
      kind: cylindrical_hole
    property: diameter
    equals: 4
    tolerance: 0.05
""",
        encoding="utf-8",
    )

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text())
    detected = [feature for feature in spatial["features"] if feature.get("detected")]

    assert len(detected) == 2
    assert {feature["kind"] for feature in detected} == {"cylindrical_hole"}
    assert sorted(feature["diameter"] for feature in detected) == pytest.approx([4.0, 4.0])
    assert all(feature["through"] for feature in detected)

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    assert result["status"] == "pass"

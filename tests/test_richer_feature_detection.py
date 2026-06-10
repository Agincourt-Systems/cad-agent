import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the CLI."""

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
    """Parse successful cadx output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_detects_planar_datums_bosses_and_obround_slots(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import *
from cadx import publish


def build(params):
    with BuildPart() as model:
        Box(50, 25, 4)
        with BuildSketch(Plane.XY):
            SlotOverall(18, 6)
        extrude(amount=10, mode=Mode.SUBTRACT, both=True)
        with Locations((0, 8, 4)):
            Cylinder(3, 4)

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
  - id: boss_count
    type: feature_count
    kind: cylindrical_boss
    equals: 1
  - id: boss_diameter
    type: feature_dimension
    selector:
      kind: cylindrical_boss
    property: diameter
    equals: 6
    tolerance: 0.05
  - id: slot_count
    type: feature_count
    kind: slot
    equals: 1
  - id: slot_width
    type: feature_dimension
    selector:
      kind: slot
    property: width
    equals: 6
    tolerance: 0.05
  - id: slot_length
    type: feature_dimension
    selector:
      kind: slot
    property: length
    equals: 18
    tolerance: 0.05
""",
        encoding="utf-8",
    )

    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text())
    by_kind: dict[str, list[dict]] = {}
    for feature in spatial["features"]:
        by_kind.setdefault(feature["kind"], []).append(feature)

    assert len(by_kind["planar_datum"]) == 9
    assert len(by_kind["cylindrical_boss"]) == 1
    assert len(by_kind["slot"]) == 1
    assert by_kind["cylindrical_boss"][0]["diameter"] == pytest.approx(6)
    assert by_kind["cylindrical_boss"][0]["height"] == pytest.approx(4)
    assert by_kind["slot"][0]["width"] == pytest.approx(6)
    assert by_kind["slot"][0]["length"] == pytest.approx(18)
    assert by_kind["slot"][0]["through"] is True

    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    assert result["status"] == "pass"

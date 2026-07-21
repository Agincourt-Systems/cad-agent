"""Acceptance tests for ADR 0049: part-frame dimension checks.

Covers D-025 of docs/specs/arm-deficiencies.md: a ``dimension`` check reads the
WORLD axis-aligned bbox, so a revolute-posed 60 mm platform measures ~84.85 mm
at 45 deg and cannot be asserted at 60. This ADR records a part-frame
``bbox_local`` and adds an opt-in ``frame: part`` option that measures it.

These fail before the ADR (``bbox_local`` / the ``frame`` option are absent) and
pass after. The world-frame trap test is a regression pin on the status quo.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def _run(tmp_path: Path, body: str) -> dict:
    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    result = run_cadx(tmp_path, "run", str(tmp_path / "design.py"))
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def _evaluate(tmp_path: Path, payload: dict, requirements: str) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    (tmp_path / "req.yaml").write_text(requirements, encoding="utf-8")
    result = run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(tmp_path / "req.yaml"))
    assert result.stdout, result.stderr
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    return {check["id"]: check for check in checks["checks"]}


# A 60x60 platform posed 45 deg about Z by a revolute joint. World AABB grows to
# 60*sqrt(2) ~ 84.85 mm on the planar axes; the part frame stays 60 mm.
POSED_PLATFORM = """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(20, 20, 6), role="final")
    publish("platform", Box(60, 60, 10),
            mate=mate(to="base", kind="revolute",
                      anchor=Location((0, 0, 0)),
                      target=Location((0, 0, 6)),
                      angle=45))
"""


def test_world_frame_dimension_fails_on_posed_platform(tmp_path):
    """The status-quo trap (regression pin): world bbox reads ~84.85 and fails."""

    pytest.importorskip("build123d")
    payload = _run(tmp_path, POSED_PLATFORM)
    assert payload["status"] == "ok", payload
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: width_world
    type: dimension
    target: obj.platform.bbox.size.x
    equals: 60
    tolerance: 1
""",
    )
    result = checks["width_world"]
    assert result["status"] == "fail"
    assert result["observed"] == pytest.approx(84.8528, abs=1e-2)


def test_part_frame_dimension_passes_on_posed_platform(tmp_path):
    """``frame: part`` reads the part-frame 60 mm and passes."""

    pytest.importorskip("build123d")
    payload = _run(tmp_path, POSED_PLATFORM)
    assert payload["status"] == "ok", payload
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: width_part
    type: dimension
    target: obj.platform.bbox.size.x
    frame: part
    equals: 60
    tolerance: 1
""",
    )
    result = checks["width_part"]
    assert result["status"] == "pass", result
    assert result["observed"] == pytest.approx(60.0, abs=1e-4)


def test_part_and_world_frames_agree_at_identity(tmp_path):
    """An unplaced/identity part reads identically in both frames."""

    pytest.importorskip("build123d")
    payload = _run(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    publish("block", Box(10, 20, 30), role="final")
""",
    )
    assert payload["status"] == "ok", payload
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: world
    type: dimension
    target: obj.block.bbox.size.x
    equals: 10
    tolerance: 0.001
  - id: part
    type: dimension
    target: obj.block.bbox.size.x
    frame: part
    equals: 10
    tolerance: 0.001
""",
    )
    assert checks["world"]["status"] == "pass"
    assert checks["part"]["status"] == "pass"
    assert checks["world"]["observed"] == pytest.approx(checks["part"]["observed"], abs=1e-9)


def test_unknown_frame_is_a_clear_error(tmp_path):
    """An unrecognized frame value fails the check loudly, not silently."""

    payload = _run(
        tmp_path,
        """
from cadx import publish


def build(params):
    publish("plate", {
        "bbox": {"min": [0, 0, 0], "max": [10, 20, 2]},
        "mass_properties": {"volume": 400},
        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
    }, role="final")
""",
    )
    assert payload["status"] == "ok", payload
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: bad_frame
    type: dimension
    target: obj.plate.bbox.size.x
    frame: diagonal
    equals: 10
""",
    )
    result = checks["bad_frame"]
    assert result["status"] == "fail"
    assert "frame" in result.get("error", "").lower()
    assert "diagonal" in result.get("error", "")


def test_bbox_local_recorded_for_synthetic(tmp_path):
    """A translated synthetic part records bbox_local as its authored bbox."""

    payload = _run(
        tmp_path,
        """
from cadx import publish, mate


def build(params):
    publish("base", {
        "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
        "mass_properties": {"volume": 1000},
        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
    }, role="final")
    publish("slider", {
        "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
        "mass_properties": {"volume": 1000},
        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
    }, mate=mate(to="base", anchor=[0, 0, 0], target=[100, 0, 0]))
""",
    )
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text())
    slider = {obj["label"]: obj for obj in spatial["objects"]}["slider"]
    # World bbox is translated by +100 in x; the part-frame bbox is not.
    assert slider["bbox"]["min"][0] == pytest.approx(100.0, abs=1e-6)
    assert slider["bbox_local"]["min"][0] == pytest.approx(0.0, abs=1e-6)
    assert slider["bbox_local"]["max"][0] == pytest.approx(10.0, abs=1e-6)
    assert slider["bbox_local"]["size"] == pytest.approx([10, 10, 10], abs=1e-6)

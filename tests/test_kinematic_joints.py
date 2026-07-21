"""ADR 0025: kinematic joint types — posed mates and motion envelopes.

``mate(kind=...)`` gains revolute/prismatic/cylindrical pose variables that
compose as ``parent * target * J(pose) * anchor⁻¹`` about the target frame's
local Z. Poses read from ``params`` make ADR 0020's parametric sweep the
motion-envelope verifier; the flagship test pins that composition. Expected
kernel values were cross-checked in the ADR 0025 probe.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


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


def run_design(tmp_path: Path, body: str, params: str = "{}\n") -> dict:
    """Write and run a design body, returning the CLI payload."""

    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text(params, encoding="utf-8")
    result = run_cadx(tmp_path, "run", str(tmp_path / "design.py"))
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def read_artifacts(tmp_path: Path, payload: dict) -> tuple[dict, dict]:
    """Load (diagnostics, spatial) for a run payload."""

    run_dir = tmp_path / payload["artifact_dir"]
    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    spatial_path = run_dir / "spatial.json"
    spatial = json.loads(spatial_path.read_text(encoding="utf-8")) if spatial_path.exists() else {}
    return diagnostics, spatial


def objects_by_label(spatial: dict) -> dict:
    return {obj["label"]: obj for obj in spatial.get("objects", [])}


SYNTHETIC_BLOCK = """
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        }
"""


def test_prismatic_mate_slides_synthetic_child(tmp_path):
    """Travel slides a synthetic child along the (translation-only) frame Z."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("slider", {SYNTHETIC_BLOCK},
            mate=mate(to="base", kind="prismatic",
                      anchor=[0, 0, 0], target=[10, 0, 5], travel=7))
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    slider = objects_by_label(spatial)["slider"]

    assert slider["placement"]["position"] == [10, 0, 12]
    assert slider["bbox"]["min"] == [10, 0, 12]
    # ADR 0038 (D-013): frames, world slide axis, and travel-free origin exported.
    slider_mate = slider["mate"]
    assert slider_mate["to"] == "base"
    assert slider_mate["kind"] == "prismatic"
    assert slider_mate["travel"] == 7
    assert slider_mate["anchor"] == {"position": [0, 0, 0], "orientation": [0, 0, 0]}
    assert slider_mate["target"] == {"position": [10, 0, 5], "orientation": [0, 0, 0]}
    assert slider_mate["origin"]["position"] == [10, 0, 5]
    assert slider_mate["axis"] == [0, 0, 1]


def test_revolute_mate_on_synthetic_object_warns(tmp_path):
    """Rotating a synthetic bbox is undefined: warn and leave unplaced."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("flap", {SYNTHETIC_BLOCK},
            mate=mate(to="base", kind="revolute",
                      anchor=[0, 0, 0], target=[10, 0, 5], angle=30))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics, spatial = read_artifacts(tmp_path, payload)

    warnings = [w for w in diagnostics["warnings"] if w["type"] == "mate_failed"]
    assert warnings and warnings[0]["label"] == "flap"
    assert "placement" not in objects_by_label(spatial)["flap"]


def test_pose_outside_declared_range_warns(tmp_path):
    """An out-of-range pose is placed as requested but flagged."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("slider", {SYNTHETIC_BLOCK},
            mate=mate(to="base", kind="prismatic", anchor=[0, 0, 0], target=[0, 0, 0],
                      travel=30, travel_range=(0, 20)))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics, spatial = read_artifacts(tmp_path, payload)

    warnings = [w for w in diagnostics["warnings"] if w["type"] == "mate_out_of_range"]
    assert warnings and warnings[0]["label"] == "slider"
    assert "30" in warnings[0]["message"] and "20" in warnings[0]["message"]
    # The geometry stays honest to the requested pose.
    slider = objects_by_label(spatial)["slider"]
    assert slider["placement"]["position"] == [0, 0, 30]
    assert slider["mate"]["travel_range"] == [0, 20]


def test_pose_argument_foreign_to_kind_is_an_authoring_error(tmp_path):
    """angle on a rigid mate fails the run with a message naming both."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("bad", {SYNTHETIC_BLOCK},
            mate=mate(to="base", anchor=[0, 0, 0], target=[0, 0, 0], angle=30))
""",
    )
    assert payload["status"] == "error"
    message = payload["errors"][0]["message"]
    assert "angle" in message and "rigid" in message


def test_revolute_mate_rotates_about_target_axis(tmp_path):
    """The probe geometry, driven from params: 0 deg spans +X, 90 deg spans +Y."""

    pytest.importorskip("build123d")
    body = """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(60, 40, 6), role="final")
    publish("flap", Box(20, 10, 2),
            mate=mate(to="base", kind="revolute",
                      anchor=Location((-10, 0, 0)), target=Location((50, 0, 10)),
                      angle=params.get("flap_angle", 0)))
"""
    closed = run_design(tmp_path, body, params="flap_angle: 0\n")
    assert closed["status"] == "ok", closed
    _, spatial = read_artifacts(tmp_path, closed)
    flap = objects_by_label(spatial)["flap"]
    assert flap["bbox"]["min"][0] == pytest.approx(50.0, abs=1e-6)
    assert flap["bbox"]["max"][0] == pytest.approx(70.0, abs=1e-6)
    assert flap["mate"]["kind"] == "revolute"
    assert flap["mate"]["angle"] == 0

    opened = run_design(tmp_path, body, params="flap_angle: 90\n")
    assert opened["status"] == "ok", opened
    _, spatial = read_artifacts(tmp_path, opened)
    flap = objects_by_label(spatial)["flap"]
    assert flap["placement"]["position"] == pytest.approx([50, 10, 10])
    assert flap["placement"]["orientation"][2] == pytest.approx(90.0)
    assert flap["bbox"]["min"][1] == pytest.approx(0.0, abs=1e-6)
    assert flap["bbox"]["max"][1] == pytest.approx(20.0, abs=1e-6)
    assert flap["mate"]["angle"] == 90


def test_cylindrical_mate_combines_travel_and_angle(tmp_path):
    """Travel and angle apply about the same target-frame axis."""

    pytest.importorskip("build123d")
    payload = run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("frame", Box(40, 40, 6), role="final")
    publish("quill", Box(20, 10, 2),
            mate=mate(to="frame", kind="cylindrical",
                      anchor=Location((0, 0, 0)), target=Location((0, 0, 3)),
                      travel=10, angle=45))
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    quill = objects_by_label(spatial)["quill"]

    assert quill["placement"]["position"] == pytest.approx([0, 0, 13])
    assert quill["placement"]["orientation"][2] == pytest.approx(45.0)
    # A 20x10 rectangle rotated 45 deg spans (20+10)/sqrt(2) both ways.
    expected_span = 30 / (2 ** 0.5)
    assert quill["bbox"]["size"][0] == pytest.approx(expected_span, abs=1e-6)
    assert quill["bbox"]["size"][1] == pytest.approx(expected_span, abs=1e-6)
    # ADR 0038 (D-013): frames, joint axis, and zero-pose origin exported.
    quill_mate = quill["mate"]
    assert quill_mate["to"] == "frame"
    assert quill_mate["kind"] == "cylindrical"
    assert quill_mate["angle"] == 45
    assert quill_mate["travel"] == 10
    assert quill_mate["anchor"]["position"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert quill_mate["target"]["position"] == pytest.approx([0, 0, 3], abs=1e-6)
    assert quill_mate["origin"]["position"] == pytest.approx([0, 0, 3], abs=1e-6)
    assert quill_mate["axis"] == pytest.approx([0, 0, 1], abs=1e-6)


def test_motion_envelope_sweep_catches_interference(tmp_path):
    """A parametric sweep over the pose is the motion-envelope check.

    The arm swings clear at 0 deg and through the wall at 90 deg; the sweep's
    aggregate fails with per-set verdicts naming the colliding pair.
    """

    pytest.importorskip("build123d")
    (tmp_path / "design.py").write_text(
        """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("post", Box(8, 8, 20), role="final", placement=Location((0, 0, 10)))
    publish("wall", Box(8, 10, 20), placement=Location((0, 20, 10)))
    publish("arm", Box(30, 8, 4),
            mate=mate(to="post", kind="revolute",
                      anchor=Location((-20, 0, 0)), target=Location((0, 0, 0)),
                      angle=params.get("arm_angle", 0)))
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("arm_angle: 0\n", encoding="utf-8")
    (tmp_path / "requirements.yaml").write_text(
        """
units: mm
checks:
  - id: swing_envelope
    type: parametric
    params:
      - arm_angle: 0
      - arm_angle: 90
    checks:
      - id: no_collision
        type: interference
        tolerance: 0.001
""",
        encoding="utf-8",
    )

    run_payload = json.loads(run_cadx(tmp_path, "run", str(tmp_path / "design.py")).stdout)
    assert run_payload["status"] == "ok", run_payload
    run_dir = tmp_path / run_payload["artifact_dir"]

    result = run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", "requirements.yaml")
    assert result.returncode == 0, result.stderr
    evaluation = json.loads(result.stdout)
    assert evaluation["status"] == "fail"

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    sweep = checks["checks"][0]
    assert sweep["type"] == "parametric"
    assert [entry["status"] for entry in sweep["sets"]] == ["pass", "fail"]
    collision = sweep["sets"][1]["checks"][0]
    assert ["arm", "wall"] in collision["pairs"] or ["wall", "arm"] in collision["pairs"]

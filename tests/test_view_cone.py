"""Acceptance tests for ADR 0052: the ``view_cone`` FOV containment check.

Covers D-027 of docs/specs/arm-deficiencies.md: cadx had no field-of-view /
view-angle / occlusion check primitive, so a downstream group could only assert
gripper-jaw-tip visibility as hand-rolled pytest over ``spatial.json``
coordinates. This ADR adds a ``view_cone`` check (apex, axis, half-angle,
targets, optional occluders).

The checks are driven through the CLI so the full run -> evaluate contract is
exercised. Targets are published as synthetic dict parts (bounding boxes only),
so no CAD kernel is required and the geometry is exact and deterministic.
Before implementation every check fails because ``view_cone`` is an unknown type.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


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


def _synthetic(label: str, minimum: list[float], maximum: list[float]) -> str:
    """Return a ``publish(...)`` call for a synthetic box part with a bbox."""

    return (
        f"    publish({label!r}, {{\n"
        f"        'bbox': {{'min': {minimum}, 'max': {maximum}}},\n"
        f"        'mass_properties': {{'volume': 1000}},\n"
        f"        'topology': {{'solids': 1, 'faces': 6, 'edges': 12, 'vertices': 8}},\n"
        f"    }}, role='final')\n"
    )


def _design(*parts: str) -> str:
    return "from cadx import publish\n\n\ndef build(params):\n" + "".join(parts)


# A camera near the origin looking up +z. The target box is centred over the
# apex and stays well within a 45 deg cone.
INSIDE = _design(
    _synthetic("cam", [-1, -1, -1], [1, 1, 1]),
    _synthetic("target", [-5, -5, 50], [5, 5, 60]),
)


def test_target_inside_cone_passes(tmp_path):
    payload = _run(tmp_path, INSIDE)
    assert payload["status"] == "ok", payload
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "pass", result
    target = result["targets"][0]
    assert target["status"] == "pass"
    # Worst corner (5, 5, 50) is ~8.06 deg off axis.
    assert target["angle_deg"] < 45


def test_axis_is_normalized(tmp_path):
    """A non-unit axis gives the same verdict as the unit axis."""

    payload = _run(tmp_path, INSIDE)
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 7]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "pass", result
    assert result["axis"] == [0.0, 0.0, 1.0]


def test_target_outside_cone_fails(tmp_path):
    """A box beyond the half-angle fails, naming the offending point and angle."""

    payload = _run(
        tmp_path,
        _design(_synthetic("target", [60, -5, 50], [70, 5, 60])),
    )
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail", result
    target = result["targets"][0]
    assert target["status"] == "fail"
    assert target["reason"] == "angle_exceeds_half_angle"
    assert target["angle_deg"] > 45
    assert "worst_point" in target


def test_target_behind_apex_fails(tmp_path):
    """A box on the -axis side is outside the cone (a camera sees only forward)."""

    payload = _run(
        tmp_path,
        _design(_synthetic("target", [-5, -5, -60], [5, 5, -50])),
    )
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail", result
    assert result["targets"][0]["reason"] == "behind_apex"


def test_apex_reference_resolves(tmp_path):
    """``apex: obj.cam.center`` resolves to the object's bbox centre."""

    payload = _run(tmp_path, INSIDE)
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: obj.cam.center
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "pass", result
    assert result["apex"] == [0.0, 0.0, 0.0]


def test_explicit_point_target(tmp_path):
    """An [x, y, z] target point is tested as a single point."""

    payload = _run(tmp_path, _design(_synthetic("anything", [0, 0, 0], [1, 1, 1])))
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 30
    targets:
      - [0, 0, 100]
      - [100, 0, 100]
""",
    )
    result = checks["fov"]
    # First point is on-axis (pass); second is 45 deg off (fail at 30 deg limit).
    assert result["status"] == "fail", result
    assert result["targets"][0]["status"] == "pass"
    assert result["targets"][1]["status"] == "fail"
    assert result["targets"][1]["angle_deg"] > 30


def test_occluder_blocks_sightline_fails(tmp_path):
    """A plate between apex and an in-cone target blocks the sightline."""

    payload = _run(
        tmp_path,
        _design(
            _synthetic("target", [-5, -5, 50], [5, 5, 60]),
            _synthetic("plate", [-8, -8, 20], [8, 8, 25]),
        ),
    )
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
    occluders:
      - obj.plate
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail", result
    assert result["targets"][0]["reason"] == "occluded"
    assert result["occlusion_method"] == "aabb"


def test_occluder_off_to_the_side_does_not_block(tmp_path):
    """An occluder that misses every sightline leaves the check passing."""

    payload = _run(
        tmp_path,
        _design(
            _synthetic("target", [-5, -5, 50], [5, 5, 60]),
            _synthetic("plate", [40, -3, 20], [46, 3, 25]),
        ),
    )
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.target
    occluders:
      - obj.plate
""",
    )
    result = checks["fov"]
    assert result["status"] == "pass", result
    assert result["targets"][0]["occluded"] is False


def test_missing_axis_is_a_clear_error(tmp_path):
    payload = _run(tmp_path, INSIDE)
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    half_angle_deg: 45
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail"
    assert "axis" in result.get("error", "").lower()


def test_negative_half_angle_is_a_clear_error(tmp_path):
    payload = _run(tmp_path, INSIDE)
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: -10
    targets:
      - obj.target
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail"
    error = result.get("error", "")
    assert "half_angle" in error and "-10" in error


def test_unknown_target_is_a_clear_error(tmp_path):
    payload = _run(tmp_path, INSIDE)
    checks = _evaluate(
        tmp_path,
        payload,
        """
checks:
  - id: fov
    type: view_cone
    apex: [0, 0, 0]
    axis: [0, 0, 1]
    half_angle_deg: 45
    targets:
      - obj.ghost
""",
    )
    result = checks["fov"]
    assert result["status"] == "fail"
    assert "ghost" in result.get("error", "")

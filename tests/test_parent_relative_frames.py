"""Acceptance tests for ADR 0048: parent-relative joint frames.

Covers D-018 of docs/specs/arm-deficiencies.md: ADR 0038 exports a mate's
``origin``/``axis`` in the WORLD frame only, but URDF places ``<joint><origin>``
and ``<joint><axis>`` in the PARENT link frame. This ADR adds, ADDITIVELY,
``origin_in_parent`` (``parent⁻¹ · origin_world``) and ``axis_in_parent``
(``R_parentᵀ · axis_world`` — rotation only) beside the world forms.

These fail before the ADR (the parent-relative keys are absent) and pass after.
The expected numbers were hand-computed with build123d in the ADR 0048 probe
(a parent rotated 90 deg about Z and translated, so world != parent frame).
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


def run_design(tmp_path: Path, body: str, params: str = "{}\n") -> dict:
    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text(params, encoding="utf-8")
    result = run_cadx(tmp_path, "run", str(tmp_path / "design.py"))
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def read_spatial(tmp_path: Path, payload: dict) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    return json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))


def objects_by_label(spatial: dict) -> dict:
    return {obj["label"]: obj for obj in spatial.get("objects", [])}


SYNTHETIC_BLOCK = """
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        }
"""


# A parent placed with BOTH a rotation (90 deg about Z) and a translation, so the
# parent-relative frames differ from the world frames on every axis.
def _elbow_body(parent_translation: str = "(100, 0, 0)") -> str:
    return f"""
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("upper", Box(20, 20, 6), role="final",
            placement=Location({parent_translation}, (0, 0, 90)))
    publish("fore", Box(30, 8, 4),
            mate=mate(to="upper", kind="revolute",
                      anchor=Location((-10, 0, 0)),
                      target=Location((0, 0, 20), (0, 90, 0)),
                      angle=30))
"""


def test_elbow_parent_relative_origin_and_axis(tmp_path):
    """Parent-relative origin and axis are exported and hand-match."""

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, _elbow_body())
    assert payload["status"] == "ok", payload
    mate = objects_by_label(read_spatial(tmp_path, payload))["fore"]["mate"]

    # World axis: the Y-90 target's local Z, rotated by the parent's 90-about-Z
    # -> +Y. Parent-relative axis removes the parent rotation -> +X.
    assert mate["axis"] == pytest.approx([0, 1, 0], abs=1e-6)
    assert mate["axis_in_parent"] == pytest.approx([1, 0, 0], abs=1e-6)

    # Parent-relative zero-pose origin (parent^-1 * world origin), hand-computed.
    assert mate["origin_in_parent"]["position"] == pytest.approx([0, 0, 10], abs=1e-6)
    assert mate["origin_in_parent"]["orientation"] == pytest.approx([0, 90, 0], abs=1e-6)


def test_parent_relative_origin_reconstructs_world(tmp_path):
    """parent_placement * origin_in_parent reproduces the world origin."""

    from build123d import Location

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, _elbow_body())
    assert payload["status"] == "ok", payload
    objects = objects_by_label(read_spatial(tmp_path, payload))
    mate = objects["fore"]["mate"]

    def loc(record):
        return Location(tuple(record["position"]), tuple(record["orientation"]))

    parent = loc(objects["upper"]["placement"])
    reconstructed = parent * loc(mate["origin_in_parent"])
    world_origin = mate["origin"]
    assert list(reconstructed.position) == pytest.approx(world_origin["position"], abs=1e-6)
    assert list(reconstructed.orientation) == pytest.approx(world_origin["orientation"], abs=1e-6)


def test_axis_in_parent_is_rotation_only(tmp_path):
    """The parent's translation must not shift the axis direction.

    Two elbows with the SAME parent rotation but DIFFERENT parent translations
    export the identical ``axis_in_parent`` (an axis is a direction).
    """

    pytest.importorskip("build123d")
    a = run_design(tmp_path, _elbow_body("(100, 0, 0)"))
    b = run_design(tmp_path, _elbow_body("(-40, 250, 17)"))
    axis_a = objects_by_label(read_spatial(tmp_path, a))["fore"]["mate"]["axis_in_parent"]
    axis_b = objects_by_label(read_spatial(tmp_path, b))["fore"]["mate"]["axis_in_parent"]
    assert axis_a == pytest.approx([1, 0, 0], abs=1e-6)
    assert axis_b == pytest.approx(axis_a, abs=1e-6)
    # Unit length.
    assert sum(c * c for c in axis_a) == pytest.approx(1.0, abs=1e-6)


def test_root_parented_mate_parent_equals_world(tmp_path):
    """A mate to a root parent (identity) has parent-relative == world."""

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
    mate = objects_by_label(read_spatial(tmp_path, payload))["slider"]["mate"]
    assert mate["origin_in_parent"] == mate["origin"]
    assert mate["axis_in_parent"] == pytest.approx(mate["axis"], abs=1e-6)


def test_synthetic_parent_relative_frames(tmp_path):
    """Synthetic prismatic mate: origin_in_parent == target - anchor, axis +Z."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("slider", {SYNTHETIC_BLOCK},
            mate=mate(to="base", kind="prismatic",
                      anchor=[1, 2, 3], target=[10, 0, 5], travel=7))
""",
    )
    assert payload["status"] == "ok", payload
    mate = objects_by_label(read_spatial(tmp_path, payload))["slider"]["mate"]
    # Root parent at identity: parent^-1 is identity, so origin_in_parent equals
    # the world origin (parent + target - anchor with parent at origin).
    assert mate["origin_in_parent"]["position"] == pytest.approx([9, -2, 2], abs=1e-6)
    assert mate["axis_in_parent"] == pytest.approx([0, 0, 1], abs=1e-6)

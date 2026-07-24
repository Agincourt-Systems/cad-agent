"""ADR 0054 (D-034): emit the resolved world joint frame per mate.

The mate record already carries axis directions and (parent-relative) origins,
but no world-frame point that lies ON the joint axis. This ADR adds
``joint_world_zero`` (joint value 0) and ``joint_world`` (posed) — each a point
on the axis plus the world axis direction — for kinematic mates only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
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


def objects_by_label(tmp_path: Path, payload: dict) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    return {obj["label"]: obj for obj in spatial.get("objects", [])}


SYNTHETIC_BLOCK = """
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        }
"""


# Translated parent + Y-90 target: the joint axis is world +X, not trivially +Z.
REVOLUTE_BODY = """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(20, 20, 6), role="final", placement=Location((100, 0, 0)))
    publish("arm", Box(30, 8, 4),
            mate=mate(to="base", kind="revolute",
                      anchor=Location((-10, 0, 0)),
                      target=Location((0, 0, 20), (0, 90, 0)),
                      angle=params.get("th", 0)))
"""


# --------------------------------------------------------------------------
# Real geometry (build123d).
# --------------------------------------------------------------------------


def test_revolute_joint_world_frame(tmp_path):
    """The world joint frame is a point ON the axis + the world direction; the
    posed frame equals the zero frame for a revolute; both differ from the child
    origin."""

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, REVOLUTE_BODY, params="th: 30\n")
    assert payload["status"] == "ok", payload
    mate = objects_by_label(tmp_path, payload)["arm"]["mate"]

    # Zero-config joint frame: origin = world target origin (a point on the axis),
    # axis = world local-Z of the Y-90 target = +X.
    assert mate["joint_world_zero"]["origin"] == pytest.approx([100, 0, 20], abs=1e-6)
    assert mate["joint_world_zero"]["axis"] == pytest.approx([1, 0, 0], abs=1e-6)

    # Revolute: rotation about the axis moves neither the origin nor the axis, so
    # the posed frame coincides with the zero frame.
    assert mate["joint_world"]["origin"] == pytest.approx([100, 0, 20], abs=1e-6)
    assert mate["joint_world"]["axis"] == pytest.approx([1, 0, 0], abs=1e-6)

    # The joint point is NOT the child link origin (parent*target*anchor^-1).
    assert mate["origin"]["position"] == pytest.approx([100, 0, 10], abs=1e-6)


def test_zero_joint_frame_matches_parent_target(tmp_path):
    """joint_world_zero reproduces the parent*target composition at angle 0."""

    from build123d import Location

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, REVOLUTE_BODY, params="th: 30\n")
    objects = objects_by_label(tmp_path, payload)
    arm = objects["arm"]
    mate = arm["mate"]

    def loc(record):
        return Location(tuple(record["position"]), tuple(record["orientation"]))

    parent = loc(objects["base"]["placement"])
    target = loc(mate["target"])
    world_target = parent * target
    tip = (world_target * Location((0, 0, 1))).position
    origin = world_target.position
    axis = [float(tip.X - origin.X), float(tip.Y - origin.Y), float(tip.Z - origin.Z)]

    assert mate["joint_world_zero"]["origin"] == pytest.approx(list(origin), abs=1e-6)
    assert mate["joint_world_zero"]["axis"] == pytest.approx(axis, abs=1e-6)


# --------------------------------------------------------------------------
# Kernel-free (synthetic) mates.
# --------------------------------------------------------------------------


def test_posed_point_lies_on_joint_axis(tmp_path):
    """A prismatic posed axis point moves ALONG the axis and stays on the line."""

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
    mate = objects_by_label(tmp_path, payload)["slider"]["mate"]

    zero = mate["joint_world_zero"]
    posed = mate["joint_world"]
    assert zero["origin"] == pytest.approx([10, 0, 5], abs=1e-6)
    assert zero["axis"] == pytest.approx([0, 0, 1], abs=1e-6)
    # Posed origin is the zero origin shifted 7 along the axis; still on the line.
    assert posed["origin"] == pytest.approx([10, 0, 12], abs=1e-6)
    delta = [posed["origin"][i] - zero["origin"][i] for i in range(3)]
    assert delta == pytest.approx([0, 0, 7], abs=1e-6)


def test_rigid_mate_has_no_joint_world(tmp_path):
    """A rigid mate carries no joint axis, hence no joint_world fields."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("bracket", {SYNTHETIC_BLOCK},
            mate=mate(to="base", anchor=[0, 0, 0], target=[5, 5, 5]))
""",
    )
    assert payload["status"] == "ok", payload
    mate = objects_by_label(tmp_path, payload)["bracket"]["mate"]
    assert "joint_world" not in mate
    assert "joint_world_zero" not in mate

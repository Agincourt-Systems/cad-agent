"""ADR 0038: export mate frames, joint axis, zero-pose origin, root identity.

D-013: ``objects[].mate`` must carry the anchor/target ``Location``s that define
the joint, the derived joint ``axis`` (unit vector, world frame), and the
zero-pose ``origin`` (``parent * target * anchor⁻¹``), so a URDF consumer can
recover ``<joint><axis>`` and ``<origin>`` from ``spatial.json`` alone. D-014:
a root/unmated part records an explicit identity placement instead of none.

Expected kernel values were cross-checked with build123d in the ADR 0038 probe
(a translated parent + a Y-90 target frame, so world composition is exercised).
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


# Shared kernel design: a translated parent so the world composition is real,
# and a target rotated 90 deg about Y so the joint axis is not trivially +Z.
# ``th`` (the sampled joint angle) is driven from params so one design body
# serves both the frame-export and reconstruction tests.
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


def test_revolute_mate_exports_frames_axis_and_origin(tmp_path):
    """The mate record carries anchor/target frames, joint axis, zero-pose origin."""

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, REVOLUTE_BODY, params="th: 30\n")
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    mate = objects_by_label(spatial)["arm"]["mate"]

    # Anchor/target are serialized exactly as authored (local frames).
    assert mate["anchor"]["position"] == pytest.approx([-10, 0, 0], abs=1e-6)
    assert mate["anchor"]["orientation"] == pytest.approx([0, 0, 0], abs=1e-6)
    assert mate["target"]["position"] == pytest.approx([0, 0, 20], abs=1e-6)
    assert mate["target"]["orientation"] == pytest.approx([0, 90, 0], abs=1e-6)

    # Joint axis: world local-Z of the Y-90 target (parent is a pure
    # translation, so it does not rotate the axis) -> +X, unit length.
    assert mate["axis"] == pytest.approx([1, 0, 0], abs=1e-6)

    # Zero-pose origin: parent * target * anchor^-1 at angle 0. Distinct from
    # the posed placement (which is at angle 30).
    assert mate["origin"]["position"] == pytest.approx([100, 0, 10], abs=1e-6)
    assert mate["origin"]["orientation"] == pytest.approx([0, 90, 0], abs=1e-6)


def test_exported_frames_reconstruct_posed_placement(tmp_path):
    """A URDF consumer rebuilds the posed placement from the exported frames.

    Reconstructing ``parent * target * J(theta) * anchor^-1`` from ONLY the
    exported anchor/target records and the parent's exported placement must
    reproduce the exported posed placement of the child.
    """

    from build123d import Location

    pytest.importorskip("build123d")
    payload = run_design(tmp_path, REVOLUTE_BODY, params="th: 30\n")
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    objects = objects_by_label(spatial)
    arm = objects["arm"]
    mate = arm["mate"]

    def loc(record):
        return Location(tuple(record["position"]), tuple(record["orientation"]))

    parent = loc(objects["base"]["placement"])
    anchor = loc(mate["anchor"])
    target = loc(mate["target"])
    reconstructed = parent * target * Location((0, 0, 0), (0, 0, 30)) * anchor.inverse()

    posed = arm["placement"]
    assert list(reconstructed.position) == pytest.approx(posed["position"], abs=1e-6)
    assert list(reconstructed.orientation) == pytest.approx(posed["orientation"], abs=1e-6)
    # And the exported posed placement matches the independently probed value.
    assert posed["position"] == pytest.approx([100, 5, 11.339746], abs=1e-5)


def test_prismatic_synthetic_mate_exports_frames(tmp_path):
    """Translation-only frames export with world axis +Z and a travel-free origin."""

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
    mate = objects_by_label(spatial)["slider"]["mate"]

    assert mate["anchor"] == {"position": [0, 0, 0], "orientation": [0, 0, 0]}
    assert mate["target"] == {"position": [10, 0, 5], "orientation": [0, 0, 0]}
    assert mate["axis"] == pytest.approx([0, 0, 1], abs=1e-6)
    # Zero-pose origin excludes the 7 mm of travel: parent + target - anchor.
    assert mate["origin"]["position"] == pytest.approx([10, 0, 5], abs=1e-6)


def test_root_part_gets_identity_placement(tmp_path):
    """An unmated root part records an explicit identity placement (D-014)."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    base = objects_by_label(spatial)["base"]
    assert base["placement"] == {"position": [0, 0, 0], "orientation": [0, 0, 0]}


def test_unresolved_mate_still_has_no_placement(tmp_path):
    """A genuinely unplaced part keeps no placement and no frame export (D-014)."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("orphan", {SYNTHETIC_BLOCK},
            mate=mate(to="ghost", anchor=[0, 0, 0], target=[5, 5, 5]))
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    orphan = objects_by_label(spatial)["orphan"]
    assert "placement" not in orphan
    # The intent record survives, but no resolved frames are exported.
    assert orphan["mate"]["to"] == "ghost"
    assert "anchor" not in orphan["mate"]
    assert "axis" not in orphan["mate"]
    assert "origin" not in orphan["mate"]

"""ADR 0024: joint-driven placement (declarative mates).

``publish(..., mate=mate(to=..., ...))`` must resolve to an ordinary
``placement`` before normalization — anchor/target frames or native build123d
``RigidJoint`` names, chains in any publish order, and graceful warnings for
unknown targets and cycles. Synthetic dict publications exercise the
translation-only path kernel-free; the real-geometry tests pin the Location
math against values cross-checked with build123d's own ``connect_to``.
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


def run_design(tmp_path: Path, body: str) -> dict:
    """Write and run a design body, returning the CLI payload."""

    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
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


def test_synthetic_mate_translates_child_bbox(tmp_path):
    """A translation mate lands a dict-published child at the target frame."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("base", {SYNTHETIC_BLOCK}, role="final")
    publish("child", {SYNTHETIC_BLOCK},
            mate=mate(to="base", anchor=[0, 0, 0], target=[30, 0, 10]))
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    child = objects_by_label(spatial)["child"]

    assert child["bbox"]["min"] == [30, 0, 10]
    assert child["bbox"]["max"] == [40, 10, 20]
    assert child["placement"]["position"] == [30, 0, 10]
    assert child["mate"] == {"to": "base"}


def test_mate_chain_resolves_regardless_of_publish_order(tmp_path):
    """C mates to B mates to A, published C-first, still composes positions."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("c", {SYNTHETIC_BLOCK}, mate=mate(to="b", anchor=[0, 0, 0], target=[0, 0, 10]))
    publish("b", {SYNTHETIC_BLOCK}, mate=mate(to="a", anchor=[0, 0, 0], target=[0, 0, 10]))
    publish("a", {SYNTHETIC_BLOCK}, role="final")
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    objects = objects_by_label(spatial)

    assert objects["b"]["placement"]["position"] == [0, 0, 10]
    assert objects["c"]["placement"]["position"] == [0, 0, 20]


def test_unknown_mate_target_warns(tmp_path):
    """A mate to an unpublished label degrades to a warning, run stays ok."""

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
    diagnostics, spatial = read_artifacts(tmp_path, payload)

    warnings = [w for w in diagnostics["warnings"] if w["type"] == "mate_unresolved"]
    assert warnings and "ghost" in warnings[0]["message"]
    assert "placement" not in objects_by_label(spatial)["orphan"]


def test_mate_cycle_warns(tmp_path):
    """A mate cycle leaves its members unplaced with warnings, run stays ok."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("a", {SYNTHETIC_BLOCK}, mate=mate(to="b", anchor=[0, 0, 0], target=[1, 0, 0]))
    publish("b", {SYNTHETIC_BLOCK}, mate=mate(to="a", anchor=[0, 0, 0], target=[1, 0, 0]))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics, spatial = read_artifacts(tmp_path, payload)

    cycle_warnings = [w for w in diagnostics["warnings"] if w["type"] == "mate_unresolved"]
    assert len(cycle_warnings) == 2
    objects = objects_by_label(spatial)
    assert "placement" not in objects["a"]
    assert "placement" not in objects["b"]


def test_mate_with_placement_is_an_authoring_error(tmp_path):
    """Supplying both placement and mate fails loudly at design time."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish, mate


def build(params):
    publish("both", {SYNTHETIC_BLOCK},
            placement=[1, 2, 3],
            mate=mate(to="base", anchor=[0, 0, 0], target=[0, 0, 0]))
""",
    )
    assert payload["status"] == "error"
    message = payload["errors"][0]["message"]
    assert "placement" in message and "mate" in message


def test_location_mate_places_real_part(tmp_path):
    """Location-frame mates place real geometry, compose with the assembly
    export, and honor mate orientation."""

    pytest.importorskip("build123d")
    payload = run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish, mate


def build(params):
    publish("base", Box(60, 40, 6), role="final")
    # Tower bottom (15 below its centroid) onto the base top face point.
    publish("tower", Box(15, 15, 30),
            mate=mate(to="base", anchor=Location((0, 0, -15)), target=Location((20, 5, 3))))
    # Fin rotated 90 deg about Y at the base top center: extents swap x<->z.
    publish("fin", Box(20, 10, 2),
            mate=mate(to="base", anchor=Location((0, 0, 0)),
                      target=Location((0, 0, 3), (0, 90, 0))))
""",
    )
    assert payload["status"] == "ok", payload
    diagnostics, spatial = read_artifacts(tmp_path, payload)
    objects = objects_by_label(spatial)

    tower = objects["tower"]
    assert tower["placement"]["position"] == pytest.approx([20, 5, 18])
    assert tower["bbox"]["max"][2] == pytest.approx(33.0, abs=1e-6)
    assert tower["mate"] == {"to": "base"}

    fin_size = objects["fin"]["bbox"]["size"]
    assert fin_size == pytest.approx([2, 10, 20], abs=1e-6)

    # Mated parts participate in the ADR 0023 combined assembly export.
    flagged = [e for e in diagnostics["exports"] if e.get("assembly")]
    assert sorted(e["format"] for e in flagged) == ["glb", "step", "stl"]


def test_native_joint_mate_matches_connect_to(tmp_path):
    """RigidJoint-spelled mates land exactly where connect_to would.

    The expected position (20, 5, 18) was cross-checked against
    ``base.joints["socket"].connect_to(child.joints["plug"])`` on build123d
    0.10 in the ADR 0024 kernel probe.
    """

    pytest.importorskip("build123d")
    payload = run_design(
        tmp_path,
        """
from build123d import Box, Location, Part, RigidJoint
from cadx import publish, mate


def build(params):
    base = Part() + Box(60, 40, 6)
    tower = Part() + Box(15, 15, 30)
    RigidJoint("socket", base, Location((20, 5, 3)))
    RigidJoint("plug", tower, Location((0, 0, -15)))

    publish("base", base, role="final")
    publish("tower", tower, mate=mate(to="base", joint="plug", target_joint="socket"))
""",
    )
    assert payload["status"] == "ok", payload
    _, spatial = read_artifacts(tmp_path, payload)
    tower = objects_by_label(spatial)["tower"]

    assert tower["placement"]["position"] == pytest.approx([20, 5, 18])
    assert tower["bbox"]["min"][2] == pytest.approx(3.0, abs=1e-6)
    assert tower["bbox"]["max"][2] == pytest.approx(33.0, abs=1e-6)
    assert tower["mate"] == {"to": "base", "joint": "plug", "target_joint": "socket"}

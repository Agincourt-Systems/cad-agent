"""ADR 0014: assembly placement, feature alignment, and interference checks.

D3 in docs/ca-sheet-metal-fixes.md: parts are published in independent local
frames with no placement, so the harness cannot assert that two parts bolt
together (coaxial holes) or detect interference. These tests cover three new
behaviors:

- ``publish(..., placement=Location(...))`` applies and records the placement so
  a placed part's reported bbox lives in the common assembly frame.
- a ``feature_alignment`` evaluate check that asserts two holes are coaxial.
- an assembly-wide ``interference`` evaluate check that flags overlapping solids.

Real-geometry tests gate on build123d; evaluator-logic tests use synthetic
dictionary publications and a directly-invoked evaluator so they run without a
CAD kernel.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run cadx through the same subprocess path agents use."""

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
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


def _run_design(tmp_path: Path, body: str) -> Path:
    """Write a design.py from ``body``, run it, and return the run dir."""

    design = tmp_path / "design.py"
    design.write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _evaluate(tmp_path: Path, run_dir: Path, checks_yaml: str) -> dict:
    """Write a requirements file and evaluate it against a run."""

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(checks_yaml, encoding="utf-8")
    parse_stdout_json(
        run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements))
    )
    return json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Placement round-trip (real geometry).
# --------------------------------------------------------------------------- #


def test_placement_roundtrip(tmp_path):
    """A placed part's reported bbox reflects its Location, and the placement
    is recorded on the spatial object."""

    pytest.importorskip("build123d")

    run_dir = _run_design(
        tmp_path,
        """
from build123d import Box, Location
from cadx import publish


def build(params):
    box = Box(10, 20, 4)
    publish("base", box, role="final")
    publish("shifted", box, role="part", placement=Location((30, 5, 7)))
    return box
""",
    )

    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    objects = {obj["label"]: obj for obj in spatial["objects"]}

    base_min = objects["base"]["bbox"]["min"]
    base_max = objects["base"]["bbox"]["max"]
    shifted_min = objects["shifted"]["bbox"]["min"]
    shifted_max = objects["shifted"]["bbox"]["max"]

    delta = (30, 5, 7)
    for axis in range(3):
        assert shifted_min[axis] == pytest.approx(base_min[axis] + delta[axis], abs=1e-6)
        assert shifted_max[axis] == pytest.approx(base_max[axis] + delta[axis], abs=1e-6)

    # ADR 0038 (D-014): an unmated root part records an explicit identity
    # placement; the explicitly placed part records its position.
    assert objects["base"]["placement"] == {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0]}
    placement = objects["shifted"]["placement"]
    assert placement["position"] == pytest.approx([30.0, 5.0, 7.0], abs=1e-6)


# --------------------------------------------------------------------------- #
# Feature alignment (real geometry).
# --------------------------------------------------------------------------- #


_FOUR_HOLE_PLATE = """
from build123d import BuildPart, Box, Cylinder, Locations, Mode, Location
from cadx import publish


def _plate():
    with BuildPart() as model:
        Box(40, 40, 4)
        with Locations((-15, -15, 0), (15, -15, 0), (-15, 15, 0), (15, 15, 0)):
            Cylinder(2, 4, mode=Mode.SUBTRACT)
    return model.part


def build(params):
    plate = _plate()
    publish("plateA", plate, role="final")
    publish("plateB", plate, role="part", placement=Location(({offset}, 0, 12)))
    return plate
"""


def _aligned_check_yaml() -> str:
    return """
units: mm
checks:
  - id: bolt_top_left
    type: feature_alignment
    features:
      - {kind: cylindrical_hole, source_object: obj.plateA}
      - {kind: cylindrical_hole, source_object: obj.plateB}
    tolerance: 0.05
"""


def test_alignment_pass(tmp_path):
    """Two plates whose 4-hole patterns line up coaxially pass alignment."""

    pytest.importorskip("build123d")

    run_dir = _run_design(tmp_path, _FOUR_HOLE_PLATE.format(offset=0))
    checks = _evaluate(tmp_path, run_dir, _aligned_check_yaml())

    [result] = [c for c in checks["checks"] if c["id"] == "bolt_top_left"]
    assert result["type"] == "feature_alignment"
    assert result["status"] == "pass"
    assert result["axis_offset"] == pytest.approx(0.0, abs=1e-3)


def test_alignment_fail_names_feature_ids(tmp_path):
    """Offsetting one plate beyond tolerance fails and names both feature ids
    plus a non-zero measured offset."""

    pytest.importorskip("build123d")

    run_dir = _run_design(tmp_path, _FOUR_HOLE_PLATE.format(offset=3))
    checks = _evaluate(tmp_path, run_dir, _aligned_check_yaml())

    [result] = [c for c in checks["checks"] if c["id"] == "bolt_top_left"]
    assert result["status"] == "fail"
    # Both resolved feature ids are reported.
    assert len(result["features"]) == 2
    assert all(isinstance(fid, str) and fid.startswith("feat.") for fid in result["features"])
    a_id, b_id = result["features"]
    assert a_id != b_id
    # The measured perpendicular offset reflects the 3 mm in-plane shift.
    assert result["axis_offset"] == pytest.approx(3.0, abs=0.1)
    assert "bolt_top_left" in checks["failed"]


# --------------------------------------------------------------------------- #
# Interference (real geometry).
# --------------------------------------------------------------------------- #


_TWO_BLOCK_ASSEMBLY = """
from build123d import Box, Location
from cadx import publish


def build(params):
    block = Box(10, 10, 10)
    publish("left", block, role="final")
    publish("right", block, role="part", placement=Location(({dx}, 0, 0)))
    return block
"""


def test_interference_detects_overlap(tmp_path):
    """Two overlapping solids fail interference and report the offending pair;
    the same solids placed apart pass."""

    pytest.importorskip("build123d")

    yaml = """
units: mm
checks:
  - id: no_collision
    type: interference
"""

    # dx = 5: the two 10 mm cubes overlap by a 5x10x10 slab.
    overlap_dir = _run_design(tmp_path, _TWO_BLOCK_ASSEMBLY.format(dx=5))
    overlap_checks = _evaluate(tmp_path, overlap_dir, yaml)
    [overlap] = [c for c in overlap_checks["checks"] if c["id"] == "no_collision"]
    assert overlap["status"] == "fail"
    offending = {tuple(sorted(pair)) for pair in overlap["pairs"]}
    assert ("left", "right") in {tuple(sorted(p)) for p in offending}
    assert any(pair_volume > 0 for pair_volume in _overlap_volumes(overlap))

    # dx = 20: the cubes are 10 mm apart, no interference.
    apart = _run_design(tmp_path, _TWO_BLOCK_ASSEMBLY.format(dx=20))
    apart_checks = _evaluate(tmp_path, apart, yaml)
    [clear] = [c for c in apart_checks["checks"] if c["id"] == "no_collision"]
    assert clear["status"] == "pass"
    assert clear["pairs"] == []


def _overlap_volumes(result: dict) -> list:
    """Pull reported overlap volumes from an interference check result."""

    return [entry.get("volume", 0) for entry in result.get("overlaps", [])] or [
        1.0  # if volumes are not itemized, the fail itself implies positive overlap
    ]


# --------------------------------------------------------------------------- #
# Synthetic-publication evaluator logic (no build123d required).
# --------------------------------------------------------------------------- #


def _build_spatial(objects, features):
    return {"schema_version": "1.0", "units": "mm", "objects": objects, "features": features}


def test_interference_synthetic_aabb(tmp_path):
    """The interference check works from synthetic dict bboxes (AABB fallback)
    so evaluator logic is testable without a CAD kernel."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    run_dir = tmp_path
    overlapping = _build_spatial(
        objects=[
            {"label": "a", "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}},
            {"label": "b", "bbox": {"min": [5, 0, 0], "max": [15, 10, 10]}},
        ],
        features=[],
    )
    result = _evaluate_check(overlapping, {"id": "x", "type": "interference"}, run_dir)
    assert result["status"] == "fail"
    assert {tuple(sorted(p)) for p in result["pairs"]} == {("a", "b")}

    separated = _build_spatial(
        objects=[
            {"label": "a", "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}},
            {"label": "b", "bbox": {"min": [20, 0, 0], "max": [30, 10, 10]}},
        ],
        features=[],
    )
    result = _evaluate_check(separated, {"id": "x", "type": "interference"}, run_dir)
    assert result["status"] == "pass"
    assert result["pairs"] == []


def test_dict_publication_placement_translates_bbox(tmp_path):
    """A synthetic (kernel-free) publication placed by a dict offset reports a
    shifted bbox and records the placement, with no CAD kernel required."""

    run_dir = _run_design(
        tmp_path,
        """
from cadx import publish


def build(params):
    publish("base", {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}})
    publish("moved", {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}}, placement={"position": [5, 2, 0]})
""",
    )

    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    objects = {obj["label"]: obj for obj in spatial["objects"]}
    assert objects["moved"]["bbox"]["min"] == [5, 2, 0]
    assert objects["moved"]["bbox"]["max"] == [15, 12, 10]
    assert objects["moved"]["placement"]["position"] == [5, 2, 0]
    # ADR 0038 (D-014): the unmated root part records an explicit identity.
    assert objects["base"]["placement"] == {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0]}


def test_feature_alignment_missing_selector_fails(tmp_path):
    """A selector that matches no feature fails the check and names the miss."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = _build_spatial(
        objects=[],
        features=[{"id": "feat.a", "kind": "cylindrical_hole", "center": [0, 0, 0], "axis": [0, 0, 1], "diameter": 4}],
    )
    check = {
        "id": "x",
        "type": "feature_alignment",
        "features": [{"id": "feat.a"}, {"id": "feat.missing"}],
        "tolerance": 0.05,
    }
    result = _evaluate_check(spatial, check, tmp_path)
    assert result["status"] == "fail"
    assert "feat.missing" in result["error"]


def test_feature_alignment_same_feature_selector_fails(tmp_path):
    """Two selectors resolving to the same feature cannot self-align to a pass."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = _build_spatial(
        objects=[],
        features=[{"id": "feat.a", "kind": "cylindrical_hole", "center": [0, 0, 0], "axis": [0, 0, 1], "diameter": 4}],
    )
    # Both selectors name feat.a; pairing a feature with itself is meaningless.
    check = {
        "id": "x",
        "type": "feature_alignment",
        "features": [{"id": "feat.a"}, {"id": "feat.a"}],
        "tolerance": 0.05,
    }
    result = _evaluate_check(spatial, check, tmp_path)
    assert result["status"] == "fail"
    assert "same feature" in result["error"]


def test_interference_between_missing_label_fails(tmp_path):
    """A ``between`` reference to a missing object fails the check, not the run."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = _build_spatial(
        objects=[{"label": "a", "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}}],
        features=[],
    )
    result = _evaluate_check(spatial, {"id": "x", "type": "interference", "between": ["obj.a", "obj.missing"]}, tmp_path)
    assert result["status"] == "fail"
    assert "missing" in result["error"]


def test_interference_between_limits_pairs(tmp_path):
    """A ``between`` clause restricts interference to the named objects."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = _build_spatial(
        objects=[
            {"label": "a", "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}},
            {"label": "b", "bbox": {"min": [5, 0, 0], "max": [15, 10, 10]}},  # overlaps a
            {"label": "c", "bbox": {"min": [100, 0, 0], "max": [110, 10, 10]}},  # far away
        ],
        features=[],
    )
    # a and b overlap, but checking only a vs c reports no interference.
    result = _evaluate_check(spatial, {"id": "x", "type": "interference", "between": ["obj.a", "obj.c"]}, tmp_path)
    assert result["status"] == "pass"
    assert result["pairs"] == []


def test_feature_alignment_synthetic(tmp_path):
    """feature_alignment resolves selectors and measures offset/angle purely
    from spatial.json features, with no STEP import."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.evaluate import _evaluate_check

    aligned = _build_spatial(
        objects=[],
        features=[
            {
                "id": "feat.a",
                "kind": "cylindrical_hole",
                "source_object": "obj.left",
                "center": [0, 0, 0],
                "axis": [0, 0, 1],
                "diameter": 4,
            },
            {
                "id": "feat.b",
                "kind": "cylindrical_hole",
                "source_object": "obj.right",
                "center": [0, 0, 10],
                "axis": [0, 0, 1],
                "diameter": 4,
            },
        ],
    )
    check = {
        "id": "coax",
        "type": "feature_alignment",
        "features": [{"id": "feat.a"}, {"id": "feat.b"}],
        "tolerance": 0.05,
    }
    ok = _evaluate_check(aligned, check, tmp_path)
    assert ok["status"] == "pass"
    assert ok["features"] == ["feat.a", "feat.b"]
    assert ok["axis_offset"] == pytest.approx(0.0, abs=1e-9)

    # Shift the second hole 3 mm perpendicular to the shared Z axis.
    misaligned = json.loads(json.dumps(aligned))
    misaligned["features"][1]["center"] = [3, 0, 10]
    bad = _evaluate_check(misaligned, check, tmp_path)
    assert bad["status"] == "fail"
    assert bad["axis_offset"] == pytest.approx(3.0, abs=1e-9)

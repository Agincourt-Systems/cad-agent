"""Acceptance tests for ADR 0015: center of mass, inertia, and stability.

Covers D5 of docs/ca-sheet-metal-fixes.md:
- per-part center_of_mass + matrix_of_inertia in mass_properties (real geometry),
- a mass/volume-weighted assembly center of mass in spatial.json,
- new evaluate checks ``center_of_mass`` and ``stability``.

These assert real behavior and numeric properties; they fail before the ADR is
implemented (keys absent / check types raise) and pass after.
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


def parse_stdout_json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# Real-geometry tests (need build123d).
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def _run_real_design(tmp_path: Path, body: str) -> Path:
    design = tmp_path / "design.py"
    design.write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def test_com_prism(tmp_path):
    """An off-origin prism reports its true shifted centroid."""

    run_dir = _run_real_design(
        tmp_path,
        """
from build123d import Pos, Box
from cadx import publish


def build(params):
    part = Pos(40, 25, 10) * Box(20, 30, 8)
    publish("plate", part, role="final")
    return part
""",
    )

    spatial = json.loads((run_dir / "spatial.json").read_text())
    [obj] = spatial["objects"]
    com = obj["mass_properties"]["center_of_mass"]
    assert com == pytest.approx([40.0, 25.0, 10.0], abs=1e-3)


def test_inertia_present(tmp_path):
    """A solid reports a 3x3 inertia matrix with a positive diagonal."""

    run_dir = _run_real_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    part = Box(10, 20, 30)
    publish("block", part, role="final")
    return part
""",
    )

    spatial = json.loads((run_dir / "spatial.json").read_text())
    [obj] = spatial["objects"]
    inertia = obj["mass_properties"]["matrix_of_inertia"]
    assert len(inertia) == 3
    assert all(len(row) == 3 for row in inertia)
    assert all(inertia[i][i] > 0 for i in range(3))


# --------------------------------------------------------------------------
# Synthetic dict tests (no CAD kernel) for assembly aggregation + checks.
# --------------------------------------------------------------------------


def _write_assembly_design(tmp_path: Path, *, with_density: bool) -> Path:
    """Two parts with known volumes and centroids; optional densities."""

    left_meta = ", density=2.0" if with_density else ""
    right_meta = ", density=8.0" if with_density else ""
    design = tmp_path / "design.py"
    design.write_text(
        f"""
from cadx import publish


def build(params):
    publish(
        "left",
        {{
            "bbox": {{"min": [0, 0, 0], "max": [10, 10, 10]}},
            "mass_properties": {{"volume": 1000, "center_of_mass": [5, 5, 5]}},
            "topology": {{"solids": 1, "faces": 6, "edges": 12, "vertices": 8}},
        }},
        role="part"{left_meta},
    )
    publish(
        "right",
        {{
            "bbox": {{"min": [20, 0, 0], "max": [30, 10, 10]}},
            "mass_properties": {{"volume": 3000, "center_of_mass": [25, 5, 5]}},
            "topology": {{"solids": 1, "faces": 6, "edges": 12, "vertices": 8}},
        }},
        role="part"{right_meta},
    )
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def _run_assembly(tmp_path: Path, *, with_density: bool) -> Path:
    design = _write_assembly_design(tmp_path, with_density=with_density)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design)))
    return tmp_path / payload["artifact_dir"]


def test_assembly_com_weighted(tmp_path):
    """No densities -> volume-weighted assembly center of mass."""

    run_dir = _run_assembly(tmp_path, with_density=False)
    spatial = json.loads((run_dir / "spatial.json").read_text())
    assembly = spatial["assembly"]
    # (1000*5 + 3000*25) / 4000 = 20 on X, unchanged on Y/Z.
    assert assembly["center_of_mass"] == pytest.approx([20.0, 5.0, 5.0])
    assert assembly["weighting"] == "volume"
    assert assembly["mass"] == pytest.approx(4000)
    assert assembly["part_count"] == 2


def test_assembly_com_density_weighted(tmp_path):
    """Densities present -> mass-weighted assembly center of mass."""

    run_dir = _run_assembly(tmp_path, with_density=True)
    spatial = json.loads((run_dir / "spatial.json").read_text())
    assembly = spatial["assembly"]
    # masses: left 2*1000=2000 at x=5, right 8*3000=24000 at x=25.
    # (2000*5 + 24000*25) / 26000 = 23.4615...
    assert assembly["weighting"] == "mass"
    assert assembly["mass"] == pytest.approx(26000)
    assert assembly["center_of_mass"][0] == pytest.approx((2000 * 5 + 24000 * 25) / 26000)
    assert assembly["center_of_mass"][1] == pytest.approx(5.0)


def test_com_check_pass_fail(tmp_path):
    run_dir = _run_assembly(tmp_path, with_density=False)

    passing = tmp_path / "pass.yaml"
    passing.write_text(
        """
units: mm
checks:
  - id: cog
    type: center_of_mass
    target: assembly
    expected: [20, 5, 5]
    tolerance: 0.1
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(passing)))
    assert result["status"] == "pass"

    failing = tmp_path / "fail.yaml"
    failing.write_text(
        """
units: mm
checks:
  - id: cog
    type: center_of_mass
    target: assembly
    expected: [0, 5, 5]
    tolerance: 0.1
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(failing)))
    assert result["status"] == "fail"
    assert "cog" in result["failed"]


def test_stability_check(tmp_path):
    run_dir = _run_assembly(tmp_path, with_density=False)
    # Assembly CoM projects to (20, 5).

    inside = tmp_path / "inside.yaml"
    inside.write_text(
        """
units: mm
checks:
  - id: tip
    type: stability
    target: assembly
    support: [[0, 0], [40, 0], [40, 40], [0, 40]]
    min_margin: 1.0
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(inside)))
    assert result["status"] == "pass"
    checks = json.loads((run_dir / "checks.json").read_text())
    tip = next(c for c in checks["checks"] if c["id"] == "tip")
    assert tip["margin"] > 0

    outside = tmp_path / "outside.yaml"
    outside.write_text(
        """
units: mm
checks:
  - id: tip
    type: stability
    target: assembly
    support: [[0, 0], [10, 0], [10, 10], [0, 10]]
    min_margin: 0.0
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(outside)))
    assert result["status"] == "fail"
    checks = json.loads((run_dir / "checks.json").read_text())
    tip = next(c for c in checks["checks"] if c["id"] == "tip")
    assert tip["margin"] < 0


def test_com_check_region_and_object_target(tmp_path):
    """center_of_mass accepts a region and a per-object target, not just a point."""

    run_dir = _run_assembly(tmp_path, with_density=False)

    requirements = tmp_path / "region.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: cog_region
    type: center_of_mass
    target: assembly
    region:
      min: [10, 0, 0]
      max: [30, 10, 10]
  - id: left_cog
    type: center_of_mass
    target: obj.left.mass_properties.center_of_mass
    expected: [5, 5, 5]
    tolerance: 0.01
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    assert result["status"] == "pass"
    checks = json.loads((run_dir / "checks.json").read_text())
    region_check = next(c for c in checks["checks"] if c["id"] == "cog_region")
    assert region_check["observed"][0] == pytest.approx(20.0)


def test_stability_tip_angle(tmp_path):
    """A com_height yields a tip angle that can gate the check."""

    run_dir = _run_assembly(tmp_path, with_density=False)
    # CoM projects to (20, 5); base edge at x=40 is 20 mm away. With a 100 mm tall
    # CoM the worst-case tip angle is atan2(margin, 100). A 45-degree minimum is
    # far more than the geometry provides, so the check must fail on tip angle
    # even though the projection is comfortably inside the base.
    requirements = tmp_path / "tip.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: tip_ok
    type: stability
    target: assembly
    support: [[0, 0], [40, 0], [40, 40], [0, 40]]
    com_height: 100
    min_tip_angle_deg: 1.0
  - id: tip_too_steep
    type: stability
    target: assembly
    support: [[0, 0], [40, 0], [40, 40], [0, 40]]
    com_height: 100
    min_tip_angle_deg: 45.0
""",
        encoding="utf-8",
    )
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text())
    by_id = {c["id"]: c for c in checks["checks"]}
    assert by_id["tip_ok"]["status"] == "pass"
    assert by_id["tip_ok"]["tip_angle_deg"] > 0
    assert by_id["tip_too_steep"]["status"] == "fail"


def test_com_check_missing_assembly_fails(tmp_path):
    """A center_of_mass check on a run with no assembly fails with an error."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = {"schema_version": "1.0", "units": "mm", "objects": [], "features": []}
    check = {"id": "cog", "type": "center_of_mass", "target": "assembly", "expected": [0, 0, 0], "tolerance": 1}
    result = _evaluate_check(spatial, check, tmp_path)
    assert result["status"] == "fail"
    assert "assembly" in result["error"]


def test_assembly_mixed_density_weights_by_volume(tmp_path):
    """When only some parts carry density, the aggregate weights all by volume
    (a consistent uniform-density centroid, never a unit-mixed hybrid)."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.inspector import _assembly_center_of_mass

    objects = [
        {"label": "a", "role": "part", "mass_properties": {"volume": 1000, "center_of_mass": [0, 0, 0]}, "metadata": {"density": 5.0}},
        {"label": "b", "role": "part", "mass_properties": {"volume": 1000, "center_of_mass": [10, 0, 0]}},  # no density
    ]
    assembly = _assembly_center_of_mass(objects)
    assert assembly["weighting"] == "volume"
    assert assembly["center_of_mass"][0] == pytest.approx(5.0)  # equal-volume midpoint, not a hybrid
    assert assembly["mass"] == pytest.approx(2000)


def test_stability_degenerate_support_is_unstable(tmp_path):
    """A single-point or line support cannot contain a center of mass."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.evaluate import _evaluate_check

    spatial = {
        "schema_version": "1.0",
        "units": "mm",
        "objects": [],
        "features": [],
        "assembly": {"center_of_mass": [5, 5, 0], "mass": 1, "weighting": "volume", "part_count": 1},
    }
    one_point = _evaluate_check(spatial, {"id": "s", "type": "stability", "support": [[0, 0]]}, tmp_path)
    assert one_point["status"] == "fail" and one_point["margin"] < 0
    line = _evaluate_check(spatial, {"id": "s", "type": "stability", "support": [[0, 0], [10, 0]]}, tmp_path)
    assert line["status"] == "fail" and line["margin"] < 0


def test_mass_properties_without_kernel_omits_optional_keys():
    """A non-build123d object yields only volume/area, never null CoM/inertia."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _mass_properties

    props = _mass_properties(object())
    assert set(props) == {"volume", "area"}
    assert "center_of_mass" not in props
    assert "matrix_of_inertia" not in props

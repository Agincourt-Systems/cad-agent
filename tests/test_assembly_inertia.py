"""Acceptance tests for ADR 0036: assembly-level inertia aggregation.

Covers D-006 of docs/specs/arm-deficiencies.md: an aggregate inertia tensor on
`spatial["assembly"]`, composed from per-part unit-density geometric tensors
(mm^5, world axes, about part centroid) by density scaling + parallel-axis
translation to the assembly center of mass.

All synthetic tests use hand-computable geometry with closed-form answers so the
numbers are pinned, not merely shape-checked. They fail before the ADR is
implemented (no `inertia` key on the assembly record).
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


def _box_geometric_inertia(a: float, b: float, c: float) -> list[list[float]]:
    """Unit-density geometric second-moment (mm^5) of an a*b*c box about its
    centroid, matching build123d's `matrix_of_inertia` convention (volume in
    place of mass)."""

    v = a * b * c
    return [
        [v / 12.0 * (b * b + c * c), 0.0, 0.0],
        [0.0, v / 12.0 * (a * a + c * c), 0.0],
        [0.0, 0.0, v / 12.0 * (a * a + b * b)],
    ]


def _two_box_objects(*, density: float | None, centroids):
    """Two identical 10x10x10 boxes at the given centroids, optional density."""

    g = _box_geometric_inertia(10, 10, 10)
    objects = []
    for label, centroid in zip(("a", "b"), centroids):
        obj = {
            "label": label,
            "role": "part",
            "mass_properties": {
                "volume": 1000.0,
                "center_of_mass": list(centroid),
                "matrix_of_inertia": [row[:] for row in g],
            },
        }
        if density is not None:
            obj["metadata"] = {"density": density}
        objects.append(obj)
    return objects


def _import_agg():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.inspector import _assembly_center_of_mass

    return _assembly_center_of_mass


def test_assembly_inertia_two_boxes_mass():
    """Symmetric identical boxes, density-weighted -> closed-form g*mm^2."""

    agg = _import_agg()
    objects = _two_box_objects(density=2.0, centroids=[(-20, 0, 0), (20, 0, 0)])
    assembly = agg(objects)

    assert assembly["weighting"] == "mass"
    assert assembly["center_of_mass"] == pytest.approx([0.0, 0.0, 0.0])
    tensor = assembly["inertia"]["tensor"]

    # G = 1000/12*(100+100) = 16666.6667 mm^5; rho*G = 33333.333 g*mm^2 per box.
    # m = rho*V = 2000 g; d = 20 mm; parallel term = m*d^2 = 800000 g*mm^2.
    rho_g = 2.0 * 1000.0 / 12.0 * 200.0
    m_d2 = 2000.0 * 400.0
    assert tensor[0][0] == pytest.approx(2 * rho_g)  # x offset -> no transfer on xx
    assert tensor[1][1] == pytest.approx(2 * (rho_g + m_d2))
    assert tensor[2][2] == pytest.approx(2 * (rho_g + m_d2))
    # Off-diagonals vanish for an x-axis-symmetric arrangement.
    for i in range(3):
        for j in range(3):
            if i != j:
                assert tensor[i][j] == pytest.approx(0.0, abs=1e-6)
    # Symmetric by construction.
    assert tensor[0][1] == pytest.approx(tensor[1][0])


def test_assembly_inertia_volume_weighted_units():
    """No densities -> geometric aggregate (mm^5) about the volume centroid."""

    agg = _import_agg()
    objects = _two_box_objects(density=None, centroids=[(-20, 0, 0), (20, 0, 0)])
    assembly = agg(objects)

    assert assembly["weighting"] == "volume"
    tensor = assembly["inertia"]["tensor"]
    g = 1000.0 / 12.0 * 200.0  # 16666.667 mm^5
    v_d2 = 1000.0 * 400.0
    assert tensor[0][0] == pytest.approx(2 * g)
    assert tensor[1][1] == pytest.approx(2 * (g + v_d2))
    assert tensor[2][2] == pytest.approx(2 * (g + v_d2))


def test_assembly_inertia_parallel_axis_offset():
    """CoM not at the origin: transfer must use (c_i - com), not c_i."""

    agg = _import_agg()
    # Boxes at x=0 and x=40 -> assembly CoM at x=20 (equal volumes).
    objects = _two_box_objects(density=2.0, centroids=[(0, 0, 0), (40, 0, 0)])
    assembly = agg(objects)
    assert assembly["center_of_mass"][0] == pytest.approx(20.0)

    # Each box is 20 mm from the CoM in x -> identical to the symmetric case.
    rho_g = 2.0 * 1000.0 / 12.0 * 200.0
    m_d2 = 2000.0 * 400.0
    tensor = assembly["inertia"]["tensor"]
    assert tensor[0][0] == pytest.approx(2 * rho_g)
    assert tensor[1][1] == pytest.approx(2 * (rho_g + m_d2))
    assert tensor[2][2] == pytest.approx(2 * (rho_g + m_d2))


def test_assembly_inertia_omitted_without_part_tensors():
    """A part lacking matrix_of_inertia -> no aggregate inertia (CoM still emitted)."""

    agg = _import_agg()
    objects = [
        {"label": "a", "role": "part", "metadata": {"density": 2.0},
         "mass_properties": {"volume": 1000.0, "center_of_mass": [0, 0, 0],
                             "matrix_of_inertia": _box_geometric_inertia(10, 10, 10)}},
        {"label": "b", "role": "part", "metadata": {"density": 2.0},
         "mass_properties": {"volume": 1000.0, "center_of_mass": [20, 0, 0]}},  # no tensor
    ]
    assembly = agg(objects)
    assert "center_of_mass" in assembly
    assert "inertia" not in assembly


# --------------------------------------------------------------------------
# Real geometry (build123d) — guards the world-axes / about-centroid assumption.
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def test_assembly_inertia_real_geometry(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box, Pos
from cadx import publish


def build(params):
    a = Pos(-30, 0, 0) * Box(10, 20, 30)
    b = Pos(30, 0, 0) * Box(10, 20, 30)
    publish("a", a, role="part", density=2.0)
    publish("b", b, role="part", density=2.0)
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]

    spatial = json.loads((run_dir / "spatial.json").read_text())
    assembly = spatial["assembly"]
    assert assembly["weighting"] == "mass"
    tensor = assembly["inertia"]["tensor"]

    # Closed form for two identical 10x20x30 boxes (V=6000), rho=2.0, centroids
    # at x=+/-30 (assembly CoM at origin).
    v = 6000.0
    rho = 2.0
    m = rho * v
    d = 30.0
    ixx = 2 * (rho * v / 12.0 * (20 ** 2 + 30 ** 2))  # x offset: no transfer on xx
    iyy = 2 * (rho * v / 12.0 * (10 ** 2 + 30 ** 2) + m * d ** 2)
    izz = 2 * (rho * v / 12.0 * (10 ** 2 + 20 ** 2) + m * d ** 2)
    assert tensor[0][0] == pytest.approx(ixx, rel=1e-3)
    assert tensor[1][1] == pytest.approx(iyy, rel=1e-3)
    assert tensor[2][2] == pytest.approx(izz, rel=1e-3)
    # Symmetric, near-diagonal for axis-aligned boxes.
    for i in range(3):
        for j in range(3):
            assert tensor[i][j] == pytest.approx(tensor[j][i], rel=1e-6, abs=1e-6)

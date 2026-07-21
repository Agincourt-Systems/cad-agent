"""Acceptance tests for ADR 0037: machine-readable inertia semantics.

Covers D-007 of docs/specs/arm-deficiencies.md: the `matrix_of_inertia` field is
a unit-density geometric second moment (mm^5), about the part centroid, in world
axes — a silent unit trap. This ADR fixes it ADDITIVELY, emitting a semantics
record beside each tensor without renaming or re-valuing the pinned field.

These fail before the ADR (semantics keys absent) and pass after.
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


_PART_SEMANTICS = {
    "units": "mm^5",
    "density": "unit (geometric)",
    "about": "part centroid",
    "axes": "world at placed pose",
}


def test_mass_properties_semantics_paired():
    """The semantics key is emitted iff matrix_of_inertia is; neither for a
    kernel-free object (extends the ADR 0015 no-null guarantee)."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _mass_properties

    props = _mass_properties(object())
    assert "matrix_of_inertia" not in props
    assert "matrix_of_inertia_semantics" not in props


def _import_agg():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.inspector import _assembly_center_of_mass

    return _assembly_center_of_mass


def _two_box_objects(*, density):
    g = [[16666.6667, 0, 0], [0, 16666.6667, 0], [0, 0, 16666.6667]]
    objects = []
    for label, x in (("a", -20), ("b", 20)):
        obj = {
            "label": label,
            "role": "part",
            "mass_properties": {
                "volume": 1000.0,
                "center_of_mass": [x, 0, 0],
                "matrix_of_inertia": [row[:] for row in g],
            },
        }
        if density is not None:
            obj["metadata"] = {"density": density}
        objects.append(obj)
    return objects


def test_assembly_inertia_semantics_mass():
    agg = _import_agg()
    assembly = agg(_two_box_objects(density=2.0))
    inertia = assembly["inertia"]
    assert inertia["units"] == "g*mm^2"
    assert inertia["density"] == "mass-weighted"
    assert inertia["about"] == "assembly center of mass"
    assert inertia["axes"] == "world"


def test_assembly_inertia_semantics_volume():
    agg = _import_agg()
    assembly = agg(_two_box_objects(density=None))
    inertia = assembly["inertia"]
    assert inertia["units"] == "mm^5"
    assert inertia["density"] == "unit (geometric)"


# --------------------------------------------------------------------------
# Real geometry (build123d).
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def test_part_inertia_semantics(tmp_path):
    design = tmp_path / "design.py"
    design.write_text(
        """
from build123d import Box
from cadx import publish


def build(params):
    part = Box(10, 20, 30)
    publish("block", part, role="final")
    return part
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]

    spatial = json.loads((run_dir / "spatial.json").read_text())
    [obj] = spatial["objects"]
    mass_properties = obj["mass_properties"]
    # Base tensor unchanged: still a bare 3x3 list of numbers.
    tensor = mass_properties["matrix_of_inertia"]
    assert len(tensor) == 3 and all(len(row) == 3 for row in tensor)
    assert all(isinstance(v, (int, float)) for row in tensor for v in row)
    # Semantics ride alongside.
    assert mass_properties["matrix_of_inertia_semantics"] == _PART_SEMANTICS

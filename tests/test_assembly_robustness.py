"""ADR 0021: acceptance-driven robustness fixes for real assemblies.

Two issues surfaced by the holistic end-to-end acceptance review of the
sheet-metal track:

1. ``_assembly_center_of_mass`` only aggregated objects with ``role == "part"``,
   but the idiomatic convention publishes the primary/base part with
   ``role == "final"`` (the starter design and most tests do). So a real
   assembly's base part was silently dropped from the aggregate center of mass,
   and a stability/CoG check could pass on a center that omits the heaviest part.

2. A mistyped ``dimension``/``topology`` target raised an uncaught ``KeyError``
   that aborted the entire ``evaluate`` run, instead of degrading to a graceful
   failed check the way the assembly checks do for bad selectors.

These tests fail before the fix and pass after.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
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


def test_assembly_com_includes_final_role_and_excludes_fixtures():
    """The aggregate CoM counts every physical part (part and final roles) and
    excludes non-physical reference geometry (fixtures)."""

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.inspector import _assembly_center_of_mass

    objects = [
        {"label": "base", "role": "final", "mass_properties": {"volume": 1000, "center_of_mass": [0, 0, 0]}},
        {"label": "plate", "role": "part", "mass_properties": {"volume": 1000, "center_of_mass": [10, 0, 0]}},
        {"label": "jig", "role": "fixture", "mass_properties": {"volume": 1000, "center_of_mass": [100, 0, 0]}},
    ]
    assembly = _assembly_center_of_mass(objects)

    # The base (role="final") and the plate (role="part") both contribute; the
    # fixture at x=100 does not, so the centroid is the midpoint of base+plate.
    assert assembly["part_count"] == 2
    assert assembly["center_of_mass"][0] == 5.0
    assert assembly["mass"] == 2000


def test_dimension_bad_target_fails_gracefully(tmp_path):
    """A mistyped dimension target fails one check instead of crashing evaluate."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish


def build(params):
    publish(
        "plate",
        {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10]}, "mass_properties": {"volume": 1000},
         "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8}},
        role="final",
    )
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(
        """
units: mm
checks:
  - id: typo
    type: dimension
    target: obj.plate.solids
    equals: 1
""",
        encoding="utf-8",
    )
    # The mistyped target (missing the .topology segment) must not crash evaluate.
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    assert result["status"] == "fail"

    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    typo = next(c for c in checks["checks"] if c["id"] == "typo")
    assert typo["status"] == "fail"
    assert "error" in typo

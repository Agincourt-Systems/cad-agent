"""ADR 0055 (D-031): declare and enforce the publish(density=) unit contract.

Every emitted mass / mass-inertia label is hardcoded grams / g*mm^2, which is
only correct if density is g/mm^3. This ADR documents that contract, adds an
optional density_unit= (default "g/mm^3", also "kg/mm^3") that normalizes the
stored density to g/mm^3 so labels stay true, records the declared unit, and
raises loudly on an unknown unit. The ADR 0035 material-implied path is
unaffected.
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


def run_design(tmp_path: Path, body: str) -> dict:
    (tmp_path / "design.py").write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    result = run_cadx(tmp_path, "run", str(tmp_path / "design.py"))
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def single_object(tmp_path: Path, payload: dict) -> dict:
    run_dir = tmp_path / payload["artifact_dir"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    [obj] = spatial["objects"]
    return obj


SYNTHETIC = """
        {
            "bbox": {"min": [0, 0, 0], "max": [10, 10, 10]},
            "mass_properties": {"volume": 1000},
            "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
        }
"""


def test_kg_per_mm3_normalizes_to_grams(tmp_path):
    """density_unit="kg/mm^3" scales the stored density to g/mm^3, so the grams
    label is correct and the declared unit is recorded."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish


def build(params):
    publish("bracket", {SYNTHETIC}, role="final",
            density=2.7e-6, density_unit="kg/mm^3")
""",
    )
    assert payload["status"] == "ok", payload
    obj = single_object(tmp_path, payload)
    metadata = obj["metadata"]
    assert metadata["density"] == pytest.approx(2.7e-3, rel=1e-9)
    assert metadata["density_unit_declared"] == "kg/mm^3"
    assert metadata["density_source"] == "explicit"
    assert obj["mass_properties"]["mass"] == pytest.approx(2.7, rel=1e-9)


def test_default_unit_is_byte_identical(tmp_path):
    """The default g/mm^3 path adds no density_unit_declared key and does not scale."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish


def build(params):
    publish("bracket", {SYNTHETIC}, role="final", density=2.7e-3)
""",
    )
    assert payload["status"] == "ok", payload
    obj = single_object(tmp_path, payload)
    metadata = obj["metadata"]
    assert metadata["density"] == pytest.approx(2.7e-3, rel=1e-9)
    assert obj["mass_properties"]["mass"] == pytest.approx(2.7, rel=1e-9)
    assert "density_unit_declared" not in metadata


def test_unknown_density_unit_raises():
    """An unrecognized density_unit raises ValueError at publish time."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    # Import from cadx.registry, not `from cadx import publish`: importing the
    # cadx.publish MODULE (ADR 0029 export plans, done by test_publish.py in the
    # same pytest process) rebinds the package attribute `cadx.publish` from the
    # re-exported function to that module, making the package-level import
    # order-dependent under the full suite.
    from cadx.registry import clear_registry, publish

    clear_registry()
    with pytest.raises(ValueError) as excinfo:
        publish("bad", {"bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}},
                density=1.0, density_unit="kg/m^3")
    message = str(excinfo.value)
    assert "density_unit" in message
    assert "kg/mm^3" in message  # accepted values are named
    # Raises even without a density supplied (guards a bare typo).
    clear_registry()
    with pytest.raises(ValueError):
        publish("bad2", {"bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}}, density_unit="oops")
    clear_registry()


def test_material_path_unaffected(tmp_path):
    """The ADR 0035 material-implied density path is untouched by this ADR."""

    payload = run_design(
        tmp_path,
        f"""
from cadx import publish


def build(params):
    publish("bracket", {SYNTHETIC}, role="final", material="6061-T6")
""",
    )
    assert payload["status"] == "ok", payload
    obj = single_object(tmp_path, payload)
    metadata = obj["metadata"]
    assert metadata["density"] == pytest.approx(0.0027, rel=1e-9)
    assert metadata["density_source"] == "material:6061-T6"
    assert "density_unit_declared" not in metadata

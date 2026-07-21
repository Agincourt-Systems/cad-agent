"""Acceptance tests for ADR 0035: material-implied density and per-part mass.

Covers D-008 of docs/specs/arm-deficiencies.md:
- a built-in material->density table (g/mm^3) with case/punctuation-insensitive
  lookup and an explicit-override contract,
- a declared material implying `metadata.density` and a computed
  `mass_properties.mass` on the spatial object,
- material declared via either `publish(material=...)` or
  `publish_part_meta(material=...)`,
- unknown materials recording an unresolved state rather than guessing.

These assert real numeric behavior and fail before the ADR is implemented
(the density module and the join do not exist yet).
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
# Pure table lookup (no CAD kernel).
# --------------------------------------------------------------------------


def test_resolve_known_materials():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.density import resolve_density

    # Canonical names resolve to the tabulated g/mm^3.
    cases = {
        "6061-T6": 0.00270,
        "5052-H32": 0.00268,
        "5052": 0.00268,
        "304 stainless": 0.00800,
        "1018": 0.00787,
        "mild steel": 0.00787,
        "brass": 0.00850,
        "ABS": 0.00104,
        "PLA": 0.00124,
        "PETG": 0.00127,
        "Ti-6Al-4V": 0.00443,
    }
    for name, expected in cases.items():
        resolved = resolve_density(name)
        assert resolved is not None, name
        key, density = resolved
        assert density == pytest.approx(expected), name

    # Forgiving spellings still resolve to the right alloy.
    assert resolve_density("Aluminum 6061-T6")[1] == pytest.approx(0.00270)
    assert resolve_density("ti6al4v")[1] == pytest.approx(0.00443)
    assert resolve_density("304 SS")[1] == pytest.approx(0.00800)

    # Unknown / non-string / ambiguous-substring inputs do NOT resolve.
    assert resolve_density("unobtainium") is None
    assert resolve_density("plastic") is None  # must not partial-match "pla"
    assert resolve_density(None) is None
    assert resolve_density(123) is None


# --------------------------------------------------------------------------
# Runner join, unit level (no CAD kernel).
# --------------------------------------------------------------------------


def test_explicit_density_overrides_material():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _apply_material_density

    records = [
        {
            "label": "p",
            "role": "part",
            "metadata": {"material": "6061-T6", "density": 0.005},
            "mass_properties": {"volume": 1000.0},
        }
    ]
    _apply_material_density(records, part_meta=[])
    meta = records[0]["metadata"]
    assert meta["density"] == pytest.approx(0.005)  # explicit wins
    assert meta["density_source"] == "explicit"
    assert records[0]["mass_properties"]["mass"] == pytest.approx(5.0)


def test_unknown_material_records_unresolved():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _apply_material_density

    records = [
        {
            "label": "p",
            "role": "part",
            "metadata": {"material": "unobtainium"},
            "mass_properties": {"volume": 1000.0},
        }
    ]
    _apply_material_density(records, part_meta=[])
    meta = records[0]["metadata"]
    assert meta["density_resolved"] is False
    assert "density" not in meta  # no guess
    assert "mass" not in records[0]["mass_properties"]  # no mass without density


def test_part_meta_material_join():
    """Material declared via publish_part_meta reaches the spatial record."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.runner import _apply_material_density

    records = [
        {
            "label": "rail",
            "role": "part",
            "metadata": {},
            "mass_properties": {"volume": 2000.0},
        }
    ]
    part_meta = [{"label": "rail", "material": "6061-T6"}]
    _apply_material_density(records, part_meta=part_meta)
    meta = records[0]["metadata"]
    assert meta["density"] == pytest.approx(0.00270)
    assert meta["density_source"] == "material:6061-T6"
    assert records[0]["mass_properties"]["mass"] == pytest.approx(2000.0 * 0.00270)


# --------------------------------------------------------------------------
# End-to-end through `cadx run` (real geometry).
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")


def _run_real_design(tmp_path: Path, body: str) -> Path:
    design = tmp_path / "design.py"
    design.write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def test_material_implies_density_and_mass(tmp_path):
    run_dir = _run_real_design(
        tmp_path,
        """
from build123d import Box
from cadx import publish


def build(params):
    part = Box(10, 20, 30)  # volume 6000 mm^3
    publish("bracket", part, role="final", material="6061-T6")
    return part
""",
    )
    spatial = json.loads((run_dir / "spatial.json").read_text())
    [obj] = spatial["objects"]
    assert obj["metadata"]["density"] == pytest.approx(0.00270)
    assert obj["metadata"]["density_source"] == "material:6061-T6"
    volume = obj["mass_properties"]["volume"]
    assert obj["mass_properties"]["mass"] == pytest.approx(volume * 0.00270)


def test_part_meta_material_drives_assembly_mass_weighting(tmp_path):
    """A material named only via publish_part_meta implies density, so the
    assembly aggregation reports mass-weighting rather than volume-weighting."""

    run_dir = _run_real_design(
        tmp_path,
        """
from build123d import Box, Pos
from cadx import publish, publish_part_meta


def build(params):
    a = Pos(-30, 0, 0) * Box(10, 10, 10)
    b = Pos(30, 0, 0) * Box(10, 10, 10)
    publish("a", a, role="part")
    publish("b", b, role="part")
    publish_part_meta("a", material="6061-T6")
    publish_part_meta("b", material="6061-T6")
""",
    )
    spatial = json.loads((run_dir / "spatial.json").read_text())
    assembly = spatial["assembly"]
    assert assembly["weighting"] == "mass"
    # Two identical 1000 mm^3 boxes at density 0.00270 -> 5.4 g total.
    assert assembly["mass"] == pytest.approx(2 * 1000.0 * 0.00270)

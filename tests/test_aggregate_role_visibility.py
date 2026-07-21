"""Acceptance tests for ADR 0046: assembly aggregate role visibility + opt-in.

Covers D-026 of docs/specs/arm-deficiencies.md:
- the `assembly` block becomes self-describing (`included_roles`, `excluded`),
- `part_count` unambiguously counts included contributing parts,
- an `assembly_options(include_roles=[...])` opt-in counts a normally
  non-physical role in mass / center of mass / inertia consistently.

Synthetic tests use hand-computable geometry so mass and CoM are pinned, not
shape-checked. They fail before the ADR is implemented (no `included_roles` /
`excluded` keys, and `_assembly_center_of_mass` takes no `include_roles`).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def _agg():
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cadx.inspector import _assembly_center_of_mass

    return _assembly_center_of_mass


def _box_geometric_inertia(a: float, b: float, c: float) -> list[list[float]]:
    v = a * b * c
    return [
        [v / 12.0 * (b * b + c * c), 0.0, 0.0],
        [0.0, v / 12.0 * (a * a + c * c), 0.0],
        [0.0, 0.0, v / 12.0 * (a * a + b * b)],
    ]


def _two_parts_one_fixture(*, with_inertia: bool = False):
    """Two role='part' boxes at x=0 and x=10, one role='fixture' box at x=100.

    Equal volumes (1000 each). Parts-only CoM is x=5; all-three CoM is x=(0+10+100)/3.
    """

    g = _box_geometric_inertia(10, 10, 10)

    def box(label, role, x):
        obj = {
            "label": label,
            "role": role,
            "mass_properties": {"volume": 1000.0, "center_of_mass": [x, 0.0, 0.0]},
        }
        if with_inertia:
            obj["mass_properties"]["matrix_of_inertia"] = [row[:] for row in g]
        return obj

    return [box("a", "part", 0.0), box("b", "part", 10.0), box("jig", "fixture", 100.0)]


# --------------------------------------------------------------------------
# Part 1 — self-describing aggregate.
# --------------------------------------------------------------------------


def test_assembly_reports_included_roles_and_excluded():
    agg = _agg()
    assembly = agg(_two_parts_one_fixture())

    assert assembly["part_count"] == 2  # included-only
    assert assembly["included_roles"] == ["part"]
    assert assembly["excluded"] == [{"label": "jig", "role": "fixture"}]
    # Default aggregate unchanged: parts-only centroid at x=5, mass 2000.
    assert assembly["center_of_mass"][0] == pytest.approx(5.0)
    assert assembly["mass"] == pytest.approx(2000.0)


def test_excluded_omits_non_role_skips():
    """A part skipped for missing data is not a role exclusion."""

    agg = _agg()
    objects = [
        {"label": "good", "role": "part", "mass_properties": {"volume": 1000.0, "center_of_mass": [0, 0, 0]}},
        {"label": "novol", "role": "part", "mass_properties": {"center_of_mass": [5, 0, 0]}},  # no volume
        {"label": "jig", "role": "fixture", "mass_properties": {"volume": 1000.0, "center_of_mass": [100, 0, 0]}},
    ]
    assembly = agg(objects)
    # Only the fixture is a role exclusion; the volumeless part is a data skip.
    assert assembly["excluded"] == [{"label": "jig", "role": "fixture"}]
    assert assembly["part_count"] == 1  # only "good" contributes


def test_default_assembly_block_adds_only_metadata_keys():
    """Default behavior unchanged except the two new self-describing keys."""

    agg = _agg()
    objects = [
        {"label": "a", "role": "part", "mass_properties": {"volume": 1000.0, "center_of_mass": [0, 0, 0]}},
        {"label": "b", "role": "part", "mass_properties": {"volume": 3000.0, "center_of_mass": [20, 0, 0]}},
    ]
    assembly = agg(objects)
    # Pre-ADR values are exactly preserved.
    assert assembly["mass"] == pytest.approx(4000.0)
    assert assembly["center_of_mass"][0] == pytest.approx((1000 * 0 + 3000 * 20) / 4000)
    assert assembly["weighting"] == "volume"
    assert assembly["part_count"] == 2
    # Only the two new keys are additive.
    assert set(assembly) == {
        "center_of_mass",
        "mass",
        "weighting",
        "part_count",
        "included_roles",
        "excluded",
    }
    assert assembly["excluded"] == []


# --------------------------------------------------------------------------
# Part 2 — opt-in inclusion.
# --------------------------------------------------------------------------


def test_include_roles_counts_fixture_mass_com():
    agg = _agg()
    assembly = agg(_two_parts_one_fixture(), include_roles=["fixture"])

    assert assembly["part_count"] == 3
    assert assembly["excluded"] == []
    assert "fixture" in assembly["included_roles"]
    assert assembly["included_roles"] == ["fixture", "part"]
    # All three equal-volume boxes counted -> CoM at mean x = (0+10+100)/3.
    assert assembly["mass"] == pytest.approx(3000.0)
    assert assembly["center_of_mass"][0] == pytest.approx((0 + 10 + 100) / 3.0)


def test_include_roles_extends_inertia():
    """Opting in a role keeps mass/CoM/inertia composed from one part list."""

    agg = _agg()
    objects = _two_parts_one_fixture(with_inertia=True)
    default = agg(objects)
    opted = agg(objects, include_roles=["fixture"])

    # Default excludes the fixture from inertia; opt-in must include it and match
    # the closed form for three unit-density 10^3 boxes about the all-three CoM.
    com = (0 + 10 + 100) / 3.0
    g = 1000.0 / 12.0 * 200.0  # geometric spin term per box (mm^5)
    # Volume-weighted (no densities): weight=1, point "mass"=volume=1000.
    izz = sum(g + 1000.0 * (x - com) ** 2 for x in (0.0, 10.0, 100.0))
    tensor = opted["inertia"]["tensor"]
    assert tensor[2][2] == pytest.approx(izz)
    # The opt-in genuinely changed the tensor vs the parts-only default.
    assert opted["inertia"]["tensor"][2][2] != pytest.approx(default["inertia"]["tensor"][2][2])


# --------------------------------------------------------------------------
# End-to-end through `cadx run` (real geometry): registry -> diagnostics -> inspector.
# --------------------------------------------------------------------------

build123d = pytest.importorskip("build123d")

_DESIGN = """
from build123d import Box, Pos
from cadx import publish{extra_import}


def build(params):
    publish("a", Pos(0, 0, 0) * Box(10, 10, 10), role="part", density=1.0)
    publish("b", Pos(10, 0, 0) * Box(10, 10, 10), role="part", density=1.0)
    publish("jig", Pos(100, 0, 0) * Box(10, 10, 10), role="fixture", density=1.0)
{options_call}
"""


def _run(tmp_path: Path, *, include_fixture: bool) -> dict:
    design = tmp_path / "design.py"
    if include_fixture:
        body = _DESIGN.format(
            extra_import=", assembly_options",
            options_call='    assembly_options(include_roles=["fixture"])\n',
        )
    else:
        body = _DESIGN.format(extra_import="", options_call="")
    design.write_text(body, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    run_dir = tmp_path / payload["artifact_dir"]
    return json.loads((run_dir / "spatial.json").read_text())


def test_include_roles_end_to_end(tmp_path):
    default = _run(tmp_path, include_fixture=False)["assembly"]
    # Default: fixture excluded and reported as such.
    assert default["excluded"] == [{"label": "jig", "role": "fixture"}]
    assert default["mass"] == pytest.approx(2000.0)  # two 1000 mm^3 boxes, density 1.0

    opted = _run(tmp_path, include_fixture=True)["assembly"]
    # Opt-in: fixture counted, nothing role-excluded.
    assert opted["excluded"] == []
    assert "fixture" in opted["included_roles"]
    assert opted["mass"] == pytest.approx(3000.0)
    assert opted["center_of_mass"][0] == pytest.approx((0 + 10 + 100) / 3.0)

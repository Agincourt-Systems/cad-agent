"""ADR 0041: placement / mate passthrough on ``publish_sheet_metal`` (D-020).

A folded sheet part (a clevis) must be placeable and mateable through the public
API, exactly like a plain ``publish``. Before this ADR ``publish_sheet_metal``
dropped both arguments and always identity-placed the part. These tests drive the
real ``cadx run`` subprocess path and assert that a folded part mated to a parent
resolves to the same placement as the equivalent plain-published solid, that a
placement-only sheet part lands where placed, and — critically — that the
flat-pattern bend table / DXF / features stay in flat coordinates regardless of
placement.

Red before implementation: ``publish_sheet_metal`` has no ``placement`` / ``mate``
parameters, so passing ``mate=`` raises ``TypeError`` and the run errors.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytest.importorskip("build123d")
ezdxf = pytest.importorskip("ezdxf")


# The module L-bracket, reused so the flat-pattern expectations are one source.
FLANGE_A = 40.0
FLANGE_B = 25.0
WIDTH = 30.0
THICKNESS = 3.0
K_FACTOR = 0.44
INSIDE_RADIUS = 3.0
ANGLE_DEG = 90.0
EXPECTED_BA = (math.pi / 180.0) * ANGLE_DEG * (INSIDE_RADIUS + K_FACTOR * THICKNESS)
FLAT_BEND_X = FLANGE_A + EXPECTED_BA / 2.0  # bend-line developed x in the flat frame


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


def _run(tmp_path: Path, source: str) -> Path:
    (tmp_path / "design.py").write_text(source, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    result = run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _objects(run_dir: Path) -> dict:
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    return {obj["label"]: obj for obj in spatial["objects"]}


# A base part plus the SAME folded solid published two ways with an identical
# revolute mate: once plainly, once as sheet metal. The two children must resolve
# to the same placement, proving the passthrough is faithful to the plain path.
_MATE_COMPARE = f"""
from build123d import Box, Location
from cadx import publish, publish_sheet_metal, mate
from cadx.sheetmetal import bend


def _spec():
    return mate(to="base", kind="revolute",
                anchor=Location((-5, 0, 0)), target=Location((30, 0, 6)), angle=35)


def build(params):
    publish("base", Box(60, 40, 6), role="final")
    part = bend({FLANGE_A}, {FLANGE_B}, angle_deg={ANGLE_DEG}, inside_radius={INSIDE_RADIUS},
                k_factor={K_FACTOR}, thickness={THICKNESS}, width={WIDTH}, direction="up")
    publish("plain_child", part.folded, mate=_spec())
    publish_sheet_metal("sheet_child", part, mate=_spec())
    return part.folded
"""


def test_sheet_metal_revolute_mate_matches_plain(tmp_path):
    """A folded part mated revolute resolves to the same placement as the same
    solid published plainly with the same mate."""

    run_dir = _run(tmp_path, _MATE_COMPARE)
    objs = _objects(run_dir)
    plain = objs["plain_child"]["placement"]
    sheet = objs["sheet_child"]["placement"]

    assert sheet["position"] == pytest.approx(plain["position"], abs=1e-6)
    assert sheet["orientation"] == pytest.approx(plain["orientation"], abs=1e-6)
    # Sanity: the mate actually posed the part (not left at identity).
    assert objs["sheet_child"]["mate"]["kind"] == "revolute"
    assert objs["sheet_child"]["mate"]["angle"] == 35


def test_sheet_metal_placement_only_lands_where_placed(tmp_path):
    """A sheet part published with an explicit placement reports that translation."""

    source = f"""
from build123d import Location
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    part = bend({FLANGE_A}, {FLANGE_B}, angle_deg={ANGLE_DEG}, inside_radius={INSIDE_RADIUS},
                k_factor={K_FACTOR}, thickness={THICKNESS}, width={WIDTH}, direction="up")
    publish_sheet_metal("bracket", part, placement=Location((5, 10, 2)))
    return part.folded
"""
    run_dir = _run(tmp_path, source)
    placement = _objects(run_dir)["bracket"]["placement"]
    assert placement["position"] == pytest.approx([5, 10, 2], abs=1e-6)


# Two designs identical but for a placement, so we can prove the flat-pattern
# artifacts are byte-identical whether or not the folded part is placed.
def _bracket_design(placement: str) -> str:
    return f"""
from build123d import Location
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    part = bend({FLANGE_A}, {FLANGE_B}, angle_deg={ANGLE_DEG}, inside_radius={INSIDE_RADIUS},
                k_factor={K_FACTOR}, thickness={THICKNESS}, width={WIDTH}, direction="up")
    publish_sheet_metal("bracket", part{placement})
    return part.folded
"""


def _flat_frame_facts(run_dir: Path) -> dict:
    bends = json.loads((run_dir / "bends.json").read_text(encoding="utf-8"))["bends"]
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bend_feat = next(f for f in spatial["features"] if f.get("kind") == "bend")
    doc = ezdxf.readfile(str(run_dir / "bracket.dxf"))
    bend_entity = next(e for e in doc.modelspace() if e.dxf.layer == "bend")
    return {
        "table_line_x": bends[0]["line"][0][0],
        "feature_line_x": bend_feat["line"][0][0],
        "dxf_bend_x": bend_entity.dxf.start.x,
    }


def test_placement_leaves_flat_frame_unchanged(tmp_path):
    """Placement moves only the folded solid: bends.json, the bend feature line,
    and the DXF bend line all stay at the flat-pattern developed x."""

    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    placed = _flat_frame_facts(_run(dir_a, _bracket_design(", placement=Location((100, 50, 20))")))
    unplaced = _flat_frame_facts(_run(dir_b, _bracket_design("")))

    for key in ("table_line_x", "feature_line_x", "dxf_bend_x"):
        assert placed[key] == pytest.approx(FLAT_BEND_X, abs=1e-3), key
        assert placed[key] == pytest.approx(unplaced[key], abs=1e-9), key


def test_sheet_metal_default_placement_is_identity(tmp_path):
    """With neither argument the sheet part is identity-placed and carries no mate
    — byte-identical to before this ADR."""

    run_dir = _run(tmp_path, _bracket_design(""))
    bracket = _objects(run_dir)["bracket"]
    assert bracket["placement"] == {"position": [0, 0, 0], "orientation": [0, 0, 0]}
    assert "mate" not in bracket


def test_sheet_metal_placement_and_mate_conflict():
    """Supplying both placement and mate raises ValueError, mirroring publish()."""

    from build123d import Location

    from cadx import clear_registry, mate, publish_sheet_metal
    from cadx.sheetmetal import bend

    clear_registry()
    part = bend(FLANGE_A, FLANGE_B, angle_deg=ANGLE_DEG, inside_radius=INSIDE_RADIUS,
                k_factor=K_FACTOR, thickness=THICKNESS, width=WIDTH, direction="up")
    with pytest.raises(ValueError):
        publish_sheet_metal(
            "bracket", part,
            placement=Location((1, 2, 3)),
            mate=mate(to="base", anchor=Location((0, 0, 0)), target=Location((0, 0, 0))),
        )

"""ADR 0016: sheet-metal bend and flat-pattern unfold.

A bent bracket is described once via ``bend(...)`` and yields both a folded 3D
solid (for assembly/clearance) and a flat pattern with the correct developed
length, bend lines on a ``bend`` DXF layer, and a machine-readable ``bends.json``
bend table. These tests assert the bend-allowance arithmetic, the DXF layer
contents, the folded envelope, and the emitted bend table.
"""

import json
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


pytest.importorskip("build123d")
ezdxf = pytest.importorskip("ezdxf")


# Concrete L-bracket used across the tests. Kept in one place so the expected
# arithmetic and the design source agree.
FLANGE_A = 40.0
FLANGE_B = 25.0
WIDTH = 30.0
THICKNESS = 3.0
K_FACTOR = 0.44
INSIDE_RADIUS = 3.0
ANGLE_DEG = 90.0

EXPECTED_BA = (math.pi / 180.0) * ANGLE_DEG * (INSIDE_RADIUS + K_FACTOR * THICKNESS)
EXPECTED_DEVELOPED_LENGTH = FLANGE_A + EXPECTED_BA + FLANGE_B


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run cadx through the same subprocess path agents use."""

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


LBRACKET_DESIGN = f"""
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    part = bend(
        {FLANGE_A},
        {FLANGE_B},
        angle_deg={ANGLE_DEG},
        inside_radius={INSIDE_RADIUS},
        k_factor={K_FACTOR},
        thickness={THICKNESS},
        width={WIDTH},
        direction="up",
    )
    publish_sheet_metal("bracket", part)
    return part.folded
"""


def _write_design(tmp_path: Path) -> Path:
    design = tmp_path / "design.py"
    design.write_text(LBRACKET_DESIGN, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def test_lbracket_developed_length():
    """flange A + bend allowance + flange B equals the flat pattern length."""

    from cadx.sheetmetal import bend

    part = bend(
        FLANGE_A,
        FLANGE_B,
        angle_deg=ANGLE_DEG,
        inside_radius=INSIDE_RADIUS,
        k_factor=K_FACTOR,
        thickness=THICKNESS,
        width=WIDTH,
        direction="up",
    )

    # The arithmetic is the load-bearing assertion: BA must follow the formula
    # and the developed length must be the sum, not just the outer flanges.
    assert part.developed_length == pytest.approx(EXPECTED_DEVELOPED_LENGTH, abs=1e-6)
    assert part.developed_length > FLANGE_A + FLANGE_B  # blank is longer by BA

    # The actual flat profile geometry must measure to that developed length.
    flat_bbox = part.flat_profile.bounding_box()
    assert flat_bbox.size.X == pytest.approx(EXPECTED_DEVELOPED_LENGTH, abs=1e-3)
    assert flat_bbox.size.Y == pytest.approx(WIDTH, abs=1e-3)


def test_bend_line_layer(tmp_path):
    """The flat DXF has exactly one bend-layer entity; folded bbox matches."""

    _write_design(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    assert payload["status"] == "ok"
    run_dir = tmp_path / payload["artifact_dir"]

    dxf_path = run_dir / "bracket.dxf"
    assert dxf_path.exists(), "sheet-metal flat DXF was not exported"

    doc = ezdxf.readfile(str(dxf_path))
    assert doc.header.get("$INSUNITS") == 4  # millimeters

    msp = doc.modelspace()
    by_layer = Counter(entity.dxf.layer for entity in msp)
    assert by_layer["bend"] == 1, f"expected one bend-layer entity, got {dict(by_layer)}"
    assert by_layer["cut"] == 4, f"expected four cut-layer outline lines, got {dict(by_layer)}"

    bend_entity = next(entity for entity in msp if entity.dxf.layer == "bend")
    assert bend_entity.dxftype() == "LINE"
    expected_x = FLANGE_A + EXPECTED_BA / 2.0
    assert bend_entity.dxf.start.x == pytest.approx(expected_x, abs=1e-3)
    assert bend_entity.dxf.end.x == pytest.approx(expected_x, abs=1e-3)

    # Folded 3D envelope: base flange + standing wall.
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bracket = next(obj for obj in spatial["objects"] if obj["label"] == "bracket")
    size = bracket["bbox"]["size"]
    assert size[0] == pytest.approx(FLANGE_A + THICKNESS, abs=1e-3)
    assert size[1] == pytest.approx(WIDTH, abs=1e-3)
    assert size[2] == pytest.approx(FLANGE_B, abs=1e-3)


def test_bend_table_emitted(tmp_path):
    """bends.json lists angle/direction/radius per bend and is export-recorded."""

    _write_design(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    bends_path = run_dir / "bends.json"
    assert bends_path.exists(), "bends.json bend table was not written"
    table = json.loads(bends_path.read_text(encoding="utf-8"))
    assert table["units"] == "mm"
    bends = table["bends"]
    assert len(bends) == 1
    row = bends[0]
    assert row["angle"] == pytest.approx(ANGLE_DEG)
    assert row["direction"] == "up"
    assert row["inside_radius"] == pytest.approx(INSIDE_RADIUS)
    assert "line" in row  # the bend line coordinates travel with the table

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    bends_records = [e for e in diagnostics["exports"] if e.get("format") == "bends"]
    assert len(bends_records) == 1
    recorded = Path(bends_records[0]["path"])
    if not recorded.is_absolute():
        recorded = run_dir / recorded.name
    assert recorded.exists()
    assert bends_records[0]["units"] == "mm"


def test_non_right_angle_bend_down():
    """A non-90 down bend uses the BA formula and yields a valid folded solid."""

    from cadx.sheetmetal import bend

    angle = 45.0
    part = bend(
        FLANGE_A,
        FLANGE_B,
        angle_deg=angle,
        inside_radius=INSIDE_RADIUS,
        k_factor=K_FACTOR,
        thickness=THICKNESS,
        width=WIDTH,
        direction="down",
    )
    expected_ba = (math.pi / 180.0) * angle * (INSIDE_RADIUS + K_FACTOR * THICKNESS)
    assert part.developed_length == pytest.approx(FLANGE_A + expected_ba + FLANGE_B, abs=1e-6)
    # The folded solid is a valid (non-empty) representative body, and the flat
    # pattern still measures to the developed length.
    assert part.folded.volume > 0
    assert part.flat_profile.bounding_box().size.X == pytest.approx(part.developed_length, abs=1e-3)
    assert part.bends[0]["direction"] == "down"
    assert part.bends[0]["angle"] == pytest.approx(angle)


def test_bend_check_pass_and_fail(tmp_path):
    """The bend evaluate check asserts count/angle/direction/radius from bends.json."""

    _write_design(tmp_path)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    passing = tmp_path / "pass.yaml"
    passing.write_text(
        """
units: mm
checks:
  - id: right_angle_bend
    type: bend
    count: 1
    angle: 90
    direction: up
    inside_radius: 3
    tolerance: 0.01
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
  - id: wrong_angle
    type: bend
    angle: 120
    tolerance: 0.01
""",
        encoding="utf-8",
    )
    result = parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(failing)))
    assert result["status"] == "fail"
    assert "wrong_angle" in result["failed"]


def test_bend_check_missing_table_fails(tmp_path):
    """A bend check on a run with no bends.json fails with an error, not a crash."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from cadx.evaluate import _check_bend

    result = _check_bend(tmp_path, {"id": "b", "type": "bend", "count": 1})
    assert result["status"] == "fail"
    assert "bend table" in result["error"]


def test_down_bend_envelope_matches_up(tmp_path):
    """A 90 down bend has the same closed-form envelope as an up bend."""

    from cadx.sheetmetal import bend

    down = bend(
        FLANGE_A, FLANGE_B, angle_deg=90.0, inside_radius=INSIDE_RADIUS,
        k_factor=K_FACTOR, thickness=THICKNESS, width=WIDTH, direction="down",
    )
    size = down.folded.bounding_box().size
    assert size.X == pytest.approx(FLANGE_A + THICKNESS, abs=1e-3)
    assert size.Y == pytest.approx(WIDTH, abs=1e-3)
    assert size.Z == pytest.approx(FLANGE_B, abs=1e-3)  # not flange_b + thickness


def test_two_sheet_metal_parts_share_one_bend_table(tmp_path):
    """Multiple bent parts aggregate into one bends.json, each row label-tagged,
    with a single bends export record (no clobbering)."""

    design = tmp_path / "design.py"
    design.write_text(
        """
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend


def build(params):
    a = bend(40, 25, angle_deg=90, inside_radius=3, k_factor=0.44, thickness=3, width=30, direction="up")
    b = bend(30, 20, angle_deg=120, inside_radius=2, k_factor=0.4, thickness=2, width=20, direction="down")
    publish_sheet_metal("bracketA", a)
    publish_sheet_metal("bracketB", b)
    return a.folded
""",
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    table = json.loads((run_dir / "bends.json").read_text(encoding="utf-8"))
    labels = sorted(row["label"] for row in table["bends"])
    assert labels == ["bracketA", "bracketB"]  # both survive, neither clobbered

    diagnostics = json.loads((run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    bends_records = [e for e in diagnostics["exports"] if e.get("format") == "bends"]
    assert len(bends_records) == 1  # one aggregate record, not one-per-part-same-path
    assert (run_dir / "bracketA.dxf").exists() and (run_dir / "bracketB.dxf").exists()


def test_bend_rejects_invalid_inputs():
    """bend() validates its inputs with clear errors instead of opaque kernel errors."""

    from cadx.sheetmetal import bend

    kwargs = dict(angle_deg=90, inside_radius=3, k_factor=0.44, thickness=3, width=30)
    with pytest.raises(ValueError):
        bend(40, 25, **{**kwargs, "width": 0})
    with pytest.raises(ValueError):
        bend(40, 25, **{**kwargs, "thickness": 0})
    with pytest.raises(ValueError):
        bend(40, 25, **{**kwargs, "direction": "sideways"})

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

# ADR 0034: the folded solid is a swept constant-thickness ribbon whose bend
# region is an annular sector of neutral radius rho = R + K*t. Conserving the
# blank volume keeps the straight flange runs at their flat lengths, so the
# 90 deg-bend bounding box grows at the corner to
# (flange_a + rho + t/2, width, flange_b + rho + t/2).
EXPECTED_RHO = INSIDE_RADIUS + K_FACTOR * THICKNESS
EXPECTED_ENV_X = FLANGE_A + EXPECTED_RHO + THICKNESS / 2.0
EXPECTED_ENV_Z = FLANGE_B + EXPECTED_RHO + THICKNESS / 2.0
EXPECTED_BLANK_VOLUME = EXPECTED_DEVELOPED_LENGTH * THICKNESS * WIDTH


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

    # Folded 3D envelope (ADR 0034): swept ribbon with a rounded bend corner. The
    # extents grow at the corner by the bend geometry (rho + t/2); the straight
    # flange runs are still flange_a / flange_b.
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bracket = next(obj for obj in spatial["objects"] if obj["label"] == "bracket")
    size = bracket["bbox"]["size"]
    assert size[0] == pytest.approx(EXPECTED_ENV_X, abs=1e-3)
    assert size[1] == pytest.approx(WIDTH, abs=1e-3)
    assert size[2] == pytest.approx(EXPECTED_ENV_Z, abs=1e-3)


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
    """A 90 down bend has the same (mirror-symmetric) envelope size as an up bend."""

    from cadx.sheetmetal import bend

    down = bend(
        FLANGE_A, FLANGE_B, angle_deg=90.0, inside_radius=INSIDE_RADIUS,
        k_factor=K_FACTOR, thickness=THICKNESS, width=WIDTH, direction="down",
    )
    size = down.folded.bounding_box().size
    # ADR 0034 swept-ribbon envelope; a down fold mirrors the up fold, so the
    # bounding-box SIZE is identical.
    assert size.X == pytest.approx(EXPECTED_ENV_X, abs=1e-3)
    assert size.Y == pytest.approx(WIDTH, abs=1e-3)
    assert size.Z == pytest.approx(EXPECTED_ENV_Z, abs=1e-3)


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


# --- ADR 0032: multi-bend chains (D-003) ------------------------------------
#
# The U-channel from the deficiency: two 90 deg bends over three flanges cut as
# ONE flat blank. The parameters are chosen to reproduce the deficiency's exact
# figures: each naive two-flange bend() call would report 30+40+BA == 75.18 mm
# (double-counting the 40 mm web), while the correct single blank is
# 30+40+30+2*BA == 110.36 mm. The expected values below are DERIVED from the BA
# formula, not hardcoded approximations.
UCHAN_FLANGES = [30.0, 40.0, 30.0]
UCHAN_THICKNESS = 2.29
UCHAN_RADIUS = 2.29
UCHAN_K = 0.44
UCHAN_WIDTH = 30.0
UCHAN_BA = (math.pi / 180.0) * 90.0 * (UCHAN_RADIUS + UCHAN_K * UCHAN_THICKNESS)
UCHAN_DEVELOPED = sum(UCHAN_FLANGES) + 2.0 * UCHAN_BA


def _uchannel():
    from cadx.sheetmetal import bend_chain

    bends = [
        dict(angle_deg=90.0, inside_radius=UCHAN_RADIUS, k_factor=UCHAN_K, direction="up"),
        dict(angle_deg=90.0, inside_radius=UCHAN_RADIUS, k_factor=UCHAN_K, direction="up"),
    ]
    return bend_chain(UCHAN_FLANGES, bends, thickness=UCHAN_THICKNESS, width=UCHAN_WIDTH)


def test_uchannel_single_blank():
    """Two 90 deg bends become one blank of the correct developed length with two
    bend lines and a single connected folded solid."""

    part = _uchannel()

    # One blank, developed length = sum(flanges) + sum(BA); the web is counted once.
    assert part.developed_length == pytest.approx(UCHAN_DEVELOPED, abs=1e-6)
    assert part.developed_length == pytest.approx(110.36, abs=0.01)
    # Sanity: NOT the naive per-pair double-count.
    naive_pair = UCHAN_FLANGES[0] + UCHAN_FLANGES[1] + UCHAN_BA
    assert naive_pair == pytest.approx(75.18, abs=0.01)
    assert part.developed_length > naive_pair

    # The single flat blank measures to the developed length across its width.
    flat_bbox = part.flat_profile.bounding_box()
    assert flat_bbox.size.X == pytest.approx(UCHAN_DEVELOPED, abs=1e-3)
    assert flat_bbox.size.Y == pytest.approx(UCHAN_WIDTH, abs=1e-3)

    # Two bend lines / two bend-table rows at the computed developed positions.
    assert len(part.bend_lines) == 2
    assert len(part.bends) == 2
    x0 = UCHAN_FLANGES[0] + UCHAN_BA / 2.0
    x1 = UCHAN_FLANGES[0] + UCHAN_FLANGES[1] + UCHAN_BA + UCHAN_BA / 2.0
    got = sorted(row["line"][0][0] for row in part.bends)
    assert got[0] == pytest.approx(x0, abs=1e-6)
    assert got[1] == pytest.approx(x1, abs=1e-6)
    for row in part.bends:
        assert row["angle"] == pytest.approx(90.0)
        assert row["inside_radius"] == pytest.approx(UCHAN_RADIUS)
        assert row["direction"] == "up"

    # One CONNECTED folded solid usable by interference / CoM machinery.
    assert part.folded.volume > 0
    assert len(part.folded.solids()) == 1


def test_bend_delegates_to_chain():
    """bend() is unchanged in its flat-pattern contract; its folded solid is the
    ADR 0034 swept-ribbon envelope."""

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
    assert part.developed_length == pytest.approx(EXPECTED_DEVELOPED_LENGTH, abs=1e-6)
    assert len(part.bend_lines) == 1
    assert len(part.bends) == 1
    size = part.folded.bounding_box().size
    assert size.X == pytest.approx(EXPECTED_ENV_X, abs=1e-3)
    assert size.Y == pytest.approx(WIDTH, abs=1e-3)
    assert size.Z == pytest.approx(EXPECTED_ENV_Z, abs=1e-3)


# --- ADR 0034: bend-region volume via a swept ribbon (D-005) ------------------


def test_folded_volume_matches_blank():
    """The folded solid conserves the blank volume (developed_length * t * width),
    within a tight tolerance. The old two-box model was ~4.9% low."""

    from cadx.sheetmetal import bend

    # The deficiency's exact case: 40/60 flanges, width 30, t=R=2.29, K=0.44.
    thickness = 2.29
    part = bend(
        40.0, 60.0, angle_deg=90.0, inside_radius=2.29, k_factor=0.44,
        thickness=thickness, width=30.0, direction="up",
    )
    blank_volume = part.developed_length * thickness * 30.0
    assert blank_volume == pytest.approx(7225.86, abs=0.1)
    assert part.folded.volume == pytest.approx(blank_volume, rel=1e-4)
    # Sanity: it is materially larger than the old sharp-corner 6870 mm^3.
    assert part.folded.volume > 7000.0


def test_chain_folded_volume_matches_blank():
    """A multi-bend chain also conserves its blank volume and stays one solid."""

    part = _uchannel()
    blank_volume = part.developed_length * UCHAN_THICKNESS * UCHAN_WIDTH
    assert part.folded.volume == pytest.approx(blank_volume, rel=1e-4)
    assert len(part.folded.solids()) == 1


def test_bend_chain_validates_lengths():
    """A flanges/bends mismatch or a bad direction raises a clear ValueError."""

    from cadx.sheetmetal import bend_chain

    good = dict(angle_deg=90.0, inside_radius=3.0, k_factor=0.44, direction="up")
    # Two flanges need exactly one bend.
    with pytest.raises(ValueError):
        bend_chain([30.0, 40.0], [good, good], thickness=3.0, width=30.0)
    # Three flanges need exactly two bends.
    with pytest.raises(ValueError):
        bend_chain([30.0, 40.0, 30.0], [good], thickness=3.0, width=30.0)
    # Fewer than two flanges is degenerate.
    with pytest.raises(ValueError):
        bend_chain([30.0], [], thickness=3.0, width=30.0)
    # Bad direction inside a bend dict.
    with pytest.raises(ValueError):
        bend_chain([30.0, 40.0], [{**good, "direction": "sideways"}], thickness=3.0, width=30.0)


# --- ADR 0033: bends as spatial features so bend DFM rules fire (D-004) -------
#
# The deficiency: a part folded with a sub-minimum inside radius silently PASSES
# manufacturability because publish_sheet_metal records bends only in bends.json,
# never as kind="bend" spatial features, so the min_bend_radius / hole_to_bend
# DFM rules are inert on the only path that produces bends. These tests drive the
# real cadx run + evaluate subprocess path.

# Sub-minimum radius on 2.29 mm stock: 0.5 mm ~= 0.22 t, which SendCutSend rejects.
DFM_THICKNESS = 2.29


def _sheet_dfm_design(inside_radius: float, extra: str = "") -> str:
    """A design that folds a 40/25 bracket with the given inside radius.

    ``extra`` is optional python injected after publish_sheet_metal (e.g. to
    publish a hole feature in the flat-pattern frame).
    """

    return f"""
from cadx import publish_sheet_metal, publish_feature
from cadx.sheetmetal import bend


def build(params):
    part = bend(
        40.0, 25.0,
        angle_deg=90.0, inside_radius={inside_radius}, k_factor=0.44,
        thickness={DFM_THICKNESS}, width=30.0, direction="up",
    )
    publish_sheet_metal("bracket", part)
{extra}
    return part.folded
"""


def _run_design_src(tmp_path: Path, source: str) -> Path:
    design = tmp_path / "design.py"
    design.write_text(source, encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", "design.py", "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _evaluate_dfm(tmp_path: Path, run_dir: Path, rules_yaml: str) -> dict:
    requirements = tmp_path / "dfm.yaml"
    requirements.write_text(rules_yaml, encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    assert len(checks["checks"]) == 1, checks
    return checks["checks"][0]


_MIN_BEND_RADIUS_RULE = f"""
units: mm
checks:
  - id: bracket_dfm
    type: manufacturability
    object: obj.bracket
    thickness: {DFM_THICKNESS}
    rules:
      - rule: min_bend_radius
"""


def test_min_bend_radius_fires_on_real_bend_flow(tmp_path):
    """A sub-minimum inside radius must now FAIL manufacturability (it silently
    passed in the deficiency because no kind='bend' feature was ever published)."""

    run_dir = _run_design_src(tmp_path, _sheet_dfm_design(inside_radius=0.5))

    # The emitted bend feature is visible in spatial.json.
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bend_feats = [f for f in spatial["features"] if f.get("kind") == "bend"]
    assert len(bend_feats) == 1, spatial["features"]
    assert bend_feats[0]["inside_radius"] == pytest.approx(0.5)
    assert bend_feats[0]["source_object"] == "obj.bracket"
    assert "line" in bend_feats[0]

    check = _evaluate_dfm(tmp_path, run_dir, _MIN_BEND_RADIUS_RULE)
    assert check["status"] == "fail", check
    cited = {fid for v in check["violations"] if v["rule"] == "min_bend_radius" for fid in v["features"]}
    assert "feat.bracket_bend_0" in cited


def test_min_bend_radius_passes_adequate_radius(tmp_path):
    """The rule is bound, not always-failing: an adequate radius passes."""

    run_dir = _run_design_src(tmp_path, _sheet_dfm_design(inside_radius=DFM_THICKNESS))
    check = _evaluate_dfm(tmp_path, run_dir, _MIN_BEND_RADIUS_RULE)
    assert check["status"] == "pass", check


def test_hole_to_bend_binds_on_real_bend_flow(tmp_path):
    """A hole published in the flat-pattern frame too close to the bend line fails
    hole_to_bend, naming the hole and the bend."""

    # BA/2 for a 90 deg bend, r=2.29, k=0.44, t=2.29 -> bend line at x = 40 + BA/2.
    import math as _math

    ba = (_math.pi / 180.0) * 90.0 * (DFM_THICKNESS + 0.44 * DFM_THICKNESS)
    bend_x = 40.0 + ba / 2.0
    # Hole 1 mm past the bend line: edge is ~1 - 1.5 mm from the line, well within
    # the hole_to_bend limit (2 * thickness).
    hole_center_x = bend_x + 1.0
    extra = f"""    publish_feature(
        "crowding_hole",
        kind="cylindrical_hole",
        diameter=3.0,
        center=[{hole_center_x}, 0.0, 0.0],
        axis=[0, 0, 1],
        through=True,
        source_object="obj.bracket",
    )"""
    run_dir = _run_design_src(tmp_path, _sheet_dfm_design(inside_radius=DFM_THICKNESS, extra=extra))

    rules = f"""
units: mm
checks:
  - id: bracket_dfm
    type: manufacturability
    object: obj.bracket
    thickness: {DFM_THICKNESS}
    rules:
      - rule: hole_to_bend
"""
    check = _evaluate_dfm(tmp_path, run_dir, rules)
    assert check["status"] == "fail", check
    cited = {fid for v in check["violations"] if v["rule"] == "hole_to_bend" for fid in v["features"]}
    assert "feat.crowding_hole" in cited
    assert "feat.bracket_bend_0" in cited


def test_bend_chain_emits_two_bend_features(tmp_path):
    """A U-channel chain publishes one kind='bend' feature per bend."""

    design = """
from cadx import publish_sheet_metal
from cadx.sheetmetal import bend_chain


def build(params):
    bends = [
        dict(angle_deg=90.0, inside_radius=2.29, k_factor=0.44, direction="up"),
        dict(angle_deg=90.0, inside_radius=2.29, k_factor=0.44, direction="up"),
    ]
    part = bend_chain([30.0, 40.0, 30.0], bends, thickness=2.29, width=30.0)
    publish_sheet_metal("uchan", part)
    return part.folded
"""
    run_dir = _run_design_src(tmp_path, design)
    spatial = json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))
    bend_feats = sorted(
        (f for f in spatial["features"] if f.get("kind") == "bend"), key=lambda f: f["id"]
    )
    assert [f["id"] for f in bend_feats] == ["feat.uchan_bend_0", "feat.uchan_bend_1"]
    for feat in bend_feats:
        assert feat["inside_radius"] == pytest.approx(2.29)
        assert feat["source_object"] == "obj.uchan"
        assert "line" in feat

"""ADR 0051: suppress folded-frame re-detections of authored sheet holes (D-029).

An authored sheet-metal hole (the ADR 0040 ``holes=`` API) is published in the
FLAT-pattern frame but is also bored out of the FOLDED solid, so STEP
auto-detection (``inspector.py``) re-observes it as a folded-frame cylindrical
feature (usually a ``cylindrical_boss``). That phantom cannot deduplicate against
the flat publication (different frame, different kind) and false-positives the DFM
rules — the D-029 composition failure between ADR 0040 (holes=) and ADR 0044
(``frame: flat``).

These tests drive the public CLI end-to-end (as an agent would): author a bent
part with a hole, ``run`` it (which auto-detects and writes ``spatial.json``), then
``evaluate`` a manufacturability check. They therefore need a real CAD kernel and
are skipped without build123d. A pure-Python unit test covers the ``exclude_detected``
filter directly, without the kernel.
"""

import json
import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# --- shared single-bend bracket geometry -------------------------------------

FLANGE_A = 20.0
FLANGE_B = 60.0
ANGLE_DEG = 90.0
INSIDE_RADIUS = 3.0
K_FACTOR = 0.44
THICKNESS = 2.29
WIDTH = 30.0

# Bend allowance and developed length for the bracket, so a test can predict the
# flat-frame geometry (bend-line position, flange-1 leading edge).
_BA = (math.pi / 180.0) * ANGLE_DEG * (INSIDE_RADIUS + K_FACTOR * THICKNESS)
DEVELOPED_LENGTH = FLANGE_A + FLANGE_B + _BA
BEND_X = FLANGE_A + _BA / 2.0  # developed x of the single bend line
FLANGE1_START_X = FLANGE_A + _BA  # developed x of flange 1's leading edge


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke cadx through its public CLI, as agents do."""

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


def _run_design(tmp_path: Path, body: str) -> Path:
    """Write and run a design whose ``build`` body is ``body``; return the run dir."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    design = tmp_path / "design.py"
    design.write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _spatial(run_dir: Path) -> dict:
    return json.loads((run_dir / "spatial.json").read_text(encoding="utf-8"))


def _evaluate(tmp_path: Path, run_dir: Path, requirements_yaml: str) -> dict:
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(textwrap.dedent(requirements_yaml), encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    assert len(checks["checks"]) == 1
    return checks["checks"][0]


def _rule_failures(check: dict, rule: str) -> set:
    return {f for v in check.get("violations", []) if v["rule"] == rule for f in v["features"]}


def _bracket_design(hole_u: float, diameter: float = 3.0, flange: int = 1) -> str:
    """A single-bend bracket carrying ONE hole authored via the holes= API.

    The hole sits on ``flange`` at along-flange offset ``hole_u`` from that
    flange's leading edge. This is the exact D-029 shape: the hole is authored in
    flange-local coordinates, published in the flat frame, AND bored out of the
    folded solid (where STEP detection re-observes it).
    """

    return f"""
        from cadx import publish_sheet_metal
        from cadx.sheetmetal import bend


        def build(params):
            part = bend(
                {FLANGE_A}, {FLANGE_B},
                angle_deg={ANGLE_DEG}, inside_radius={INSIDE_RADIUS}, k_factor={K_FACTOR},
                thickness={THICKNESS}, width={WIDTH}, direction="up",
                holes=[{{"flange": {flange}, "u": {hole_u}, "v": 0.0, "diameter": {diameter}}}],
            )
            publish_sheet_metal("bracket", part, role="final")
            return part.folded
    """


# --- ADR 0051 core: hole_to_bend green/red on authored holes -----------------


def test_hole_to_bend_green_on_authored_holes(tmp_path):
    """A compliant authored hole passes an UNFILTERED hole_to_bend check.

    The hole sits on flange 1 at u=25 (developed x ~= 51, ~28 mm from the bend at
    developed x ~= 23) — far clear of the 2*t limit. Before the fix, the folded
    re-detection of this hole (a phantom cylindrical_boss at folded x ~= 24, right
    on the bend) false-positives hole_to_bend (observed ~= -0.64). After the fix
    the phantom is suppressed, so the check passes: the provable GREEN path D-029
    says is missing.
    """

    run_dir = _run_design(tmp_path, _bracket_design(hole_u=25.0))
    check = _evaluate(
        tmp_path,
        run_dir,
        f"""
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: {THICKNESS}
            rules:
              - rule: hole_to_bend
        """,
    )
    assert check["status"] == "pass", check
    assert _rule_failures(check, "hole_to_bend") == set()


def test_hole_to_bend_red_when_hole_near_bend(tmp_path):
    """Moving the authored hole close to the bend fails hole_to_bend (red direction).

    The failure must cite the AUTHORED hole (feat.bracket_hole_0), proving the
    real hole — not a phantom — drives the violation. This is the negative control
    for the green test above.
    """

    # Flange-1 hole at u=2: its edge clears the bend region but lands well inside
    # the 2*t hole_to_bend limit.
    run_dir = _run_design(tmp_path, _bracket_design(hole_u=2.0))
    check = _evaluate(
        tmp_path,
        run_dir,
        f"""
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: {THICKNESS}
            rules:
              - rule: hole_to_bend
        """,
    )
    assert check["status"] == "fail", check
    cited = _rule_failures(check, "hole_to_bend")
    assert "feat.bracket_hole_0" in cited
    assert "feat.bracket_bend_0" in cited
    # No phantom auto-detected feature may be cited.
    assert not any(fid.startswith("feat.auto_") for fid in cited), cited


def test_authored_hole_not_duplicated_in_spatial(tmp_path):
    """spatial.json carries the authored hole, not a folded re-detection of it.

    The authored cylindrical_hole survives (in the flat frame) and is marked
    confirmed_by_detection; no detected cylindrical feature on that source remains
    to double-count the bore.
    """

    run_dir = _run_design(tmp_path, _bracket_design(hole_u=25.0))
    features = _spatial(run_dir)["features"]

    cylindrical = [
        f
        for f in features
        if f.get("source_object") == "obj.bracket"
        and f.get("kind") in {"cylindrical_hole", "cylindrical_boss"}
    ]
    # Exactly the one authored hole; the phantom folded boss is suppressed.
    assert len(cylindrical) == 1, cylindrical
    hole = cylindrical[0]
    assert hole["id"] == "feat.bracket_hole_0"
    assert hole["kind"] == "cylindrical_hole"
    assert not hole.get("detected")
    # Corroborated by the (now suppressed) folded re-detection.
    assert hole.get("confirmed_by_detection") is True
    # Planar datums are still detected — suppression targets only the bore.
    assert any(f.get("kind") == "planar_datum" and f.get("detected") for f in features)


def test_frame_flat_composes_with_authored_holes(tmp_path):
    """D-029 headline: frame:flat + hole_to_bend + hole_to_edge pass on authored holes.

    Composes ADR 0040 (holes=) + ADR 0044 (frame:flat) + ADR 0050 (blank extents
    from sheet metadata, so the check needs no blank_length/blank_width). Before
    the fix the folded re-detection false-positives both rules; after, the single
    coherent flat-frame pass D-029 asks for succeeds.
    """

    run_dir = _run_design(tmp_path, _bracket_design(hole_u=25.0))
    check = _evaluate(
        tmp_path,
        run_dir,
        f"""
        units: mm
        checks:
          - id: bracket_dfm
            type: manufacturability
            object: obj.bracket
            thickness: {THICKNESS}
            frame: flat
            rules:
              - rule: hole_to_bend
              - rule: hole_to_edge
        """,
    )
    assert check["status"] == "pass", check
    assert _rule_failures(check, "hole_to_bend") == set()
    assert _rule_failures(check, "hole_to_edge") == set()


def test_non_sheet_detection_unchanged(tmp_path):
    """A plain (non-bent) part's detected features are untouched.

    The sheet-metal gate fires only for parts with published bend features, so a
    flat plate keeps its detected cylindrical holes (dedup against explicit
    publications is the ADR 0012 path, unchanged).
    """

    run_dir = _run_design(
        tmp_path,
        """
        from build123d import BuildPart, Box, Cylinder, Locations, Mode
        from cadx import publish


        def build(params):
            with BuildPart() as model:
                Box(40, 20, 4)
                with Locations((-10, 0, 0), (10, 0, 0)):
                    Cylinder(2, 8, mode=Mode.SUBTRACT)
            publish("plate", model.part, role="final")
            return model.part
        """,
    )
    features = _spatial(run_dir)["features"]
    detected_holes = [
        f
        for f in features
        if f.get("kind") == "cylindrical_hole" and f.get("detected")
    ]
    # Both real bores are detected and preserved (no sheet-metal suppression).
    assert len(detected_holes) == 2, features


# --- ADR 0051 secondary: exclude_detected DFM filter (pure-Python) -----------


def test_exclude_detected_filter():
    """exclude_detected:true drops detected features before any rule runs.

    Unit-level, no kernel: a crafted spatial.json with one authored hole (clear of
    the edge) and one DETECTED hole crowding the edge. Without the filter the
    detected hole violates hole_to_edge; with exclude_detected it is removed and
    the check passes. Proves the downstream-requested detected:true exclusion.
    """

    from cadx.dfm import evaluate_manufacturability

    spatial = {
        "objects": [
            {
                "label": "plate",
                "bbox": {"min": [0.0, 0.0, 0.0], "max": [40.0, 20.0, 2.0], "size": [40.0, 20.0, 2.0]},
            }
        ],
        "features": [
            {
                "id": "feat.authored",
                "kind": "cylindrical_hole",
                "diameter": 4.0,
                "center": [20.0, 10.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
                "source_object": "obj.plate",
            },
            {
                # A detected hole hard against the leading edge (x=0.5, r=2) so its
                # edge clearance is negative — a hole_to_edge violation.
                "id": "feat.auto_plate_cylindrical_hole_1",
                "kind": "cylindrical_hole",
                "diameter": 4.0,
                "center": [0.5, 10.0, 0.0],
                "axis": [0.0, 0.0, 1.0],
                "source_object": "obj.plate",
                "detected": True,
            },
        ],
    }
    rule_block = {
        "id": "plate_dfm",
        "type": "manufacturability",
        "thickness": 2.0,
        "rules": [{"rule": "hole_to_edge"}],
    }

    without_filter = evaluate_manufacturability(spatial, dict(rule_block))
    assert without_filter["status"] == "fail"
    assert "feat.auto_plate_cylindrical_hole_1" in _rule_failures(without_filter, "hole_to_edge")

    with_filter = evaluate_manufacturability(spatial, {**rule_block, "exclude_detected": True})
    assert with_filter["status"] == "pass", with_filter
    assert _rule_failures(with_filter, "hole_to_edge") == set()

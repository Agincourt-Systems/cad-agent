"""ADR 0018: laser/sheet manufacturability (DFM) checks.

The ``manufacturability`` requirement type is documented but unimplemented;
``evaluate.py`` raises ``ValueError`` on it today. These tests exercise the new
check purely over ``spatial.json`` built from synthetic dict publications, so
they run without a CAD kernel and are deterministic. Each test asserts the
rule math (offending feature ids, pass/fail status, warn vs fail severity),
not the presence of keys.

The design files publish a plate object as a plain dict (its bbox is the only
geometry the rules need) plus explicit features carrying ``source_object`` so
the evaluator can resolve each feature's owning bbox for edge distance and
thickness. Because dict objects produce no STEP export, automatic detection is
inert and the published features pass straight into ``spatial.json``.
"""

import json
import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def run_cadx(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    """Parse successful cadx JSON output."""

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _write_design(tmp_path: Path, feature_lines: str) -> Path:
    """Write a design publishing a 80x20x4 plate plus the given features.

    ``feature_lines`` is python publishing one or more explicit features. The
    plate bbox is centered on the origin so edge distances are easy to reason
    about: half-width 40 mm in x, half-height 10 mm in y, thickness 4 mm in z.
    """

    design = tmp_path / "design.py"
    design.write_text(
        textwrap.dedent(
            """
            from cadx import publish, publish_feature


            def build(params):
                publish(
                    "plate",
                    {
                        "bbox": {"min": [-40, -10, 0], "max": [40, 10, 4]},
                        "mass_properties": {"volume": 6400},
                        "topology": {"solids": 1, "faces": 6, "edges": 12, "vertices": 8},
                    },
                    role="final",
                )
            __FEATURES__
            """
        ).replace("__FEATURES__", textwrap.indent(textwrap.dedent(feature_lines), "    ")),
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    return design


def _run_model(tmp_path: Path, feature_lines: str) -> Path:
    """Run a synthetic plate design and return its run directory."""

    design = _write_design(tmp_path, feature_lines)
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    assert payload["status"] == "ok", payload
    return tmp_path / payload["artifact_dir"]


def _evaluate(tmp_path: Path, run_dir: Path, requirements_yaml: str) -> dict:
    """Write requirements, evaluate, and return the parsed checks.json check."""

    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(textwrap.dedent(requirements_yaml), encoding="utf-8")
    parse_stdout_json(run_cadx(tmp_path, "evaluate", str(run_dir), "--requirements", str(requirements)))
    checks = json.loads((run_dir / "checks.json").read_text(encoding="utf-8"))
    return checks


def _only_check(checks: dict) -> dict:
    assert len(checks["checks"]) == 1, checks
    return checks["checks"][0]


def _violation_features(check: dict, rule: str) -> set[str]:
    """Collect all feature ids cited by fail-severity violations of a rule."""

    ids: set[str] = set()
    for violation in check.get("violations", []):
        if violation["rule"] == rule:
            ids.update(violation["features"])
    return ids


def test_min_hole_diameter(tmp_path):
    """A hole smaller than thickness fails naming it; a large enough one passes."""

    # Hole diameter 2 mm < thickness 4 mm -> violation.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "tiny_hole",
            kind="cylindrical_hole",
            diameter=2,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
            """,
        )
    )
    assert check["type"] == "manufacturability"
    assert check["status"] == "fail"
    assert "feat.tiny_hole" in _violation_features(check, "min_hole_diameter")
    # Limit defaults to the resolved thickness (4 mm from the bbox min size).
    assert float(check["thickness"]) == 4.0

    # Hole diameter 5 mm >= thickness 4 mm -> passes.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "ok_hole",
            kind="cylindrical_hole",
            diameter=5,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
            """,
        )
    )
    assert check2["status"] == "pass"
    assert _violation_features(check2, "min_hole_diameter") == set()


def test_hole_to_edge(tmp_path):
    """A hole within 1x thickness of an edge fails; moved inward it passes."""

    # Hole radius 2 mm at x=37 -> edge gap = (40-37) - 2 = 1 mm < thickness 4.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "edge_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[37, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.edge_hole" in _violation_features(check, "hole_to_edge")

    # Move the hole to x=0: edge gap = 40 - 2 = 38 mm, comfortably clears 4 mm.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "center_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """,
        )
    )
    assert check2["status"] == "pass"


def test_min_web_names_both_features(tmp_path):
    """Two holes whose edges are too close violate min_web naming both."""

    # Two 4 mm holes (r=2) centered 5 mm apart -> edge gap = 5 - 2 - 2 = 1 mm.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole_a",
            kind="cylindrical_hole",
            diameter=4,
            center=[-2.5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "hole_b",
            kind="cylindrical_hole",
            diameter=4,
            center=[2.5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_web
            """,
        )
    )
    assert check["status"] == "fail"
    assert _violation_features(check, "min_web") == {"feat.hole_a", "feat.hole_b"}

    # Spread to 20 mm apart -> edge gap = 20 - 4 = 16 mm, clears thickness 4.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole_a",
            kind="cylindrical_hole",
            diameter=4,
            center=[-10, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "hole_b",
            kind="cylindrical_hole",
            diameter=4,
            center=[10, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_web
            """,
        )
    )
    assert check2["status"] == "pass"


def test_severity_warn_does_not_fail(tmp_path):
    """A violated rule marked warn surfaces in warnings without failing."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "tiny_hole",
            kind="cylindrical_hole",
            diameter=2,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_hole_diameter
                    severity: warn
            """,
        )
    )
    assert check["status"] == "pass"
    # No fail-severity violations for the rule...
    assert _violation_features(check, "min_hole_diameter") == set()
    # ...but it must be surfaced as a warning naming the feature.
    warned = {feature for w in check.get("warnings", []) if w["rule"] == "min_hole_diameter" for feature in w["features"]}
    assert "feat.tiny_hole" in warned


def test_explicit_thickness_and_factor(tmp_path):
    """Explicit thickness and a per-rule factor drive the resolved limit."""

    # Hole diameter 5 mm. With explicit thickness 4 and factor 1.5 the limit is
    # 6 mm, so the hole fails despite being larger than the geometric thickness.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "hole",
            kind="cylindrical_hole",
            diameter=5,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                thickness: 4
                rules:
                  - rule: min_hole_diameter
                    factor: 1.5
            """,
        )
    )
    assert check["status"] == "fail"
    fail = next(v for v in check["violations"] if v["rule"] == "min_hole_diameter")
    assert float(fail["limit"]) == 6.0
    assert float(fail["observed"]) == 5.0
    assert "feat.hole" in fail["features"]


def test_min_slot_width(tmp_path):
    """A slot narrower than thickness violates min_slot_width."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "narrow_slot",
            kind="slot",
            width=2,
            length=10,
            center=[0, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_slot_width
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.narrow_slot" in _violation_features(check, "min_slot_width")


def test_min_bend_radius(tmp_path):
    """An explicit bend feature with an inside radius below thickness fails."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "sharp_bend",
            kind="bend",
            inside_radius=1,
            center=[0, 0, 2],
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_bend_radius
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.sharp_bend" in _violation_features(check, "min_bend_radius")


def test_hole_to_bend(tmp_path):
    """A hole too close to a bend line violates hole_to_bend naming both."""

    # Hole edge is 5 - 2 = 3 mm from the bend line at x=0; limit = 2 * 4 = 8 mm.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "near_hole",
            kind="cylindrical_hole",
            diameter=4,
            center=[5, 0, 2],
            axis=[0, 0, 1],
            through=True,
            source_object="obj.plate",
        )
        publish_feature(
            "the_bend",
            kind="bend",
            line=[[0, -10], [0, 10]],
            center=[0, 0, 2],
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_bend
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.near_hole" in _violation_features(check, "hole_to_bend")


def test_hole_to_edge_slot_uses_thickness_axis(tmp_path):
    """A slot elongated in-plane (the inspector's axis convention) is measured
    against the real edges, not the thickness face: centered passes, overhang fails."""

    # axis=[1,0,0] is the slot ELONGATION direction, as inspector._slot_features
    # records it. A 20 mm-long slot centered in an 80x20 plate is well clear.
    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "centered_slot",
            kind="slot",
            width=4,
            length=20,
            center=[0, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    rules = """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: hole_to_edge
                    factor: 1.0
            """
    assert _only_check(_evaluate(tmp_path, run_dir, rules))["status"] == "pass"

    # Same slot pushed to x=35 extends to x=50, past the +x edge at 40.
    run_dir2 = _run_model(
        tmp_path,
        """
        publish_feature(
            "overhang_slot",
            kind="slot",
            width=4,
            length=30,
            center=[35, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check2 = _only_check(_evaluate(tmp_path, run_dir2, rules))
    assert check2["status"] == "fail"
    assert "feat.overhang_slot" in _violation_features(check2, "hole_to_edge")


def test_min_web_ignores_unsourced_features(tmp_path):
    """Two features that name no owning object are not paired by min_web."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature("orphan_a", kind="cylindrical_hole", diameter=4, center=[-2.5, 0, 2], axis=[0, 0, 1], through=True)
        publish_feature("orphan_b", kind="cylindrical_hole", diameter=4, center=[2.5, 0, 2], axis=[0, 0, 1], through=True)
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                thickness: 4
                rules:
                  - rule: min_web
            """,
        )
    )
    # Without a shared source_object the two holes must not be treated as a web.
    assert check["status"] == "pass"
    assert _violation_features(check, "min_web") == set()


def test_unknown_rule_and_passing_slot(tmp_path):
    """An unrecognized rule name is skipped; a wide-enough slot passes."""

    run_dir = _run_model(
        tmp_path,
        """
        publish_feature(
            "wide_slot",
            kind="slot",
            width=6,
            length=20,
            center=[0, 0, 2],
            axis=[1, 0, 0],
            through=True,
            source_object="obj.plate",
        )
        """,
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_slot_width
                  - rule: not_a_real_rule
            """,
        )
    )
    # Slot width 6 >= thickness 4 passes, and the unknown rule contributes nothing.
    assert check["status"] == "pass"
    assert check["violations"] == []


# --- ADR 0043: min_flange rule (D-022) ---------------------------------------
#
# min_flange keys off the published kind="bend" features (flat-pattern frame,
# ADR 0033): each carries a 2D ``line`` across the blank width and a midpoint
# ``center``. The developed (flange) axis is x, matching bend_chain's flat
# pattern; the blank runs x in [0, blank_length]. A flange segment is the gap
# between two consecutive boundaries (a blank edge or a bend line); every
# segment, including the interior web of a U-channel, must clear the limit.


def _bend_feature(feature_id: str, bend_x: float) -> str:
    """A synthetic flat-frame bend feature at developed position ``bend_x``.

    Mirrors what publish_sheet_metal emits: a vertical bend line spanning the
    width with a midpoint center, so min_flange reads bend_x from ``center[0]``.
    """

    return textwrap.dedent(
        f"""
        publish_feature(
            "{feature_id}",
            kind="bend",
            inside_radius=3,
            line=[[{bend_x}, -10], [{bend_x}, 10]],
            center=[{bend_x}, 0, 0],
            source_object="obj.plate",
        )
        """
    )


def test_min_flange_outer_flange(tmp_path):
    """A stubby outer flange fails min_flange; lengthening it passes.

    Blank runs x in [0, 100]; a bend at x=5 leaves a 5 mm left flange (0->5),
    below an explicit 10 mm minimum, so it fails naming the bend. Moving the
    bend to x=20 leaves 20 mm and 80 mm flanges, both clearing 10 mm.
    """

    run_dir = _run_model(tmp_path, _bend_feature("stub_bend", 5))
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_flange
                    min: 10
                    blank_length: 100
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.stub_bend" in _violation_features(check, "min_flange")

    run_dir2 = _run_model(tmp_path, _bend_feature("ok_bend", 20))
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_flange
                    min: 10
                    blank_length: 100
            """,
        )
    )
    assert check2["status"] == "pass"
    assert _violation_features(check2, "min_flange") == set()


def test_min_flange_relative_limit(tmp_path):
    """The thickness-relative form resolves the limit from part thickness.

    factor 4.0 on thickness 4 mm -> a 16 mm floor. A bend at x=10 leaves a
    10 mm flange (< 16) and fails; at x=20 both flanges clear 16 mm.
    """

    run_dir = _run_model(tmp_path, _bend_feature("near_bend", 10))
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                thickness: 4
                rules:
                  - rule: min_flange
                    factor: 4.0
                    blank_length: 100
            """,
        )
    )
    assert check["status"] == "fail"
    fail = next(v for v in check["violations"] if v["rule"] == "min_flange")
    assert float(fail["limit"]) == 16.0
    assert float(fail["observed"]) == 10.0

    run_dir2 = _run_model(tmp_path, _bend_feature("far_bend", 20))
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                thickness: 4
                rules:
                  - rule: min_flange
                    factor: 4.0
                    blank_length: 100
            """,
        )
    )
    assert check2["status"] == "pass"


def test_min_flange_interior_web(tmp_path):
    """A U-channel's short interior web fails min_flange citing BOTH bends.

    Two bends at x=30 and x=40 over a 100 mm blank give segments 30 (outer),
    10 (web), 60 (outer). With a 15 mm minimum only the 10 mm web fails, and
    the violation names both bends bounding it. Spreading the bends to x=30 and
    x=60 lengthens the web to 30 mm and the whole part passes.
    """

    run_dir = _run_model(
        tmp_path, _bend_feature("web_bend_a", 30) + _bend_feature("web_bend_b", 40)
    )
    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_flange
                    min: 15
                    blank_length: 100
            """,
        )
    )
    assert check["status"] == "fail"
    # The single failing segment (the web) cites both bounding bends.
    assert _violation_features(check, "min_flange") == {"feat.web_bend_a", "feat.web_bend_b"}

    run_dir2 = _run_model(
        tmp_path, _bend_feature("web_bend_a", 30) + _bend_feature("web_bend_b", 60)
    )
    check2 = _only_check(
        _evaluate(
            tmp_path,
            run_dir2,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                rules:
                  - rule: min_flange
                    min: 15
                    blank_length: 100
            """,
        )
    )
    assert check2["status"] == "pass"


def test_min_flange_real_bend_flow(tmp_path):
    """min_flange fires on a real publish_sheet_metal part with a stubby flange.

    A 5 mm / 60 mm single-bend bracket: the bend line sits at flange_a + BA/2
    (~8.1 mm) in the developed frame, under a 15 mm minimum, so the short leg
    fails naming the emitted feat.bracket_bend_0. blank_length is the part's
    developed length (computed here from the bend-allowance formula); thickness
    is passed explicitly per ADR 0033 (a folded bbox min is a flange, not gauge).
    """

    pytest.importorskip("build123d")
    thickness, radius, k_factor, angle = 2.29, 3.0, 0.44, 90.0
    flange_a, flange_b = 5.0, 60.0
    bend_allowance = (math.pi / 180.0) * angle * (radius + k_factor * thickness)
    developed_length = flange_a + flange_b + bend_allowance

    design = tmp_path / "design.py"
    design.write_text(
        textwrap.dedent(
            f"""
            from cadx import publish_sheet_metal
            from cadx.sheetmetal import bend


            def build(params):
                part = bend(
                    {flange_a},
                    {flange_b},
                    angle_deg={angle},
                    inside_radius={radius},
                    k_factor={k_factor},
                    thickness={thickness},
                    width=30,
                )
                publish_sheet_metal("bracket", part, role="final")
                return part.folded
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "params.yaml").write_text("{}\n", encoding="utf-8")
    payload = parse_stdout_json(run_cadx(tmp_path, "run", str(design), "--params", "params.yaml"))
    run_dir = tmp_path / payload["artifact_dir"]

    check = _only_check(
        _evaluate(
            tmp_path,
            run_dir,
            f"""
            units: mm
            checks:
              - id: bracket_dfm
                type: manufacturability
                object: obj.bracket
                thickness: {thickness}
                rules:
                  - rule: min_flange
                    min: 15
                    blank_length: {developed_length}
            """,
        )
    )
    assert check["status"] == "fail"
    assert "feat.bracket_bend_0" in _violation_features(check, "min_flange")


# --- ADR 0043: bend-radius default policy (D-021) ----------------------------


def test_min_bend_radius_explicit_sub_t_min(tmp_path):
    """An explicit sub-thickness ``min`` admits a fab house's verified radius.

    The 1.0 t default floor rejects SendCutSend's verified 0.81 mm inside radius
    on 2.29 mm stock. A design working to that published radius table opts in
    with ``min: 0.81``: a 0.81 mm bend then PASSES, while a 0.5 mm bend (below
    the explicit min) still FAILS. Without the explicit min, the conservative
    default would reject even the verified 0.81 mm radius.
    """

    def bend_radius_feature(radius: float) -> str:
        return textwrap.dedent(
            f"""
            publish_feature(
                "the_bend",
                kind="bend",
                inside_radius={radius},
                center=[0, 0, 2],
                source_object="obj.plate",
            )
            """
        )

    explicit_min_rules = """
        units: mm
        checks:
          - id: plate_dfm
            type: manufacturability
            object: obj.plate
            thickness: 2.29
            rules:
              - rule: min_bend_radius
                min: 0.81
        """

    # Verified 0.81 mm radius passes under the explicit 0.81 mm min.
    run_dir = _run_model(tmp_path, bend_radius_feature(0.81))
    check = _only_check(_evaluate(tmp_path, run_dir, explicit_min_rules))
    assert check["status"] == "pass"
    assert _violation_features(check, "min_bend_radius") == set()

    # A 0.5 mm radius, below the explicit min, still fails.
    run_dir2 = _run_model(tmp_path, bend_radius_feature(0.5))
    check2 = _only_check(_evaluate(tmp_path, run_dir2, explicit_min_rules))
    assert check2["status"] == "fail"
    assert "feat.the_bend" in _violation_features(check2, "min_bend_radius")

    # Documented motivation: without the explicit min, the conservative 1.0 t
    # default floor (2.29 mm here) rejects even the verified 0.81 mm radius.
    run_dir3 = _run_model(tmp_path, bend_radius_feature(0.81))
    check3 = _only_check(
        _evaluate(
            tmp_path,
            run_dir3,
            """
            units: mm
            checks:
              - id: plate_dfm
                type: manufacturability
                object: obj.plate
                thickness: 2.29
                rules:
                  - rule: min_bend_radius
            """,
        )
    )
    assert check3["status"] == "fail"
